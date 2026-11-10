"""Save one raw copy of the current college basketball spreads from The Odds API.

Run as:
    uv run python -m shac.snapshots.odds --data-dir <private data repo> --log <status csv>

What it does, in order:
1. Looks at the status log. If credits are nearly gone, it skips (and rechecks daily).
2. Makes ONE request to the API (1 credit), retrying only on network errors / HTTP 5xx.
3. Saves the response, gzipped and stamped with the fetch time, into the private data repo.
4. Adds one derived row to the public status log (counts and hashes only, never odds).

The API key is never written to the file, the log, or any message.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from shac.snapshots.common import (
    Clock,
    append_log_row,
    dated_folder,
    file_stamp,
    gzip_bytes,
    iso_z,
    parse_iso_z,
    read_log_rows,
    sha256_hex,
    utc_now,
    write_new_file,
)

URL = "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"
REQUEST_INFO = {
    "sport": "basketball_ncaab",
    "regions": "us",
    "markets": "spreads",
    "oddsFormat": "american",
    "dateFormat": "iso",
}
LOG_COLUMNS = [
    "fetched_at_utc",
    "status",
    "http_status",
    "n_games",
    "n_books",
    "credits_used",
    "credits_remaining",
    "file",
    "sha256",
    "bytes",
]
LOW_CREDIT_LIMIT = 5
RECHECK_AFTER = timedelta(hours=24)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [5, 20]


class FetchError(Exception):
    """The fetch failed. The message is safe to print (it never contains the API key)."""

    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _int_header(response: httpx.Response, name: str) -> int | None:
    """Read a credit-count header as an int, or None if missing or not a number."""
    try:
        # float() first, so a value like "480.0" still counts.
        return int(float(response.headers[name]))
    except (KeyError, ValueError):
        return None


def _credit_count(row: dict[str, str]) -> int | None:
    """Read credits_remaining from a log row, or None if the row has no count."""
    try:
        return int(float(row.get("credits_remaining", "")))
    except ValueError:
        return None


def count_books(games: list) -> int | None:
    """Count distinct bookmaker keys across all games. None if the data has an odd shape."""
    try:
        return len({book["key"] for game in games for book in game.get("bookmakers", [])})
    except (AttributeError, KeyError, TypeError):
        return None


def fetch_odds(
    client: httpx.Client,
    api_key: str,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Call the API, retrying only on network errors and 5xx. Returns the 200 response.

    401, 403, 422, 429 (and any other non-5xx error) are not retried.
    Raises FetchError with a scrubbed message for anything that is not a 200.
    """
    params = {
        "apiKey": api_key,
        "regions": REQUEST_INFO["regions"],
        "markets": REQUEST_INFO["markets"],
        "oddsFormat": REQUEST_INFO["oddsFormat"],
        "dateFormat": REQUEST_INFO["dateFormat"],
    }
    last_error = FetchError("no attempt made")
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            sleep(BACKOFF_SECONDS[attempt - 1])
        try:
            response = client.get(URL, params=params)
        except httpx.HTTPError as exc:
            # httpx error text can include the full URL (and so the key), so only
            # the error class name is kept.
            last_error = FetchError(f"network error: {type(exc).__name__}")
            continue
        if response.status_code == 200:
            return response
        if response.status_code < 500:
            raise FetchError(f"HTTP {response.status_code}", response.status_code)
        last_error = FetchError(f"HTTP {response.status_code}", response.status_code)
    raise last_error


def should_skip_for_credits(rows: list[dict[str, str]], now: datetime) -> bool:
    """Decide whether to skip this run because the monthly credits are nearly gone.

    Looks at the last run that told us something about credits: a row with a credit
    count, or one where the API said the quota was used up (HTTP 429). Skipped rows
    and other errors carry no credit information, so they are ignored.
    If that run left 5 or fewer credits, or was a 429, skip. But only for 24 hours:
    after that, try one real call again, so the job picks back up by itself when
    the monthly credits reset.
    """
    informative = [
        r
        for r in rows
        if r.get("status") != "skipped_low_credits"
        and (_credit_count(r) is not None or r.get("http_status") == "429")
    ]
    if not informative:
        return False
    last = informative[-1]
    count = _credit_count(last)
    low = count is not None and count <= LOW_CREDIT_LIMIT
    if not (low or last.get("http_status") == "429"):
        return False
    try:
        age = now - parse_iso_z(last.get("fetched_at_utc", ""))
    except ValueError:
        return False
    return age < RECHECK_AFTER


def run(
    client: httpx.Client,
    api_key: str,
    data_dir: Path,
    log_path: Path,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """Do one snapshot run. Returns (status, one-line summary). Always logs one row."""
    fetched_at = now()
    row: dict[str, object] = {"fetched_at_utc": iso_z(fetched_at)}

    if should_skip_for_credits(read_log_rows(log_path), fetched_at):
        row.update(status="skipped_low_credits")
        append_log_row(log_path, LOG_COLUMNS, row)
        return "skipped_low_credits", "skipped_low_credits"

    try:
        response = fetch_odds(client, api_key, sleep)
        # Re-read the clock so the stamp reflects when the data actually arrived.
        fetched_at = now()
        row["fetched_at_utc"] = iso_z(fetched_at)
        games = response.json()
        if not isinstance(games, list):
            raise FetchError("unexpected response shape (not a list)", 200)
        credits = {
            "remaining": _int_header(response, "x-requests-remaining"),
            "used": _int_header(response, "x-requests-used"),
            "last": _int_header(response, "x-requests-last"),
        }
        payload = {
            "fetched_at": iso_z(fetched_at),
            "source": "the-odds-api",
            "request": REQUEST_INFO,
            "credits": credits,
            "data": games,
        }
        blob = gzip_bytes(json.dumps(payload).encode("utf-8"))
        relative = f"odds/raw/{dated_folder(fetched_at)}/odds_{file_stamp(fetched_at)}.json.gz"
        write_new_file(data_dir / relative, blob)
    except (FetchError, ValueError, OSError) as exc:
        message = exc.args[0] if isinstance(exc, FetchError) else type(exc).__name__
        row.update(status="error", http_status=getattr(exc, "http_status", None))
        append_log_row(log_path, LOG_COLUMNS, row)
        return "error", f"error: {message}"

    row.update(
        status="ok",
        http_status=200,
        n_games=len(games),
        n_books=count_books(games),
        credits_used=credits["used"],
        credits_remaining=credits["remaining"],
        file=relative,
        sha256=sha256_hex(blob),
        bytes=len(blob),
    )
    append_log_row(log_path, LOG_COLUMNS, row)
    return "ok", f"ok games={len(games)} credits_remaining={credits['remaining']}"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 0 for ok / skipped, 1 for error."""
    parser = argparse.ArgumentParser(description="Save a raw odds snapshot.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args(argv)

    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        print("error: ODDS_API_KEY is not set", file=sys.stderr)
        return 1

    with httpx.Client(timeout=30) as client:
        status, summary = run(client, api_key, args.data_dir, args.log)
    print(summary)
    return 0 if status in ("ok", "skipped_low_credits") else 1


if __name__ == "__main__":
    sys.exit(main())
