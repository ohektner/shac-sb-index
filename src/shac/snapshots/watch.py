"""Check that the odds snapshot job is still running, and fail loudly if it is not.

Run as:
    uv run python -m shac.snapshots.watch --log data/status/odds_snapshot_log.csv --max-age-hours 3

GitHub can skip a scheduled run without telling anyone, and it switches scheduled jobs
off in a repo that has gone quiet. This reads the public status log and exits 1 if the
newest "ok" row is older than the limit. A failed run sends GitHub's usual email.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from shac.snapshots.common import parse_iso_z, read_log_rows, utc_now


def newest_ok(rows: list[dict[str, str]]) -> datetime | None:
    """Return the time of the most recent row with status "ok", or None if there is none."""
    times = []
    for row in rows:
        if row.get("status") != "ok":
            continue
        try:
            times.append(parse_iso_z(row.get("fetched_at_utc", "")))
        except ValueError:
            continue
    return max(times) if times else None


def check(rows: list[dict[str, str]], now: datetime, max_age: timedelta) -> tuple[bool, str]:
    """Return (healthy, one-line message)."""
    latest = newest_ok(rows)
    if latest is None:
        return False, "no ok snapshot in the log"
    age_hours = (now - latest).total_seconds() / 3600
    if now - latest > max_age:
        return False, f"last ok snapshot was {age_hours:.1f} hours ago"
    return True, f"healthy: last ok snapshot was {age_hours:.1f} hours ago"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 0 if healthy, 1 if snapshots have stopped."""
    parser = argparse.ArgumentParser(description="Check the odds snapshot job is alive.")
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--max-age-hours", type=float, default=3.0)
    args = parser.parse_args(argv)
    healthy, message = check(
        read_log_rows(args.log), utc_now(), timedelta(hours=args.max_age_hours)
    )
    print(message)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
