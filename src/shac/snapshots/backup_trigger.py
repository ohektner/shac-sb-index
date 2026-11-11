"""Backup trigger for the scheduled GitHub Actions jobs, run from Owen's PC.

Run (a Windows task runs this every 15 minutes via scripts/schedule-backup.ps1):
    uv run python -m shac.snapshots.backup_trigger --repo .
    uv run python -m shac.snapshots.backup_trigger --repo . --dry-run
    python -m shac.snapshots.backup_trigger --repo . --guard odds-snapshot.yml --run-id 123

Why: GitHub doesn't promise to run scheduled jobs. On 2026-11-10 it ran 2 of the first
10 slots and skipped the rest (see docs/DECISIONS.md). Missed odds snapshots can't be
recreated later, so this checks each job's most recent slot and, if GitHub hasn't run the
job since then, starts it by hand (workflow_dispatch) through the GitHub CLI (`gh`).

The schedule is read from the workflow files themselves, so there is one source of truth.
A slot is only triggered once it is GRACE_MINUTES old (GitHub's own runs are often 30 to
50 minutes late) and no more than MAX_LATE_MINUTES old (a PC waking from sleep shouldn't
fire long-gone slots). Any run of the job after the slot, scheduled or not, counts.

The other half is the guard (--guard), which runs at the start of a scheduled odds job.
GitHub's own run sometimes turns up 90 minutes late, after this script has already
covered the slot. The guard sees that and skips the late run, so the slot isn't paid for
twice (each odds snapshot costs 2 API credits). It prints skip=true or skip=false.
Only the standard library is used, so the guard runs without installing anything.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shac.snapshots.common import iso_z, parse_iso_z, utc_now

WORKFLOWS = [
    "odds-snapshot.yml",
    "polymarket-snapshot.yml",
    "polymarket-history.yml",
    "snapshot-watch.yml",
]
GRACE_MINUTES = 35
MAX_LATE_MINUTES = 180
# Only the simple daily form our workflows use: "MINUTE HOUR[,HOUR...] * * *".
CRON_LINE = re.compile(r'^\s*-\s*cron:\s*"([^"]+)"', re.MULTILINE)


def cron_slots(workflow_text: str) -> list[tuple[int, int]]:
    """Every (hour, minute) in UTC that a workflow file's cron lines schedule."""
    slots = []
    for expression in CRON_LINE.findall(workflow_text):
        minute, hours, *rest = expression.split()
        if rest != ["*", "*", "*"] or not minute.isdigit():
            raise ValueError(f"Unsupported cron expression: {expression}")
        slots.extend((int(hour), int(minute)) for hour in hours.split(","))
    return sorted(slots)


def latest_due_slot(
    slots: list[tuple[int, int]], now: datetime, grace_minutes: int = GRACE_MINUTES
) -> datetime | None:
    """The most recent slot at least `grace_minutes` old, or None if there isn't one."""
    cutoff = now - timedelta(minutes=grace_minutes)
    candidates = [
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
        for day in (now - timedelta(days=1), now)
        for hour, minute in slots
    ]
    due = [slot for slot in candidates if slot <= cutoff]
    return max(due) if due else None


def needs_trigger(slot: datetime | None, now: datetime, run_times: list[datetime]) -> bool:
    """True if the slot is recent enough to still matter and no run started after it."""
    if slot is None or now - slot > timedelta(minutes=MAX_LATE_MINUTES):
        return False
    return not any(started >= slot for started in run_times)


def already_covered(
    slots: list[tuple[int, int]], now: datetime, other_runs: list[datetime]
) -> bool:
    """Guard: True if another run already started after the latest slot (so skip this one)."""
    slot = latest_due_slot(slots, now, grace_minutes=0)
    return slot is not None and any(started >= slot for started in other_runs)


def recent_run_times(gh: str, workflow: str, exclude_run_id: int | None = None) -> list[datetime]:
    """Start times of the workflow's last 20 runs (any trigger), from the GitHub CLI."""
    output = subprocess.run(
        [
            gh,
            "run",
            "list",
            "--workflow",
            workflow,
            "--limit",
            "20",
            "--json",
            "createdAt,databaseId",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        parse_iso_z(run["createdAt"])
        for run in json.loads(output)
        if run["databaseId"] != exclude_run_id
    ]


def guard(repo: Path, gh: str, workflow: str, run_id: int) -> int:
    """Print skip=true if another run already covered the latest slot, else skip=false."""
    text = (repo / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    covered = already_covered(cron_slots(text), utc_now(), recent_run_times(gh, workflow, run_id))
    print(f"skip={'true' if covered else 'false'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Check every scheduled workflow and start the ones GitHub skipped. Exit 1 on any error."""
    parser = argparse.ArgumentParser(description="Start scheduled jobs that GitHub skipped.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Checkout of this repo")
    parser.add_argument("--gh", default="gh", help="Path to the GitHub CLI")
    parser.add_argument("--dry-run", action="store_true", help="Report only; start nothing")
    parser.add_argument("--guard", metavar="WORKFLOW", help="Guard mode for one workflow")
    parser.add_argument("--run-id", type=int, help="This run's id (guard mode)")
    args = parser.parse_args(argv)
    if args.guard:
        return guard(args.repo, args.gh, args.guard, args.run_id)

    now = utc_now()
    failed = False
    for workflow in WORKFLOWS:
        try:
            text = (args.repo / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            slot = latest_due_slot(cron_slots(text), now)
            if not needs_trigger(slot, now, recent_run_times(args.gh, workflow)):
                print(f"{workflow}: ok")
                continue
            if args.dry_run:
                print(f"{workflow}: would start (missed slot {iso_z(slot)})")
                continue
            subprocess.run(
                [args.gh, "workflow", "run", workflow, "--ref", "main"],
                capture_output=True,
                text=True,
                check=True,
            )
            print(f"{workflow}: started (missed slot {iso_z(slot)})")
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            print(f"{workflow}: error: {error}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
