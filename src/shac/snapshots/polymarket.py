"""Save one raw copy of the live college basketball markets from Polymarket's Gamma API.

Run as:
    uv run python -m shac.snapshots.polymarket --data-dir <private data repo> --log <status csv>

What it does, in order:
1. Asks the Gamma API for every ACTIVE (not closed) event in the college basketball
   series, one page of 100 events at a time, until a page comes back short.
2. Saves all the raw pages, unchanged, in ONE gzipped JSON file in the private data repo.
3. Adds one derived row to the public status log: counts and a file hash only.
   Team names and prices never reach the log or the printed output.

Polymarket gave written permission (2026-11-10) to read its public API for this
non-commercial project. Raw prices still stay in the private repo.
No API key is needed. Be polite: at most 2 requests a second.

This file also holds the small helpers the history job (shac.ingest.polymarket_history)
reuses: the User-Agent, blocked-page detection, and reading spread markets out of events.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
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

GAMMA_URL = "https://gamma-api.polymarket.com"
EVENTS_PATH = "/events"
USER_AGENT = "shac-sb-index/0.1 (+https://github.com/ohektner/shac-sb-index)"
SERIES_ID = 10470  # college basketball ("cbb")
PAGE_SIZE = 100
MAX_PAGES = 30  # safety stop: about 370 events were live on 2026-11-10 (4 pages)
REQUEST_GAP_SECONDS = 0.5  # 2 requests a second at most
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [5, 20]

REQUEST_INFO = {
    "series_id": SERIES_ID,
    "active": "true",
    "closed": "false",
    "limit": PAGE_SIZE,
}
LOG_COLUMNS = [
    "fetched_at_utc",
    "status",
    "http_status",
    "n_pages",
    "n_events",
    "n_spread_markets",
    "file",
    "sha256",
    "bytes",
]


class FetchError(Exception):
    """A fetch failed. The message is safe to print (no team names, no prices)."""

    def __init__(self, message: str, http_status: int | None = None, blocked: bool = False):
        super().__init__(message)
        self.http_status = http_status
        self.blocked = blocked


# ---- helpers shared with the history job ----------------------------------------------


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Build an HTTP client that identifies itself politely."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=60.0,
        transport=transport,
    )


def looks_blocked(body: bytes) -> bool:
    """True if the body is an HTML (Cloudflare-style) page instead of JSON.

    Same idea as the Torvik job: only the start of the body is checked.
    """
    start = body[:200].lstrip().lower()
    return start.startswith((b"<!doctype", b"<html"))


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, object],
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = MAX_ATTEMPTS,
) -> object:
    """GET a URL and return the parsed JSON. Retries only network errors and 5xx.

    Pass max_attempts=1 to make exactly one request and let the caller do the retrying.

    Raises FetchError for a blocked page (blocked=True), any non-200 answer, or a
    200 answer that is not JSON. 4xx answers are never retried.
    """
    last_error = FetchError("no attempt made")
    for attempt in range(max_attempts):
        if attempt > 0:
            sleep(BACKOFF_SECONDS[attempt - 1])
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            # Only the class name is kept: httpx messages can include the full URL.
            last_error = FetchError(f"network error: {type(exc).__name__}")
            continue
        # A browser-check page often comes back as HTTP 403, so look at the body first.
        if looks_blocked(response.content):
            raise FetchError("blocked (HTML page instead of JSON)", response.status_code, True)
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise FetchError("response was not valid JSON", 200) from None
        last_error = FetchError(f"HTTP {response.status_code}", response.status_code)
        if response.status_code < 500:
            raise last_error
    raise last_error


def json_list(value: object) -> list:
    """Read a field the API sends as a JSON-encoded string, like '["a", "b"]'.

    Also accepts a real list. Anything else (missing, broken) gives an empty list.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_when(text: object) -> datetime | None:
    """Parse the API's time strings into UTC.

    Handles both '2026-11-11 00:00:00+00' (market gameStartTime) and
    '2026-11-11T00:00:00Z' (event startTime). Returns None if it is not a time.
    """
    if not isinstance(text, str) or not text:
        return None
    cleaned = text.strip().replace(" ", "T", 1)
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    elif len(cleaned) >= 3 and cleaned[-3] in "+-" and cleaned[-2:].isdigit():
        cleaned += ":00"  # '+00' -> '+00:00'
    try:
        moment = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def is_spread_market(market: object) -> bool:
    """True for a Polymarket point-spread market."""
    return isinstance(market, dict) and market.get("sportsMarketType") == "spreads"


