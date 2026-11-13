"""Tests for the snapshot watch check."""

from datetime import UTC, datetime, timedelta

from shac.snapshots import watch

NOW = datetime(2026, 11, 9, 23, 47, 0, tzinfo=UTC)
LIMIT = timedelta(hours=3)


def row(stamp, status="ok"):
    return {"fetched_at_utc": stamp, "status": status}


def test_recent_ok_row_is_healthy():
    healthy, message = watch.check([row("2026-11-09T23:17:05Z")], NOW, LIMIT)
    assert healthy
    assert "healthy" in message


def test_old_ok_row_is_unhealthy():
    healthy, message = watch.check([row("2026-11-09T18:17:05Z")], NOW, LIMIT)
    assert not healthy
    assert "5.5 hours ago" in message


def test_recent_rows_that_are_not_ok_do_not_count():
    rows = [
        row("2026-11-09T18:17:05Z"),
        row("2026-11-09T23:17:05Z", "error"),
        row("2026-11-09T23:18:00Z", "push_failed"),
    ]
    healthy, _ = watch.check(rows, NOW, LIMIT)
    assert not healthy


def test_empty_log_is_unhealthy():
    healthy, message = watch.check([], NOW, LIMIT)
    assert not healthy
    assert "no ok snapshot" in message


def test_main_exit_codes(tmp_path, monkeypatch):
    log = tmp_path / "log.csv"
    log.write_text("fetched_at_utc,status\n2026-11-09T23:17:05Z,ok\n", encoding="utf-8")
    monkeypatch.setattr(watch, "utc_now", lambda: NOW)
    assert watch.main(["--log", str(log)]) == 0
    monkeypatch.setattr(watch, "utc_now", lambda: NOW + timedelta(hours=6))
    assert watch.main(["--log", str(log)]) == 1
    assert watch.main(["--log", str(tmp_path / "missing.csv")]) == 1


SLOTS = watch.parse_slots("01:17,03:17,13:17,16:17,19:17,21:17,23:17")
GRACE = timedelta(hours=3)


def test_schedule_overnight_gap_is_healthy():
    # 13:27 UTC on 11-13: last ok 08:02 covers the 03:17 slot; 13:17 is not 3 hours old yet.
    now = datetime(2026, 11, 13, 13, 27, tzinfo=UTC)
    healthy, _ = watch.check_schedule([row("2026-11-13T08:02:57Z")], now, SLOTS, GRACE)
    assert healthy


def test_schedule_missed_slot_is_unhealthy():
    now = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)  # 13:17 slot is 3.7 hours old
    healthy, message = watch.check_schedule([row("2026-11-13T08:02:57Z")], now, SLOTS, GRACE)
    assert not healthy
    assert "2026-11-13 13:17" in message


def test_schedule_early_run_counts():
    now = datetime(2026, 11, 12, 23, 47, tzinfo=UTC)  # 21:17 slot is due
    healthy, _ = watch.check_schedule([row("2026-11-12T20:50:19Z")], now, SLOTS, GRACE)
    assert healthy


def test_schedule_looks_back_across_midnight():
    now = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)  # newest due slot is yesterday 21:17
    healthy, _ = watch.check_schedule([row("2026-11-12T21:20:00Z")], now, SLOTS, GRACE)
    assert healthy
    healthy, _ = watch.check_schedule([row("2026-11-12T16:20:00Z")], now, SLOTS, GRACE)
    assert not healthy


def test_main_schedule_mode(tmp_path, monkeypatch):
    log = tmp_path / "log.csv"
    log.write_text("fetched_at_utc,status\n2026-11-13T08:02:57Z,ok\n", encoding="utf-8")
    monkeypatch.setattr(watch, "utc_now", lambda: datetime(2026, 11, 13, 13, 27, tzinfo=UTC))
    assert watch.main(["--log", str(log), "--slots-utc", "03:17,13:17"]) == 0
