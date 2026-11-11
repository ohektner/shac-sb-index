"""Collect Google News headlines about availability for teams playing soon (task 1.11).

Run as:
    uv run python -m shac.news.google_news --data-dir <private repo> --log <status csv>

What it does, in order:
1. Finds the D-I teams with a game in the next LOOKAHEAD_HOURS, from the raw CBBD games
   files in the private repo (the daily `cbbd-ingest` run keeps those current).
2. For each team, one Google News RSS search: the team's name plus availability words,
   limited to the last 2 days. Requests are at least GAP_SECONDS apart.
3. Saves every response, raw XML plus the parsed items (title, source, link, publish
   time), in ONE gzipped JSON file in the private repo. Nothing is fetched beyond the
   feed itself: no article pages.
4. Adds one row to the public status log: counts and a file hash only. Headlines never
   reach the log or the printed output.

Terms: the feed says it is for personal, non-commercial use in a feed reader. Owen
authorized this use on 2026-11-11 for this personal, non-commercial project (DECISIONS.md):
text stays private, only short derived facts (player, status) are ever published, and the
request rate is kept low. If Google answers with an error page or 429, the run stops.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import httpx
import pandas as pd

from shac.snapshots.common import (
    Clock,
    append_log_row,
    dated_folder,
    file_stamp,
    gzip_bytes,
    iso_z,
    sha256_hex,
    utc_now,
    write_new_file,
)
from shac.tables import raw

FEED_URL = "https://news.google.com/rss/search"
USER_AGENT = "shac-sb-index/0.1 (+https://github.com/ohektner/shac-sb-index)"
LOOKAHEAD_HOURS = 36
GAP_SECONDS = 5.0
EXTRA_GAP_SECONDS = 3.0  # plus a random 0 to 3, so requests don't land on a fixed beat
MAX_QUERIES = 250  # safety stop
NEWS_DIR = Path("news") / "raw" / "google_news"
# Availability words. Google News search supports OR, quotes and `when:` (recent days).
TERMS = (
    'injury OR injured OR questionable OR doubtful OR "game-time decision" OR "will miss" '
    'OR "out for" OR "ruled out" OR suspended OR "day-to-day" OR availability'
)
WINDOW = "when:2d"
LOG_COLUMNS = [
    "fetched_at_utc",
    "status",
    "http_status",
    "n_teams",
    "n_queries",
    "n_items",
    "n_new_items",
    "file",
    "sha256",
    "bytes",
]


@dataclass
class QueryResult:
    team_id: int
    query: str
    http_status: int | None
    xml: str
    items: list[dict] = field(default_factory=list)


class Blocked(Exception):
    """Google refused us (429, an error page, or not a feed). The run stops."""


def build_query(team_name: str) -> str:
    """The search text for one team: its name in quotes, 'basketball', the words."""
    return f'"{team_name}" basketball ({TERMS}) {WINDOW}'


def feed_url(query: str) -> str:
    return f"{FEED_URL}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def teams_playing_soon(
    data_dir: Path, teams: pd.DataFrame, now: datetime, hours: int = LOOKAHEAD_HOURS
) -> list[tuple[int, str]]:
    """(team_id, name) for every D-I team with a game tipping in the next `hours`."""
    deduped = raw.dedupe_files(raw.load_endpoint(data_dir, "games"), key=lambda g: g["id"])
    names = dict(zip(teams["team_id"].astype(int), teams["name"], strict=True))
    end = now + timedelta(hours=hours)
    ids: set[int] = set()
    for _fetched, game in deduped.records:
        start = game.get("startDate")
        if not start or str(game.get("status", "")).lower() in ("final", "cancelled"):
            continue
        tip = pd.Timestamp(start).tz_convert("UTC").to_pydatetime()
        if now <= tip < end:
            for key in ("homeTeamId", "awayTeamId"):
                if game.get(key) is not None and int(game[key]) in names:
                    ids.add(int(game[key]))
    return [(tid, names[tid]) for tid in sorted(ids)]


def parse_items(xml_text: str) -> list[dict]:
    """The items of one RSS response: title, source name, link, guid, published time.

    Raises Blocked if the text isn't an RSS feed (Google's error and consent pages are
    HTML).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise Blocked("response is not XML") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise Blocked("response is not an RSS feed")
    items = []
    for item in root.findall("./channel/item"):
        source = item.find("source")
        published = item.findtext("pubDate")
        try:
            published_at = iso_z(parsedate_to_datetime(published)) if published else None
        except (TypeError, ValueError):
            published_at = None
        items.append(
            {
                "guid": item.findtext("guid") or item.findtext("link"),
                "title": item.findtext("title"),
                "source_name": source.text if source is not None else None,
                "source_url": source.get("url") if source is not None else None,
                "url": item.findtext("link"),
                "published_at": published_at,
            }
        )
    return items