def count_spread_markets(events: list) -> int:
    """Count spread markets across a list of events."""
    return sum(
        1
        for event in events
        if isinstance(event, dict)
        for market in event.get("markets") or []
        if is_spread_market(market)
    )


@dataclass
class SpreadMarket:
    """One spread market, with the JSON-string fields already unpacked."""

    event_slug: str
    market_id: str
    line: float | None
    question: str
    outcomes: list[str]
    token_ids: list[str]
    game_start: datetime
    created_at: datetime | None


def spread_markets(events: list) -> Iterator[SpreadMarket]:
    """Yield every spread market (and only spread markets) found in a list of events.

    A market with no usable game start time or no token ids is skipped, because the
    history job cannot use it.
    """
    for event in events:
        if not isinstance(event, dict):
            continue
        for market in event.get("markets") or []:
            if not is_spread_market(market):
                continue
            token_ids = [str(t) for t in json_list(market.get("clobTokenIds"))]
            # The market's own start time is best; fall back to the event's.
            game_start = parse_when(market.get("gameStartTime")) or parse_when(
                event.get("startTime")
            )
            if game_start is None or not token_ids:
                continue
            line = market.get("line")
            yield SpreadMarket(
                event_slug=str(event.get("slug", "")),
                market_id=str(market.get("id", "")),
                line=float(line) if isinstance(line, int | float) else None,
                question=str(market.get("question", "")),
                outcomes=[str(o) for o in json_list(market.get("outcomes"))],
                token_ids=token_ids,
                game_start=game_start,
                created_at=parse_when(market.get("createdAt")),
            )


# ---- the snapshot job ------------------------------------------------------------------


def fetch_all_pages(
    client: httpx.Client, sleep: Callable[[float], None] = time.sleep
) -> list[list]:
    """Page through the active events. Stops when a page has fewer than 100 events."""
    pages: list[list] = []
    offset = 0
    while True:
        if pages:
            sleep(REQUEST_GAP_SECONDS)
        params = {**REQUEST_INFO, "offset": offset}
        page = get_json(client, GAMMA_URL + EVENTS_PATH, params, sleep)
        if not isinstance(page, list):
            raise FetchError("unexpected response shape (not a list)", 200)
        pages.append(page)
        if len(page) < PAGE_SIZE:
            return pages
        if len(pages) >= MAX_PAGES:
            raise FetchError(f"still full pages after {MAX_PAGES} pages; stopping")
        offset += PAGE_SIZE


def run(
    client: httpx.Client,
    data_dir: Path,
    log_path: Path,
    now: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """Do one snapshot run. Returns (status, one-line summary). Always logs one row."""
    row: dict[str, object] = {"fetched_at_utc": iso_z(now())}
    try:
        pages = fetch_all_pages(client, sleep)
        # Re-read the clock so the stamp reflects when the data actually arrived.
        fetched_at = now()
        row["fetched_at_utc"] = iso_z(fetched_at)
        events = [event for page in pages for event in page]
        payload = {
            "fetched_at": iso_z(fetched_at),
            "source": "polymarket-gamma",
            "request": REQUEST_INFO,
            "pages": pages,
        }
        blob = gzip_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        relative = (
            f"polymarket/raw/{dated_folder(fetched_at)}/events_{file_stamp(fetched_at)}.json.gz"
        )
        write_new_file(data_dir / relative, blob)
    except (FetchError, OSError) as exc:
        message = exc.args[0] if isinstance(exc, FetchError) else type(exc).__name__
        status = "blocked" if isinstance(exc, FetchError) and exc.blocked else "error"
        row.update(status=status, http_status=getattr(exc, "http_status", None))
        append_log_row(log_path, LOG_COLUMNS, row)
        return status, f"{status}: {message}"

    n_spreads = count_spread_markets(events)
    row.update(
        status="ok",
        http_status=200,
        n_pages=len(pages),
        n_events=len(events),
        n_spread_markets=n_spreads,
        file=relative,
        sha256=sha256_hex(blob),
        bytes=len(blob),
    )
    append_log_row(log_path, LOG_COLUMNS, row)
    return "ok", f"ok pages={len(pages)} events={len(events)} spread_markets={n_spreads}"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 0 for ok, 1 for blocked or error."""
    parser = argparse.ArgumentParser(description="Save a raw Polymarket events snapshot.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args(argv)

    with make_client() as client:
        status, summary = run(client, args.data_dir, args.log)
    print(summary)
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
