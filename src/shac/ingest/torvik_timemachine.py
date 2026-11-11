"""Backfill Bart Torvik's Time Machine: one team-ratings file per in-season day (task 1.3).

Run (from Owen's PC; barttorvik.com blocks GitHub's servers):
    uv run python -m shac.ingest.torvik_timemachine --data-dir <private repo> --seasons 2019
Add --max-files N to stop after N files (handy for a first test).

What it fetches, for each season (ending year):
- The Time Machine ratings file for every day from the day before the season's first game
  through its last game: https://barttorvik.com/timemachine/team_results/YYYYMMDD_team_results.json.gz
  Game dates come from the CBBD games table (tables/games.parquet), in US Eastern time.
- That season's schedule file, YYYY_super_sked.csv, which holds Torvik's game predictions.

A season still in progress works too: days stop at the latest file that is surely final
(`last_final_day`, yesterday's once it's past 06:00 Eastern), so no future day is ever
asked for (a 404 is recorded as "missing" and never asked again). Its schedule file is
skipped while the season runs; the daily Torvik job saves a fresh copy every day.
Re-running later picks up the days that have passed since.

Bart Torvik OK'd this on 2026-11-10 as long as it goes slowly (see docs/DECISIONS.md), so:
- Requests are at least 20 seconds apart, plus a random 0 to 10 seconds.
- The run STOPS at the first block page, 403, 429 or repeated error. Run it again later;
  it picks up where it left off.
- Every result is written to a manifest in the data repo. A file already saved (or a day the
  site says doesn't exist, HTTP 404) is never requested again.

Raw files go to the private data repo only. Printed output is counts and statuses only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import httpx

from shac.snapshots.common import (
    Clock,
    append_log_row,
    file_stamp,
    gzip_bytes,
    iso_z,
    read_log_rows,
    sha256_hex,
    utc_now,
    write_new_file,
)
from shac.snapshots.torvik import fetch_with_one_retry, looks_blocked

BASE_URL = "https://barttorvik.com"
DEFAULT_SEASONS = list(range(2019, 2027))  # 2018-19 through 2025-26
MIN_GAP_SECONDS = 20
EXTRA_GAP_SECONDS = 10  # a random 0 to 10 more, so requests don't land on a fixed beat
# A real ratings file has a row per D-I team (350+); a real schedule has thousands of games.
MIN_TEAMS = 300
MIN_SKED_LINES = 1000
GAMES_TABLE = Path("tables") / "games.parquet"
TIMEMACHINE_DIR = Path("torvik") / "timemachine"
SEASONS_DIR = Path("torvik") / "raw" / "seasons"
MANIFEST = TIMEMACHINE_DIR / "manifest.csv"

# status: ok | missing (404, the site has no file for that day) | blocked | error
MANIFEST_COLUMNS = [
    "fetched_at_utc",
    "season",
    "source",
    "status",
    "http_status",
    "n_rows",
    "file",
    "sha256",
    "bytes",
]
# Rows with these statuses are final: the source is never requested again.
DONE_STATUSES = {"ok", "missing"}


def season_dates(first_game: date, last_game: date) -> list[date]:
    """Every day from the day before the first game through the last game.

    The day before is included because a game on date D uses the ratings file from D-1.
    """
    start = first_game - timedelta(days=1)
    return [start + timedelta(days=i) for i in range((last_game - start).days + 1)]


def season_game_ranges(games_path: Path) -> dict[int, tuple[date, date]]:
    """First and last game date (US Eastern) for each season in the CBBD games table."""
    query = """
        select season,
               min(cast(timezone('America/New_York', tip_at) as date)) as first_game,
               max(cast(timezone('America/New_York', tip_at) as date)) as last_game
        from read_parquet(?)
        group by season
        order by season
    """
    rows = duckdb.execute(query, [str(games_path)]).fetchall()
    return {int(season): (first, last) for season, first, last in rows}


def last_final_day(now: datetime) -> date:
    """The newest Time Machine file that is surely frozen: the file dated D is known at
    06:00 US Eastern on D+1 (docs/TORVIK.md), so before 06:00 it's the day before that."""
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    return (eastern - timedelta(hours=6)).date() - timedelta(days=1)


def timemachine_source(day: date) -> str:
    """The site path of one day's Time Machine file."""
    return f"timemachine/team_results/{day:%Y%m%d}_team_results.json.gz"


def plan_sources(
    ranges: dict[int, tuple[date, date]], seasons: list[int], last_day: date
) -> list[tuple[int, str]]:
    """Every (season, source path) to fetch: the schedule file, then each day in order.

    Days after `last_day` are left out. A season whose last game is after `last_day` is
    still in progress, so its schedule file is left out too.
    """
    sources = []
    for season in seasons:
        if season not in ranges:
            raise ValueError(f"Season {season} is not in the games table.")
        first, last = ranges[season]
        if last <= last_day:
            sources.append((season, f"{season}_super_sked.csv"))
        days = [d for d in season_dates(first, last) if d <= last_day]
        sources.extend((season, timemachine_source(day)) for day in days)
    return sources


