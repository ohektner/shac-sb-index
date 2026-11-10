"""Save raw Polymarket price history for finished college basketball spread markets.

Run:
    uv run python -m shac.ingest.polymarket_history --data-dir <private repo> --mode recent
    uv run python -m shac.ingest.polymarket_history --data-dir <private repo> --mode backfill

For every spread market (and only spread markets) whose game started more than 6 hours
ago, this asks the CLOB API for the price history of BOTH outcome tokens, from 48 hours
before tip-off (or the market's creation, if later) to 4 hours after tip-off, in 5-minute steps.

Modes:
- recent:   finds the markets inside the live snapshot files already saved in the data repo
            (shac.snapshots.polymarket). No listing calls.
- backfill: lists CLOSED events from the Gamma API for the 2025-26 season, saves those
            listing pages too, then fetches history for their spread markets.

Like the CBBD job, every call goes through one method (`History.fetch`) that checks the
manifest first (a saved call is never repeated) and the --max-calls budget.
Raw data goes to the private data repo only. Printed output is counts and statuses only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from shac.snapshots.polymarket import (
    EVENTS_PATH,
    GAMMA_URL,
    PAGE_SIZE,
    REQUEST_GAP_SECONDS,
    SERIES_ID,
    FetchError,
    SpreadMarket,
    get_json,
    make_client,
    parse_when,
    spread_markets,
)

CLOB_URL = "https://clob.polymarket.com"
PRICES_PATH = "/prices-history"
DEFAULT_MAX_CALLS = 1500
FIDELITY_MINUTES = 5
MIN_HOURS_AFTER_START = 6  # wait this long after tip-off so the market is settled
HOURS_BEFORE_START = 48
HOURS_AFTER_START = 4
SEASON_START = datetime(2025, 11, 1, tzinfo=UTC)
SEASON_END = datetime(2026, 4, 16, tzinfo=UTC)  # exclusive: games through 2026-04-15
WINDOW_DAYS = 7  # closed-event listing is done a week at a time (see docs/POLYMARKET.md)
MAX_PAGES_PER_WINDOW = 15
HISTORY_DIR = Path("polymarket") / "history"
CLOSED_DIR = Path("polymarket") / "raw" / "closed"

# status: ok | error | retry (a 5xx we then retried). Same idea as the CBBD manifest.
MANIFEST_COLUMNS = [
    "fetched_at_utc",
    "endpoint",
    "params_json",
    "status",
    "http_status",
    "n_rows",
    "file",
    "sha256",
    "bytes",
]


class StopRun(Exception):
    """The --max-calls limit was reached. The run ends cleanly (exit 0)."""


class FatalApiError(Exception):
    """The API refused us (blocked, 401, 403 or 429). The whole run stops with exit code 1."""


@dataclass
class Fetched:
    """What `History.fetch` returns: the parsed response (or None on error) and a cache flag."""

    data: object | None
    cached: bool


class History:
    """Makes the API calls, keeps the manifest, and enforces the call budget."""

    def __init__(
        self,
        data_dir: Path,
        *,
        max_calls: int,
        transport: httpx.BaseTransport | None = None,
        clock: Clock = utc_now,
        sleep: Callable[[float], None] = time.sleep,
        out: Callable[[str], None] = print,
    ) -> None:
        self.data_dir = data_dir
        self.max_calls = max_calls
        self.clock = clock
        self.sleep = sleep
        self.out = out
        self.manifest_path = data_dir / HISTORY_DIR / "manifest.csv"
        self.rows = read_log_rows(self.manifest_path)
        # (endpoint, params_json) -> manifest row, for calls already saved successfully.
        self.cache: dict[tuple[str, str], dict[str, str]] = {}
        for row in self.rows:
            if row["status"] == "ok":
                self.cache[(row["endpoint"], row["params_json"])] = row
        self.client = make_client(transport)
        self.calls_made = 0
        self.cached_skips = 0
        self.error_rows = 0
        self.saved_files = 0

    # ---- manifest -----------------------------------------------------------------------

    def _record(self, endpoint: str, params_json: str, status: str, **fields: object) -> None:
        row = {
            "fetched_at_utc": iso_z(self.clock()),
            "endpoint": endpoint,
            "params_json": params_json,
            "status": status,
            "http_status": "",
            "n_rows": 0,
            "file": "",
            "sha256": "",
            "bytes": "",
            **fields,
        }
        append_log_row(self.manifest_path, MANIFEST_COLUMNS, row)
        stored = {k: "" if v is None else str(v) for k, v in row.items()}
        self.rows.append(stored)
        if status == "ok":
            self.cache[(endpoint, params_json)] = stored
        if status == "error":
            self.error_rows += 1

    # ---- the one method that calls the API ----------------------------------------------

    def fetch(
        self,
        endpoint: str,
        url: str,
        params: dict[str, object],
        *,
        folder: Path,
        name_prefix: str,
        envelope: dict[str, object],
        count: Callable[[object], int],
    ) -> Fetched:
        """Fetch one URL unless already saved. Saves a gzipped file and a manifest row.

        `envelope` is the extra fields written next to "fetched_at" and "data" in the file.
        Returns the parsed data (read back from disk on a cache hit), or data=None on error.
        """
        params_json = json.dumps(params, sort_keys=True)
        hit = self.cache.get((endpoint, params_json))
        if hit is not None:
            self.cached_skips += 1
            return Fetched(load_saved(self.data_dir / hit["file"]), cached=True)

        for attempt in range(1, 4):
            if self.calls_made >= self.max_calls:
                raise StopRun(f"Stopped: reached --max-calls {self.max_calls} for this run.")
            if self.calls_made > 0:
                self.sleep(REQUEST_GAP_SECONDS)
            self.calls_made += 1
            try:
                data = get_json(self.client, url, params, self.sleep, max_attempts=1)
            except FetchError as err:
                if err.blocked or err.http_status in (401, 403, 429):
                    self._record(endpoint, params_json, "error", http_status=err.http_status)
                    raise FatalApiError(f"{err}. Stopping the whole run.") from err
                retryable = err.http_status is None or err.http_status >= 500
                if retryable and attempt < 3:
                    self._record(endpoint, params_json, "retry", http_status=err.http_status)
                    self.sleep(10.0 * attempt)
                    continue
                self._record(endpoint, params_json, "error", http_status=err.http_status)
                self.out(f"  {endpoint}: {err}")
                return Fetched(None, cached=False)
            return self._save(
                endpoint, params, params_json, data, folder, name_prefix, envelope, count
            )
        return Fetched(None, cached=False)  # pragma: no cover (loop always returns or raises)

    def _save(
        self,
        endpoint: str,
        params: dict[str, object],
        params_json: str,
        data: object,
        folder: Path,
        name_prefix: str,
        envelope: dict[str, object],
        count: Callable[[object], int],
    ) -> Fetched:
        now = self.clock()
        payload = {"fetched_at": iso_z(now), **envelope, "data": data}
        blob = gzip_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        relative = folder / f"{name_prefix}_{file_stamp(now)}.json.gz"
        write_new_file(self.data_dir / relative, blob)
        self.saved_files += 1
        self._record(
            endpoint,
            params_json,
            "ok",
            http_status=200,
            n_rows=count(data),
            file=relative.as_posix(),
            sha256=sha256_hex(blob),
            bytes=len(blob),
        )
        return Fetched(data, cached=False)

    # ---- price history for one market ---------------------------------------------------

    def fetch_market_history(self, market: SpreadMarket) -> None:
        """Fetch the history of every outcome token of one spread market."""
        start, end = history_window(market)
        for token_id in market.token_ids:
            params = {
                "market": token_id,
                "startTs": int(start.timestamp()),
                "endTs": int(end.timestamp()),
                "fidelity": FIDELITY_MINUTES,
            }
            envelope = {
                "source": "polymarket-clob",
                "params": params,
                "market": {
                    "event_slug": market.event_slug,
                    "market_id": market.market_id,
                    "line": market.line,
                    "question": market.question,
                    "outcomes": market.outcomes,
                    "token_id": token_id,
                    "game_start": iso_z(market.game_start),
                },
            }
            self.fetch(
                "prices-history",
                CLOB_URL + PRICES_PATH,
                params,
                folder=HISTORY_DIR / market.game_start.strftime("%Y/%m/%d"),
                name_prefix=token_id,
                envelope=envelope,
                count=count_history_points,
            )

    def run_markets(self, markets: list[SpreadMarket]) -> int:
        """Fetch history for every eligible market, oldest game first. Returns how many."""
        cutoff = self.clock() - timedelta(hours=MIN_HOURS_AFTER_START)
        eligible = sorted(
            (m for m in markets if m.game_start < cutoff), key=lambda m: (m.game_start, m.market_id)
        )
        self.out(f"spread markets: {len(markets)}, eligible (started >6h ago): {len(eligible)}")
        for market in eligible:
            self.fetch_market_history(market)
        return len(eligible)

    # ---- backfill: list closed events ---------------------------------------------------

    def list_closed_window(self, start: datetime, end: datetime) -> list:
        """Fetch (or load) every page of closed events starting in [start, end)."""
        events: list = []
        offset = 0
        for _ in range(MAX_PAGES_PER_WINDOW):
            params = {
                "series_id": SERIES_ID,
                "closed": "true",
                "limit": PAGE_SIZE,
                "offset": offset,
                "order": "startTime",
                "ascending": "true",
                "start_time_min": iso_z(start),
                "start_time_max": iso_z(end),
            }
            name = f"{start:%Y-%m-%d}_to_{end:%Y-%m-%d}_offset{offset}"
            result = self.fetch(
                "events",
                GAMMA_URL + EVENTS_PATH,
                params,
                folder=CLOSED_DIR,
                name_prefix=name,
                envelope={"source": "polymarket-gamma", "params": params},
                count=lambda page: len(page) if isinstance(page, list) else 0,
            )
            page = result.data
            if not isinstance(page, list):
                self.out(f"  closed listing {start:%Y-%m-%d} failed; skipping this window")
                break
            events.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return events

    def backfill(self) -> None:
        """List the 2025-26 closed events a week at a time, then fetch their history."""
        markets: list[SpreadMarket] = []
        for start, end in week_windows(SEASON_START, SEASON_END):
            events = self.list_closed_window(start, end)
            # The server filter is on startTime; check again here so a stray event
            # outside the season never costs history calls.
            events = [e for e in events if in_season(e)]
            markets.extend(spread_markets(events))
            self.out(f"{start:%Y-%m-%d}: events={len(events)}")
        self.run_markets(dedupe(markets))

    def recent(self) -> None:
        """Fetch history for markets found in the saved live snapshot files."""
        markets = []
        for events in saved_snapshot_events(self.data_dir):
            markets.extend(spread_markets(events))
        self.run_markets(dedupe(markets))


# ---- small pure helpers ----------------------------------------------------------------


def history_window(market: SpreadMarket) -> tuple[datetime, datetime]:
    """Return (start, end): later of (creation, tip-off - 48 h), up to tip-off + 4 h."""
    start = market.game_start - timedelta(hours=HOURS_BEFORE_START)
    if market.created_at is not None and market.created_at > start:
        start = market.created_at
    return start, market.game_start + timedelta(hours=HOURS_AFTER_START)


def count_history_points(data: object) -> int:
    """Number of price points in a /prices-history response."""
    if isinstance(data, dict) and isinstance(data.get("history"), list):
        return len(data["history"])
    return 0


def week_windows(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Cut [start, end) into consecutive 7-day windows (the last one may be shorter)."""
    cursor = start
    while cursor < end:
        yield cursor, min(cursor + timedelta(days=WINDOW_DAYS), end)
        cursor += timedelta(days=WINDOW_DAYS)


