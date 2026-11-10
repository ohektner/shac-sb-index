"""Save raw CollegeBasketballData (CBBD) responses into the private data repo.

This does raw ingestion only: every response is saved as-is (gzipped JSON) plus one
row in a manifest CSV. Nothing is parsed into tables here.

Run:
    uv run python -m shac.ingest.cbbd --data-dir <private repo checkout> --mode discover
    uv run python -m shac.ingest.cbbd --data-dir <private repo checkout> --mode backfill

The free quota is 1,000 calls a month, so every call goes through one function
(`Ingestor.fetch`) that checks the manifest first (never repeat a saved call) and
the budget (stop before spending too much).

CBBD's terms forbid republishing the data, so nothing is saved in the public repo and
nothing from a response is printed except field names, counts and statuses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from shac.ingest.windows import (
    CAP_ROWS,
    MONTHLY_ENDPOINTS,
    Window,
    is_single_day,
    split_window,
    windows_for,
)
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

BASE_URL = "https://api.collegebasketballdata.com"
MAX_ATTEMPTS = 3
DEFAULT_MONTHLY_BUDGET = 800
DISCOVER_MAX_CALLS = 8
BACKFILL_MAX_CALLS = 300
DEFAULT_SEASONS = "2019-2026"
RAW_DIR = Path("cbbd") / "raw"

# status: ok | capped (hit the row cap; file kept) | error | retry (a 5xx we then retried).
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
    """A budget limit was reached. The run ends cleanly (exit 0)."""


class FatalApiError(Exception):
    """The API refused us (401, 403 or 429). The whole run stops with exit code 1."""


@dataclass
class FetchResult:
    """What `fetch` tells its caller. `first_record` is only set for a fresh, saved call."""

    status: str
    n_rows: int
    cached: bool
    first_record: dict | None = None


def slugify(text: str) -> str:
    """Make text safe for a file name: keep letters, digits, dot and dash, drop the rest."""
    return re.sub(r"[^A-Za-z0-9.\-]", "", text)


def endpoint_slug(endpoint: str) -> str:
    """games/teams -> games-teams."""
    return endpoint.strip("/").replace("/", "-")


def params_slug(params: dict[str, str | int]) -> str:
    """Short file-name text for the params, e.g. startDateRange-20251101T000000Z_season-2026."""
    if not params:
        return "all"
    return "_".join(f"{slugify(k)}-{slugify(str(v))}" for k, v in sorted(params.items()))


def json_type(value: object) -> str:
    """Name the JSON type of a value (never the value itself)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    return "dict"


def count_rows(data: object) -> int:
    """Number of records in a response: list length, or 1 for a single object."""
    if isinstance(data, list):
        return len(data)
    return 0 if data is None else 1


def first_record_of(data: object) -> dict | None:
    """The first record of a response, if it is a list of objects (or a single object)."""
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def describe_params(params: dict[str, str | int]) -> str:
    """Printable params. They only ever hold dates and a season, never row data."""
    if "startDateRange" in params:
        start = str(params["startDateRange"])[:10]
        end = str(params["endDateRange"])[:10]
        return f"{start}..{end}"
    return " ".join(f"{k}={v}" for k, v in params.items())