def check_ratings(body: bytes) -> tuple[bytes, int]:
    """Check a Time Machine file and return (gzipped bytes to store, number of teams).

    The site may send the file still gzipped or already unzipped, so both are handled.
    Raises ValueError if it isn't a JSON list with a row per team.
    """
    raw = gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) < MIN_TEAMS:
        raise ValueError("not a full ratings file")
    blob = body if body[:2] == b"\x1f\x8b" else gzip_bytes(body)
    return blob, len(rows)


def check_schedule(body: bytes) -> tuple[bytes, int]:
    """Check a super_sked file and return (gzipped bytes to store, number of lines)."""
    n_lines = len(body.splitlines())
    if n_lines < MIN_SKED_LINES:
        raise ValueError("schedule file too short")
    return gzip_bytes(body), n_lines


def save_path(season: int, source: str, stamp: str) -> Path:
    """Where a source is stored in the data repo."""
    if source.startswith("timemachine/"):
        return TIMEMACHINE_DIR / str(season) / Path(source).name
    # Same folder and naming as the per-season files already saved there.
    return SEASONS_DIR / f"{Path(source).stem}_{stamp}.csv.gz"


def fetch_one(
    client: httpx.Client,
    data_dir: Path,
    season: int,
    source: str,
    now: Clock,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    """Fetch and save one source. Returns its manifest row (status says what happened)."""
    response = fetch_with_one_retry(client, f"{BASE_URL}/{source}", sleep)
    fetched_at = now()
    row: dict[str, object] = {
        "fetched_at_utc": iso_z(fetched_at),
        "season": season,
        "source": source,
    }
    if response is None:
        row["status"] = "error"
        return row
    row["http_status"] = response.status_code
    body = response.content
    # A browser-check page often comes back as HTTP 403, so check for it first.
    if looks_blocked(body) or response.status_code in (403, 429):
        row["status"] = "blocked"
        return row
    if response.status_code == 404:
        row["status"] = "missing"
        return row
    if response.status_code != 200:
        row["status"] = "error"
        return row
    try:
        if source.startswith("timemachine/"):
            blob, n_rows = check_ratings(body)
        else:
            blob, n_rows = check_schedule(body)
    except (ValueError, OSError):  # bad JSON, a short file, or a broken gzip
        row["status"] = "error"
        return row
    relative = save_path(season, source, file_stamp(fetched_at))
    if (data_dir / relative).exists():
        # Saved by a run that stopped before writing its manifest row. Keep the first copy.
        blob = (data_dir / relative).read_bytes()
    else:
        write_new_file(data_dir / relative, blob)
    row.update(
        status="ok",
        n_rows=n_rows,
        file=relative.as_posix(),
        sha256=sha256_hex(blob),
        bytes=len(blob),
    )
    return row


def run(
    client: httpx.Client,
    data_dir: Path,
    seasons: list[int],
    max_files: int | None = None,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    gap: Callable[[], float] = lambda: MIN_GAP_SECONDS + random.uniform(0, EXTRA_GAP_SECONDS),
) -> bool:
    """Fetch every source not already in the manifest. Returns False if the run had to stop."""
    ranges = season_game_ranges(data_dir / GAMES_TABLE)
    manifest_path = data_dir / MANIFEST
    done = {r["source"] for r in read_log_rows(manifest_path) if r["status"] in DONE_STATUSES}
    planned = plan_sources(ranges, seasons, last_final_day(now()))
    todo = [(s, src) for s, src in planned if src not in done]
    if max_files is not None:
        todo = todo[:max_files]
    print(f"{len(todo)} files to fetch ({len(done)} already in the manifest).")

    counts = {"ok": 0, "missing": 0}
    for index, (season, source) in enumerate(todo):
        if index > 0:
            sleep(gap())
        row = fetch_one(client, data_dir, season, source, now, sleep)
        append_log_row(manifest_path, MANIFEST_COLUMNS, row)
        status = str(row["status"])
        if status not in DONE_STATUSES:
            print(f"Stopped at {source}: {status} (HTTP {row.get('http_status', '-')}).")
            print(f"Saved {counts['ok']}, missing {counts['missing']}. Re-run later to resume.")
            return False
        counts[status] += 1
        if (index + 1) % 25 == 0:
            print(f"{index + 1} of {len(todo)} done.")
    print(f"Finished. Saved {counts['ok']}, missing {counts['missing']}.")
    return True


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 1 if the run stopped early."""
    parser = argparse.ArgumentParser(description="Backfill Torvik Time Machine files.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--seasons", type=int, nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--max-files", type=int, default=None)
    args = parser.parse_args(argv)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        ok = run(client, args.data_dir, args.seasons, args.max_files)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
