"""Pull The Odds API's historical college basketball spreads (docs/EARLY_LINE.md, section 4).

The plan, fixed in EARLY_LINE.md before any pull: for every US Eastern game day D with a
final D-I game in seasons 2025-26, 2024-25, 2023-24 and 2022-23 (newest first), three
requests at Eastern clock times:

    evening    22:20 on D-1   the call line
    afternoon  13:55 on D     close for afternoon tips
    late       18:55 on D     close for evening tips

Each request asks for the archive's snapshot at or before that time: regions `us`, markets
`spreads`, 10 credits. Needs a paid plan and ODDS_API_KEY.

Run as (from the repo):
    uv run python -m shac.ingest.odds_history --data-dir <private repo> --plan
        counts and credits only, no request
    uv run python -m shac.ingest.odds_history --data-dir <private repo> --max-calls 1
        one real request, to check the format and the credit count
    uv run python -m shac.ingest.odds_history --data-dir <private repo>
        the whole plan; safe to stop and run again (it skips what is already saved)

It stops when the next call would leave fewer than --reserve credits (default 1,000, for
the live snapshot job), on any 401/403/422/429, or after 3 errors in a row. Raw files go to
odds/history/<season>/<game day>/ in the private repo, wrapped like live snapshot files
(`fetched_at` = the archive's snapshot time), and every request gets a manifest row. The
API key is never written or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import httpx
import pandas as pd

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
from shac.snapshots.odds import FetchError, count_books

URL = "https://api.the-odds-api.com/v4/historical/sports/basketball_ncaab/odds"
REQUEST_INFO = {
    "sport": "basketball_ncaab",
    "regions": "us",
    "markets": "spreads",
    "oddsFormat": "american",
    "dateFormat": "iso",
}
SEASONS = [2026, 2025, 2024, 2023]
EASTERN = ZoneInfo("America/New_York")
# (kind, days from the game day, Eastern clock time)
KINDS = [
    ("evening", -1, clock_time(22, 20)),
    ("afternoon", 0, clock_time(13, 55)),
    ("late", 0, clock_time(18, 55)),
]
CREDITS_PER_CALL = 10
RESERVE = 1000
MAX_ERRORS_IN_A_ROW = 3
STOP_STATUSES = {401, 403, 422, 429}
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [5, 20]
PAUSE_SECONDS = 1.0
FOLDER = "odds/history"
MANIFEST = f"{FOLDER}/manifest.csv"
MANIFEST_COLUMNS = [
    "requested_at_utc",
    "kind",
    "season",
    "game_day",
    "status",
    "http_status",
    "snapshot_at_utc",
    "previous_at_utc",
    "next_at_utc",
    "n_games",
    "n_books",
    "credits_used",
    "credits_remaining",
    "file",
    "sha256",
    "bytes",
    "pulled_at_utc",
]


def plan(games: pd.DataFrame, seasons: list[int] = SEASONS) -> pd.DataFrame:
    """Every request in pull order: newest season first, then by time.

    `games` needs season, tip_at (UTC) and status. A game day is the US Eastern date of a
    final game's tip.
    """
    g = games[games["season"].isin(seasons) & (games["status"] == "final")]
    days = pd.DataFrame(
        {
            "season": g["season"].astype(int).to_numpy(),
            "game_day": pd.to_datetime(g["tip_at"], utc=True).dt.tz_convert(EASTERN).dt.date,
        }
    ).drop_duplicates()
    rows = []
    for season, game_day in days.itertuples(index=False):
        for kind, offset, at in KINDS:
            local = datetime.combine(game_day + timedelta(days=offset), at, tzinfo=EASTERN)
            rows.append(
                {
                    "season": int(season),
                    "game_day": game_day,
                    "kind": kind,
                    "requested_at": local.astimezone(UTC),
                }
            )
    out = pd.DataFrame(rows, columns=["season", "game_day", "kind", "requested_at"])
    return out.sort_values(["season", "requested_at"], ascending=[False, True]).reset_index(
        drop=True
    )


def plan_summary(planned: pd.DataFrame) -> pd.DataFrame:
    """Calls and credits per season, with running totals."""
    out = planned.groupby("season", sort=False).agg(
        game_days=("game_day", "nunique"), calls=("kind", "size")
    )
    out["credits"] = out["calls"] * CREDITS_PER_CALL
    out["running_credits"] = out["credits"].cumsum()
    return out.reset_index()


def _int_header(response: httpx.Response, name: str) -> int | None:
    try:
        return int(float(response.headers[name]))
    except (KeyError, ValueError):
        return None


def fetch_snapshot(
    client: httpx.Client,
    api_key: str,
    requested_at: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """One historical request, retrying only on network errors and 5xx."""
    params = {
        "apiKey": api_key,
        "regions": REQUEST_INFO["regions"],
        "markets": REQUEST_INFO["markets"],
        "oddsFormat": REQUEST_INFO["oddsFormat"],
        "dateFormat": REQUEST_INFO["dateFormat"],
        "date": iso_z(requested_at),
    }
    last_error = FetchError("no attempt made")
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            sleep(BACKOFF_SECONDS[attempt - 1])
        try:
            response = client.get(URL, params=params)
        except httpx.HTTPError as exc:
            # The error text can hold the URL, and so the key: keep the class name only.
            last_error = FetchError(f"network error: {type(exc).__name__}")
            continue
        if response.status_code == 200:
            return response
        if response.status_code < 500:
            raise FetchError(f"HTTP {response.status_code}", response.status_code)
        last_error = FetchError(f"HTTP {response.status_code}", response.status_code)
    raise last_error


def relative_path(season: int, game_day: date, kind: str, requested_at: datetime) -> str:
    stamp = file_stamp(requested_at)
    return f"{FOLDER}/{season}/{game_day.isoformat()}/odds_hist_{stamp}_{kind}.json.gz"


def run(
    client: httpx.Client,
    api_key: str,
    data_dir: Path,
    planned: pd.DataFrame,
    max_calls: int | None = None,
    reserve: int = RESERVE,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    pause: float = PAUSE_SECONDS,
) -> dict:
    """Work through the plan. Returns counts and why it stopped."""
    manifest = data_dir / MANIFEST
    done = {r["requested_at_utc"] for r in read_log_rows(manifest) if r.get("status") == "ok"}
    out = {"calls": 0, "ok": 0, "errors": 0, "skipped_done": 0, "stopped": "plan_finished"}
    remaining: int | None = None
    errors_in_a_row = 0
    for req in planned.itertuples(index=False):
        key = iso_z(req.requested_at)
        relative = relative_path(req.season, req.game_day, req.kind, req.requested_at)
        if key in done or (data_dir / relative).exists():
            out["skipped_done"] += 1
            continue
        if max_calls is not None and out["calls"] >= max_calls:
            out["stopped"] = "max_calls"
            break
        if remaining is not None and remaining - CREDITS_PER_CALL < reserve:
            out["stopped"] = "reserve"
            break
        if out["calls"] > 0 and pause:
            sleep(pause)
        row: dict[str, object] = {
            "requested_at_utc": key,
            "kind": req.kind,
            "season": req.season,
            "game_day": req.game_day.isoformat(),
        }
        out["calls"] += 1
        try:
            response = fetch_snapshot(client, api_key, req.requested_at, sleep)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise FetchError("unexpected response shape", 200)
            if not body.get("timestamp"):
                raise FetchError("response has no snapshot timestamp", 200)
            credits = {
                "remaining": _int_header(response, "x-requests-remaining"),
                "used": _int_header(response, "x-requests-used"),
                "last": _int_header(response, "x-requests-last"),
            }
            pulled_at = iso_z(now())
            payload = {
                # `fetched_at` is the archive's snapshot time, so the live table code
                # (shac.tables.odds.flatten_snapshots) reads these files unchanged.
                "fetched_at": body["timestamp"],
                "pulled_at": pulled_at,
                "requested_at": key,
                "kind": req.kind,
                "season": req.season,
                "game_day": req.game_day.isoformat(),
                "source": "the-odds-api historical",
                "request": REQUEST_INFO,
                "credits": credits,
                "previous_timestamp": body.get("previous_timestamp"),
                "next_timestamp": body.get("next_timestamp"),
                "data": body["data"],
            }
            blob = gzip_bytes(json.dumps(payload).encode("utf-8"))
            write_new_file(data_dir / relative, blob)
        except (FetchError, ValueError, OSError) as exc:
            status = getattr(exc, "http_status", None)
            row.update(status="error", http_status=status, pulled_at_utc=iso_z(now()))
            append_log_row(manifest, MANIFEST_COLUMNS, row)
            out["errors"] += 1
            errors_in_a_row += 1
            if status in STOP_STATUSES:
                out["stopped"] = f"http_{status}"
                break
            if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                out["stopped"] = "errors_in_a_row"
                break
            continue
        errors_in_a_row = 0
        remaining = credits["remaining"]
        row.update(
            status="ok",
            http_status=200,
            snapshot_at_utc=body["timestamp"],
            previous_at_utc=body.get("previous_timestamp"),
            next_at_utc=body.get("next_timestamp"),
            n_games=len(body["data"]),
            n_books=count_books(body["data"]),
            credits_used=credits["used"],
            credits_remaining=credits["remaining"],
            file=relative,
            sha256=sha256_hex(blob),
            bytes=len(blob),
            pulled_at_utc=pulled_at,
        )
        append_log_row(manifest, MANIFEST_COLUMNS, row)
        out["ok"] += 1
    out["credits_remaining"] = remaining
    return out


def load_games(data_dir: Path) -> pd.DataFrame:
    from shac.tables.build import query_df

    path = (data_dir / "tables" / "games.parquet").as_posix().replace("'", "''")
    con = duckdb.connect()
    games = query_df(con, f"SELECT season, tip_at, status FROM read_parquet('{path}')")
    con.close()
    return games


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--plan", action="store_true", help="print counts only; no request")
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--reserve", type=int, default=RESERVE)
    args = parser.parse_args(argv)

    if not (args.data_dir / "tables" / "games.parquet").exists():
        print("missing tables/games.parquet (run shac.tables.build first)", file=sys.stderr)
        return 1
    planned = plan(load_games(args.data_dir))
    summary = plan_summary(planned)
    print(summary.to_string(index=False))
    print(f"total: {len(planned)} calls, {len(planned) * CREDITS_PER_CALL} credits")
    if args.plan:
        return 0

    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        print("error: ODDS_API_KEY is not set", file=sys.stderr)
        return 1
    with httpx.Client(timeout=60) as client:
        result = run(client, api_key, args.data_dir, planned, args.max_calls, args.reserve)
    print(
        f"calls {result['calls']}, ok {result['ok']}, errors {result['errors']}, "
        f"already saved {result['skipped_done']}, stopped: {result['stopped']}, "
        f"credits remaining: {result['credits_remaining']}"
    )
    return 0 if result["stopped"] in ("plan_finished", "max_calls", "reserve") else 1


if __name__ == "__main__":
    sys.exit(main())
