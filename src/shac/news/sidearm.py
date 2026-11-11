"""Read team athletics-site news feeds for teams playing soon (task 1.11, secondary source).

Run as:
    uv run python -m shac.news.sidearm --data-dir <private repo> --log <status csv>

Once a day, for each D-I team with a game in the next 36 hours and a feed in
`data/team_sites.csv` (found by `shac.news.team_sites`, which checked robots.txt), it reads
that one feed: one request per site, at least GAP_SECONDS between any two requests.
Saves the raw XML and the parsed items (title, link, summary, publish time) in one file
in the private repo, and a counts-only row in the public status log. A 403 or 429 from a
site skips that site; three in one run stop the run.

Owen chose SIDEARM team sites as the slow secondary news source on 2026-11-11
(DECISIONS.md): personal, non-commercial use, text kept private, only short facts
published. Re-run `shac.news.team_sites` about monthly so robots.txt is re-checked.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import pandas as pd

from shac.news.google_news import USER_AGENT, teams_playing_soon
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

NEWS_DIR = Path("news") / "raw" / "sidearm"
GAP_SECONDS = 10.0
EXTRA_GAP_SECONDS = 5.0
MAX_REFUSALS = 3
LOG_COLUMNS = [
    "fetched_at_utc",
    "status",
    "n_teams",
    "n_feeds_read",
    "n_refused",
    "n_errors",
    "n_items",
    "file",
    "sha256",
    "bytes",
]


def parse_feed(text: str) -> list[dict] | None:
    """Items of an RSS feed, or None if `text` isn't one."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.tag != "rss" or root.find("channel") is None:
        return None
    items = []
    for item in root.findall("./channel/item"):
        published = item.findtext("pubDate")
        try:
            published_at = iso_z(parsedate_to_datetime(published)) if published else None
        except (TypeError, ValueError):
            published_at = None
        items.append(
            {
                "guid": item.findtext("guid") or item.findtext("link"),
                "title": item.findtext("title"),
                "url": item.findtext("link"),
                "summary": item.findtext("description"),
                "published_at": published_at,
            }
        )
    return items


def run(
    client: httpx.Client,
    data_dir: Path,
    teams: pd.DataFrame,
    sites: pd.DataFrame,
    log_path: Path,
    *,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    gap: Callable[[], float] = lambda: GAP_SECONDS + random.uniform(0, EXTRA_GAP_SECONDS),
) -> bool:
    """Read the feeds, save one file, log one row. Returns False if the run was stopped."""
    started = now()
    feeds = dict(zip(sites["team_id"].astype(int), sites["feed_url"], strict=True))
    todo = [
        (tid, feeds[tid])
        for tid, _name in teams_playing_soon(data_dir, teams, started)
        if tid in feeds
    ]
    results, refused, errors, status = [], 0, 0, "ok"
    for index, (team_id, url) in enumerate(todo):
        if index > 0:
            sleep(gap())
        try:
            response = client.get(url)
        except httpx.HTTPError:
            errors += 1
            continue
        if response.status_code in (403, 429):
            refused += 1
            if refused >= MAX_REFUSALS:
                status = "stopped"
                print(f"Stopped after {refused} refusals ({index + 1} of {len(todo)} sites).")
                break
            continue
        items = parse_feed(response.text) if response.status_code == 200 else None
        if items is None:
            errors += 1
            continue
        results.append({"team_id": team_id, "feed_url": url, "items": items, "xml": response.text})

    n_items = sum(len(r["items"]) for r in results)
    row: dict[str, object] = {
        "fetched_at_utc": iso_z(started),
        "status": status,
        "n_teams": len(todo),
        "n_feeds_read": len(results),
        "n_refused": refused,
        "n_errors": errors,
        "n_items": n_items,
    }
    if results:
        payload = {"fetched_at": iso_z(started), "source": "sidearm_rss", "data": results}
        blob = gzip_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        relative = NEWS_DIR / dated_folder(started) / f"sidearm_{file_stamp(started)}.json.gz"
        write_new_file(data_dir / relative, blob)
        row.update(file=relative.as_posix(), sha256=sha256_hex(blob), bytes=len(blob))
    append_log_row(log_path, LOG_COLUMNS, row)
    print(
        f"{status}: {len(results)} of {len(todo)} feeds read, {n_items} items, "
        f"{refused} refused, {errors} errors."
    )
    return status == "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path, help="private data repo")
    parser.add_argument("--log", required=True, type=Path, help="public status log CSV")
    parser.add_argument("--map-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    teams = pd.read_csv(args.map_dir / "teams.csv")
    sites = pd.read_csv(args.map_dir / "team_sites.csv")
    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        ok = run(client, args.data_dir, teams, sites, args.log)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
