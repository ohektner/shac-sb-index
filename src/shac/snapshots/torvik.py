"""Save raw daily copies of two Bart Torvik files (team results and the super schedule).

Run as:
    uv run python -m shac.snapshots.torvik --data-dir <private repo> --log <csv> --season 2027

The files have no header row and are saved byte-for-byte (just gzipped).
The site asks for a 10 second crawl delay, so this waits between the two requests.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

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

USER_AGENT = "shac-sb-index/0.1 (+https://github.com/ohektner/shac-sb-index)"
FILE_KINDS = ["team_results", "super_sked"]
LOG_COLUMNS = [
    "fetched_at_utc",
    "status",
    "http_status",
    "source_file",
    "n_lines",
    "file",
    "sha256",
    "bytes",
]
CRAWL_DELAY_SECONDS = 10
RETRY_DELAY_SECONDS = 30
# Both files have a row per team or per game, so a real one is far longer than this.
MIN_LINES = 50


def current_season(today: datetime) -> int:
    """Return the season's ending year: July 2026 through June 2027 is season 2027."""
    return today.year + 1 if today.month >= 7 else today.year


BLOCK_MARKERS = [b"verifying your browser", b"just a moment"]


def looks_blocked(body: bytes) -> bool:
    """True if the response is a Cloudflare / HTML page instead of CSV data.

    Only the body is checked. The content-type header is ignored on purpose: some
    servers label real CSV files as HTML, and we don't want to throw data away.
    """
    start = body[:200].lstrip().lower()
    if start.startswith((b"<!doctype", b"<html")):
        return True
    head = body[:5000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


def fetch_with_one_retry(
    client: httpx.Client, url: str, sleep: Callable[[float], None]
) -> httpx.Response | None:
    """GET a URL; retry once after 30s on a network error or 5xx. None means network failure."""
    response = None
    for attempt in range(2):
        if attempt > 0:
            sleep(RETRY_DELAY_SECONDS)
        try:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError:
            response = None
            continue
        if response.status_code < 500:
            return response
    return response


def run(
    client: httpx.Client,
    data_dir: Path,
    log_path: Path,
    season: int = 2027,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Fetch both files and log one row per file. Returns True only if both were saved."""
    all_ok = True
    for index, kind in enumerate(FILE_KINDS):
        if index > 0:
            sleep(CRAWL_DELAY_SECONDS)
        source_name = f"{season}_{kind}.csv"
        url = f"https://barttorvik.com/{source_name}"
        fetched_at = now()
        row: dict[str, object] = {
            "fetched_at_utc": iso_z(fetched_at),
            "source_file": source_name,
        }
        response = fetch_with_one_retry(client, url, sleep)
        # Re-read the clock so the stamp reflects when the data actually arrived.
        fetched_at = now()
        row["fetched_at_utc"] = iso_z(fetched_at)
        if response is None:
            row["status"] = "error"
        else:
            row["http_status"] = response.status_code
            body = response.content
            # A browser-check page often comes back as HTTP 403, so check for it first.
            if looks_blocked(body):
                row["status"] = "blocked"
            elif response.status_code != 200:
                row["status"] = "error"
            elif len(body.splitlines()) < MIN_LINES:
                # An empty or cut-off file is not a real snapshot.
                row["status"] = "error"
                row["n_lines"] = len(body.splitlines())
            else:
                relative = (
                    f"torvik/raw/{dated_folder(fetched_at)}/"
                    f"{season}_{kind}_{file_stamp(fetched_at)}.csv.gz"
                )
                blob = gzip_bytes(body)
                try:
                    write_new_file(data_dir / relative, blob)
                except OSError:
                    row["status"] = "error"
                else:
                    row.update(
                        status="ok",
                        n_lines=len(body.splitlines()),
                        file=relative,
                        sha256=sha256_hex(blob),
                        bytes=len(blob),
                    )
        append_log_row(log_path, LOG_COLUMNS, row)
        print(f"{source_name}: {row['status']}")
        if row["status"] != "ok":
            all_ok = False
    return all_ok


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 1 if any file was not saved."""
    parser = argparse.ArgumentParser(description="Save raw Torvik snapshots.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--season", type=int, default=None, help="Ending year; default: current")
    args = parser.parse_args(argv)
    season = args.season if args.season is not None else current_season(utc_now())
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        ok = run(client, args.data_dir, args.log, season)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