def fetch_query(client: httpx.Client, team_id: int, query: str) -> QueryResult:
    """One feed request. Raises Blocked on 429, 403, 5xx or a non-feed answer."""
    response = client.get(feed_url(query))
    if response.status_code != 200:
        raise Blocked(f"HTTP {response.status_code}")
    items = parse_items(response.text)
    return QueryResult(team_id, query, 200, response.text, items)


def recent_guids(data_dir: Path, now: datetime, days: int = 7) -> set[str]:
    """Item guids saved in the last `days` days, to count how many items are new."""
    seen: set[str] = set()
    folder = data_dir / NEWS_DIR
    if not folder.exists():
        return seen
    for back in range(days + 1):
        day = folder / dated_folder(now - timedelta(days=back))
        for path in sorted(day.glob("*.json.gz")):
            wrapped = raw.read_wrapped(path)
            for result in wrapped.get("data", []):
                seen.update(i["guid"] for i in result.get("items", []) if i.get("guid"))
    return seen


def run(
    client: httpx.Client,
    data_dir: Path,
    teams: pd.DataFrame,
    log_path: Path,
    *,
    max_queries: int = MAX_QUERIES,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    gap: Callable[[], float] = lambda: GAP_SECONDS + random.uniform(0, EXTRA_GAP_SECONDS),
) -> bool:
    """Collect, save one file, log one row. Returns False if the run was blocked."""
    started = now()
    playing = teams_playing_soon(data_dir, teams, started)[:max_queries]
    seen = recent_guids(data_dir, started)
    results: list[QueryResult] = []
    status, http_status = "ok", 200
    for index, (team_id, name) in enumerate(playing):
        if index > 0:
            sleep(gap())
        try:
            results.append(fetch_query(client, team_id, build_query(name)))
        except Blocked as exc:
            status = "blocked"
            http_status = str(exc).removeprefix("HTTP ") if str(exc).startswith("HTTP") else ""
            print(f"Stopped after {index} of {len(playing)} queries: {exc}")
            break
        except httpx.HTTPError as exc:
            status, http_status = "error", ""
            print(f"Stopped after {index} of {len(playing)} queries: {type(exc).__name__}")
            break

    n_items = sum(len(r.items) for r in results)
    guids = {i["guid"] for r in results for i in r.items if i.get("guid")}
    row: dict[str, object] = {
        "fetched_at_utc": iso_z(started),
        "status": status,
        "http_status": http_status,
        "n_teams": len(playing),
        "n_queries": len(results),
        "n_items": n_items,
        "n_new_items": len(guids - seen),
    }
    if results:
        payload = {
            "fetched_at": iso_z(started),
            "source": "google_news_rss",
            "lookahead_hours": LOOKAHEAD_HOURS,
            "data": [
                {
                    "team_id": r.team_id,
                    "query": r.query,
                    "http_status": r.http_status,
                    "items": r.items,
                    "xml": r.xml,
                }
                for r in results
            ],
        }
        blob = gzip_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        relative = NEWS_DIR / dated_folder(started) / f"google_news_{file_stamp(started)}.json.gz"
        write_new_file(data_dir / relative, blob)
        row.update(file=relative.as_posix(), sha256=sha256_hex(blob), bytes=len(blob))
    append_log_row(log_path, LOG_COLUMNS, row)
    print(
        f"{status}: {len(results)} of {len(playing)} teams queried, {n_items} items "
        f"({row['n_new_items']} new)."
    )
    return status == "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path, help="private data repo")
    parser.add_argument("--log", required=True, type=Path, help="public status log CSV")
    parser.add_argument("--map-dir", type=Path, default=Path("data"))
    parser.add_argument("--max-queries", type=int, default=MAX_QUERIES)
    args = parser.parse_args(argv)
    teams = pd.read_csv(args.map_dir / "teams.csv")
    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        ok = run(client, args.data_dir, teams, args.log, max_queries=args.max_queries)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
