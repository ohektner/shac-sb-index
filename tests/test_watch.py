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