def in_season(event: object) -> bool:
    """True if an event's startTime is inside the 2025-26 backfill range."""
    moment = parse_when(event.get("startTime")) if isinstance(event, dict) else None
    return moment is not None and SEASON_START <= moment < SEASON_END


def dedupe(markets: list[SpreadMarket]) -> list[SpreadMarket]:
    """Keep one entry per market id (the last one seen, so the newest snapshot wins)."""
    return list({m.market_id: m for m in markets}.values())


def load_saved(path: Path) -> object:
    """Read the "data" part of a file saved by this job (None if it cannot be read)."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle).get("data")
    except (OSError, ValueError):
        return None


def saved_snapshot_events(data_dir: Path) -> Iterator[list]:
    """Yield the events of each saved snapshot file, oldest first.

    Looks only at polymarket/raw/YYYY/MM/DD/events_*.json.gz, so the closed-event
    listing pages in raw/closed/ are not mixed in.
    """
    for path in sorted((data_dir / "polymarket" / "raw").glob("*/*/*/events_*.json.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, ValueError):
            continue
        for page in snapshot.get("pages", []):
            if isinstance(page, list):
                yield page


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Command-line entry point. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Save raw Polymarket price history.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=["recent", "backfill"])
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    args = parser.parse_args(argv)

    history = History(
        args.data_dir, max_calls=args.max_calls, transport=transport, clock=clock, sleep=sleep
    )
    fatal = False
    try:
        if args.mode == "recent":
            history.recent()
        else:
            history.backfill()
    except StopRun as stop:
        print(str(stop))
    except FatalApiError as err:
        print(str(err))
        fatal = True
    finally:
        history.client.close()

    print("== summary ==")
    print(f"calls made this run: {history.calls_made}")
    print(f"cached skips: {history.cached_skips}")
    print(f"files saved: {history.saved_files}")
    print(f"error rows written: {history.error_rows}")
    return 1 if fatal or history.error_rows else 0


if __name__ == "__main__":
    sys.exit(main())
