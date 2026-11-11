"""Tests for the backup trigger's slot logic. No GitHub calls."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from shac.snapshots import backup_trigger as bt

ODDS_YML = """
on:
  schedule:
    # a comment
    - cron: "17 13,16,19,21,23 * * *"
    - cron: "17 1,3 * * *"
  workflow_dispatch:
"""


def at(hour, minute, day=11):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_cron_slots_reads_every_hour():
    assert bt.cron_slots(ODDS_YML) == [
        (1, 17),
        (3, 17),
        (13, 17),
        (16, 17),
        (19, 17),
        (21, 17),
        (23, 17),
    ]


def test_real_workflow_files_parse():
    workflows = Path(__file__).parent.parent / ".github" / "workflows"
    for name in bt.WORKFLOWS:
        assert bt.cron_slots((workflows / name).read_text(encoding="utf-8"))


def test_unsupported_cron_is_refused():
    with pytest.raises(ValueError):
        bt.cron_slots('    - cron: "*/5 * * * 1"')


def test_slot_waits_for_the_grace_period():
    slots = bt.cron_slots(ODDS_YML)
    # 16:17 slot, 20 minutes later: too early, so the previous slot (13:17) is the due one.
    assert bt.latest_due_slot(slots, at(16, 37)) == at(13, 17)
    assert bt.latest_due_slot(slots, at(16, 52)) == at(16, 17)


def test_slot_wraps_to_the_day_before():
    slots = bt.cron_slots(ODDS_YML)
    # 00:30 UTC: the latest due slot is 23:17 the day before.
    assert bt.latest_due_slot(slots, at(0, 30)) == at(23, 17, day=10)


def test_trigger_when_no_run_since_slot():
    slot = at(16, 17)
    assert bt.needs_trigger(slot, at(16, 55), [at(13, 50)]) is True


def test_no_trigger_when_any_run_came_after_slot():
    slot = at(16, 17)
    assert bt.needs_trigger(slot, at(16, 55), [at(16, 48)]) is False


def test_no_trigger_for_a_long_gone_slot():
    # PC wakes at 12:00 UTC: the 03:17 slot is more than 3 hours old, so leave it.
    slot = at(3, 17)
    assert bt.needs_trigger(slot, at(12, 0), []) is False


def test_no_trigger_without_a_slot():
    assert bt.needs_trigger(None, at(12, 0), []) is False


def test_guard_skips_a_late_run_after_the_backup_covered_the_slot():
    slots = bt.cron_slots(ODDS_YML)
    # GitHub's run for the 01:17 slot shows up at 02:46; the backup started one at 01:55.
    assert bt.already_covered(slots, at(2, 46), [at(1, 55)]) is True


def test_guard_lets_the_next_slot_run():
    slots = bt.cron_slots(ODDS_YML)
    # GitHub's run for the 03:17 slot at 03:20; the last run was the 01:55 backup.
    assert bt.already_covered(slots, at(3, 20), [at(1, 55)]) is False


def test_guard_lets_an_uncovered_late_run_through():
    slots = bt.cron_slots(ODDS_YML)
    # Late run for 01:17 at 02:46 with nothing since the slot: it should go ahead.
    assert bt.already_covered(slots, at(2, 46), [at(23, 40, day=10)]) is False