class Ingestor:
    """Makes the API calls, keeps the manifest, and enforces the budgets."""

    def __init__(
        self,
        data_dir: Path,
        api_key: str,
        *,
        max_calls: int,
        monthly_budget: int,
        transport: httpx.BaseTransport | None = None,
        clock: Clock = utc_now,
        sleep: Callable[[float], None] = time.sleep,
        out: Callable[[str], None] = print,
    ) -> None:
        self.data_dir = data_dir
        self.max_calls = max_calls
        self.monthly_budget = monthly_budget
        self.clock = clock
        self.sleep = sleep
        self.out = out
        self.manifest_path = data_dir / RAW_DIR / "manifest.csv"
        self.rows = read_log_rows(self.manifest_path)
        # (endpoint, params_json) -> manifest row, for calls already saved successfully.
        self.cache: dict[tuple[str, str], dict[str, str]] = {}
        for row in self.rows:
            if row["status"] in ("ok", "capped"):
                self.cache[(row["endpoint"], row["params_json"])] = row
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=60.0,
            transport=transport,
        )
        self.calls_made = 0
        self.cached_skips = 0
        self.error_rows = 0
        self.still_capped: list[tuple[str, str]] = []

    # ---- budget -------------------------------------------------------------------------

    def calls_this_month(self) -> int:
        """Calls in the manifest for the current UTC month (any call that reached the server)."""
        month = self.clock().strftime("%Y-%m")
        return sum(
            1 for r in self.rows if r["fetched_at_utc"].startswith(month) and r["http_status"] != ""
        )

    def _check_budget(self) -> None:
        if self.calls_made >= self.max_calls:
            raise StopRun(f"Stopped: reached --max-calls {self.max_calls} for this run.")
        if self.calls_this_month() + 1 > self.monthly_budget:
            raise StopRun(
                f"Stopped: monthly budget of {self.monthly_budget} calls would be exceeded."
            )

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
        if status in ("ok", "capped"):
            self.cache[(endpoint, params_json)] = stored
        if status == "error":
            self.error_rows += 1

    # ---- the one function that calls the API --------------------------------------------

    def fetch(self, endpoint: str, params: dict[str, str | int]) -> FetchResult:
        """Fetch one endpoint with params, unless it is already saved. Saves file + manifest row."""
        params_json = json.dumps(params, sort_keys=True)
        hit = self.cache.get((endpoint, params_json))
        if hit is not None:
            self.cached_skips += 1
            return FetchResult(hit["status"], int(hit["n_rows"] or 0), cached=True)

        http_status: int | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._check_budget()
            try:
                response = self.client.get(f"/{endpoint}", params=params)
            except httpx.HTTPError as exc:
                # Only the class name is kept: httpx messages can include URLs or headers.
                self.out(f"  network error ({type(exc).__name__}), attempt {attempt}")
                http_status = None
                if attempt < MAX_ATTEMPTS:
                    self.sleep(2.0 * attempt)
                continue

            self.calls_made += 1
            http_status = response.status_code
            if http_status == 200:
                return self._save(endpoint, params, params_json, response)
            if http_status >= 500:
                self.out(f"  HTTP {http_status}, attempt {attempt}")
                if attempt < MAX_ATTEMPTS:
                    self._record(endpoint, params_json, "retry", http_status=http_status)
                    self.sleep(2.0 * attempt)
                continue
            # 4xx: never retried.
            self._record(endpoint, params_json, "error", http_status=http_status)
            if http_status in (401, 403, 429):
                raise FatalApiError(f"HTTP {http_status} from the API. Stopping the whole run.")
            return FetchResult("error", 0, cached=False)

        # Three tries failed (5xx or no response).
        self._record(
            endpoint, params_json, "error", http_status="" if http_status is None else http_status
        )
        return FetchResult("error", 0, cached=False)

    def _save(
        self,
        endpoint: str,
        params: dict[str, str | int],
        params_json: str,
        response: httpx.Response,
    ) -> FetchResult:
        """Write a 200 response to a new gzipped file and add its manifest row."""
        now = self.clock()
        try:
            data = response.json()
        except ValueError:
            self._record(endpoint, params_json, "error", http_status=200)
            self.out("  response was not valid JSON")
            return FetchResult("error", 0, cached=False)

        n_rows = count_rows(data)
        payload = {
            "fetched_at": iso_z(now),
            "source": "cbbd",
            "endpoint": endpoint,
            "params": params,
            "n_rows": n_rows,
            "data": data,
        }
        raw = gzip_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        name = f"{params_slug(params)}_{file_stamp(now)}.json.gz"
        relative = RAW_DIR / endpoint_slug(endpoint) / name
        write_new_file(self.data_dir / relative, raw)

        cap = CAP_ROWS.get(endpoint)
        status = "capped" if cap is not None and n_rows >= cap else "ok"
        self._record(
            endpoint,
            params_json,
            status,
            http_status=200,
            n_rows=n_rows,
            file=relative.as_posix(),
            sha256=sha256_hex(raw),
            bytes=len(raw),
        )
        return FetchResult(status, n_rows, cached=False, first_record=first_record_of(data))

    # ---- helpers that decide which calls to make ----------------------------------------

    def run_call(
        self, endpoint: str, params: dict[str, str | int], *, show_shape: bool = False
    ) -> FetchResult:
        """Fetch, then print one safe line (endpoint, dates, count, status)."""
        result = self.fetch(endpoint, params)
        note = "cached" if result.cached else result.status
        self.out(f"/{endpoint} {describe_params(params)}: {note} rows={result.n_rows}")
        if show_shape:
            self._print_shape(result)
        return result

    def _print_shape(self, result: FetchResult) -> None:
        """Print field names and JSON types of the first record (never values)."""
        if result.cached:
            self.out("  (cached: shape not shown)")
        elif result.first_record is None:
            self.out("  (no records to describe)")
        else:
            fields = ", ".join(f"{k}:{json_type(v)}" for k, v in result.first_record.items())
            self.out(f"  fields: {fields}")

    def fetch_window(
        self, endpoint: str, window: Window, *, split: bool, show_shape: bool = False
    ) -> None:
        """Fetch a date window. If capped and `split`, fetch the two halves instead."""
        params = {"startDateRange": iso_z(window[0]), "endDateRange": iso_z(window[1])}
        result = self.run_call(endpoint, params, show_shape=show_shape)
        if result.status != "capped":
            return
        if not split:
            self.still_capped.append((endpoint, describe_params(params)))
        elif is_single_day(window):
            self.out("  still capped at one day; cannot split further")
            self.still_capped.append((endpoint, describe_params(params)))
        else:
            first, second = split_window(window)
            self.fetch_window(endpoint, first, split=True)
            self.fetch_window(endpoint, second, split=True)

    # ---- the two modes ------------------------------------------------------------------

    def discover(self) -> None:
        """Eight cheap calls to learn the real response shapes."""
        self.run_call("conferences", {}, show_shape=True)
        self.run_call("lines/providers", {}, show_shape=True)
        self.run_call("teams", {"season": 2026}, show_shape=True)
        day_start = datetime(2026, 1, 10, tzinfo=UTC)
        day = (day_start, day_start + timedelta(days=1))
        for endpoint in ("games", "lines", "games/teams", "games/players"):
            self.fetch_window(endpoint, day, split=False, show_shape=True)
        month = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        self.fetch_window("games", month, split=False, show_shape=True)

    def backfill(self, seasons: list[int]) -> None:
        """Fetch teams and every window of every season, newest season first."""
        for season in sorted(seasons, reverse=True):
            self.out(f"== season {season} ==")
            self.run_call("teams", {"season": season})
            for endpoint in (*MONTHLY_ENDPOINTS, "games/players"):
                for window in windows_for(endpoint, season):
                    self.fetch_window(endpoint, window, split=True)


