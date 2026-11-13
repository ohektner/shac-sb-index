"""Check that the odds snapshot job is still running, and fail loudly if it is not.

Run as:
    uv run python -m shac.snapshots.watch --log data/status/odds_snapshot_log.csv \
        --slots-utc 01:17,03:17,13:17,16:17,19:17,21:17,23:17 --grace-hours 3

GitHub can skip a scheduled run without telling anyone, and it switches scheduled jobs
off in a repo that has gone quiet. This reads the public status log and exits 1 if there
is no "ok" row covering the newest scheduled slot that is at least `grace` old (the odds job
only runs 7 times a day, so a fixed "3 hours old" limit raised false alarms overnight).
Without --slots-utc it falls back to the plain "newest ok row older than the limit" check.
A failed run sends GitHub's usual email.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
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


def parse_slots(text: str) -> list[time]:
    """Turn "13:17,16:17" (UTC) into a list of times of day."""
    slots = []
    for part in text.split(","):
        hour, minute = part.strip().split(":")
        slots.append(time(int(hour), int(minute)))
    return sorted(slots)


def last_due_slot(slots: list[time], now: datetime, grace: timedelta) -> datetime | None:
    """Return the newest scheduled slot that is at least `grace` old, or None."""
    cutoff = now - grace
    for day_back in (0, 1):
        day = (cutoff - timedelta(days=day_back)).date()
        due = [datetime.combine(day, slot, tzinfo=now.tzinfo) for slot in slots]
        due = [d for d in due if d <= cutoff]
        if due:
            return max(due)
    return None


def check_schedule(
    rows: list[dict[str, str]],
    now: datetime,
    slots: list[time],
    grace: timedelta,
    early: timedelta = timedelta(minutes=45),
) -> tuple[bool, str]:
    """Healthy if an ok snapshot exists for the newest slot that is `grace` old.

    A run may land a little before its slot (GitHub timing drifts), so `early` is allowed.
    """
    latest = newest_ok(rows)
    if latest is None:
        return False, "no ok snapshot in the log"
    due = last_due_slot(slots, now, grace)
    if due is None:
        return True, "healthy: no scheduled slot is due yet"
    stamp = due.strftime("%Y-%m-%d %H:%M")
    if latest >= due - early:
        return True, f"healthy: slot {stamp} UTC is covered"
    age_hours = (now - latest).total_seconds() / 3600
    return False, f"slot {stamp} UTC has no ok snapshot; last ok was {age_hours:.1f} hours ago"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exit 0 if healthy, 1 if snapshots have stopped."""
    parser = argparse.ArgumentParser(description="Check the odds snapshot job is alive.")
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--max-age-hours", type=float, default=3.0)
    parser.add_argument(
        "--slots-utc",
        help="Comma-separated scheduled times (UTC), e.g. 13:17,16:17. If given, the check is "
        "schedule-aware and --max-age-hours is ignored.",
    )
    parser.add_argument("--grace-hours", type=float, default=3.0)
    args = parser.parse_args(argv)
    rows = read_log_rows(args.log)
    if args.slots_utc:
        healthy, message = check_schedule(
            rows, utc_now(), parse_slots(args.slots_utc), timedelta(hours=args.grace_hours)
        )
    else:
        healthy, message = check(rows, utc_now(), timedelta(hours=args.max_age_hours))
    print(message)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