def parse_seasons(text: str) -> list[int]:
    """Parse '2019-2026', '2024,2026' or '2025' into a list of seasons."""
    seasons: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            low, high = part.split("-", 1)
            seasons.extend(range(int(low), int(high) + 1))
        elif part:
            seasons.append(int(part))
    return sorted(set(seasons))


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Command-line entry point. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Save raw CBBD responses to the private repo.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=["discover", "backfill"])
    parser.add_argument("--seasons", default=DEFAULT_SEASONS)
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--monthly-budget", type=int, default=DEFAULT_MONTHLY_BUDGET)
    args = parser.parse_args(argv)

    api_key = os.environ.get("CBBD_API_KEY", "")
    if not api_key:
        print("CBBD_API_KEY is not set.")
        return 1
    if args.max_calls is None:
        args.max_calls = DISCOVER_MAX_CALLS if args.mode == "discover" else BACKFILL_MAX_CALLS

    ingestor = Ingestor(
        args.data_dir,
        api_key,
        max_calls=args.max_calls,
        monthly_budget=args.monthly_budget,
        transport=transport,
        clock=clock,
        sleep=sleep,
    )
    fatal = False
    try:
        if args.mode == "discover":
            ingestor.discover()
        else:
            ingestor.backfill(parse_seasons(args.seasons))
    except StopRun as stop:
        print(str(stop))
    except FatalApiError as err:
        print(str(err))
        fatal = True
    finally:
        ingestor.client.close()

    print("== summary ==")
    print(f"calls made this run: {ingestor.calls_made}")
    print(f"cached skips: {ingestor.cached_skips}")
    print(f"calls this month: {ingestor.calls_this_month()}")
    print(f"windows still capped: {len(ingestor.still_capped)}")
    for endpoint, window in ingestor.still_capped:
        print(f"  /{endpoint} {window}")
    print(f"error rows written: {ingestor.error_rows}")
    return 1 if fatal or ingestor.error_rows else 0


if __name__ == "__main__":
    sys.exit(main())
