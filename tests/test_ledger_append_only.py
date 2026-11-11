"""The ledger is append-only (docs/ARCHITECTURE.md, "Integrity rules").

Two halves:

- `test_ledger_matches_main_plus_appends`: every ledger file on `main` must still be in
  this checkout with every line unchanged and in place; new lines may only be added at
  the end. CI fetches `main` first (ci.yml). Outside CI, the test is skipped if there is
  no `main` to compare with; in CI a missing `main` FAILS, so the check can't silently
  turn itself off. The base can be changed with LEDGER_BASE_REF (default origin/main).
- Unit tests for the writer: made-up games, nothing touches the real ledger.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shac.ledger.writer import (
    LEDGER_COLUMNS,
    LedgerError,
    append_only_problems,
    append_rows,
    ledger_path,
    read_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_REF = os.environ.get("LEDGER_BASE_REF", "origin/main")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )


def test_ledger_matches_main_plus_appends():
    if git("rev-parse", "--verify", "--quiet", f"{BASE_REF}^{{commit}}").returncode != 0:
        if os.environ.get("CI"):
            pytest.fail(f"{BASE_REF} is not available; CI must fetch it (see ci.yml)")
        pytest.skip(f"{BASE_REF} not available locally")
    listing = git("ls-tree", "-r", "--name-only", BASE_REF, "--", "ledger")
    assert listing.returncode == 0, listing.stderr
    problems = []
    for name in [n for n in listing.stdout.splitlines() if n.endswith(".csv")]:
        old = git("show", f"{BASE_REF}:{name}")
        assert old.returncode == 0, old.stderr
        current = REPO_ROOT / name
        if not current.exists():
            problems.append(f"{name}: file removed")
            continue
        new_text = current.read_text(encoding="utf-8")
        problems += [f"{name}: {p}" for p in append_only_problems(old.stdout, new_text)]
    assert problems == [], problems


def test_every_ledger_file_has_the_contract_header():
    for path in sorted((REPO_ROOT / "ledger").rglob("*.csv")):
        read_rows(path)  # raises if the header is wrong


# ---------- append_only_problems ----------

OLD = "a,b\n1,2\n3,4\n"


@pytest.mark.parametrize(
    ("new", "ok"),
    [
        (OLD, True),
        (OLD + "5,6\n", True),
        ("a,b\n1,2\n3,5\n", False),  # a value changed
        ("a,b\n3,4\n1,2\n", False),  # reordered
        ("a,b\n1,2\n", False),  # a row removed
        ("a,b\n1,2\n9,9\n3,4\n", False),  # inserted in the middle
        ("a,c\n1,2\n3,4\n", False),  # header changed
    ],
)
def test_append_only_problems(new, ok):
    assert (append_only_problems(OLD, new) == []) is ok


# ---------- append_rows ----------

TIP = datetime(2026, 11, 20, 23, 0, tzinfo=UTC)


def make_row(game_id=1, run="morning", priced_at=None, tip_at=TIP, **changes):
    row = {
        "game_id": game_id,
        "run": run,
        "priced_at": priced_at or TIP - timedelta(hours=10),
        "tip_at": tip_at,
        "home_team_id": 10,
        "away_team_id": 20,
        "model_version": "abc1234",
        "model_margin": 4.25,
        "line": -3.5,
        "book": "consensus",
        "line_snapshot_at": TIP - timedelta(hours=11),
        "kalshi_implied_margin": None,
        "cover_prob": 0.541,
        "side": "home",
        "tier": "lean",
        "flags": "",
    }
    row.update(changes)
    return row


NOW = TIP - timedelta(hours=9)


def test_append_writes_header_once_and_only_adds(tmp_path):
    path = ledger_path(tmp_path, 2027)
    assert append_rows(path, [make_row(1)], NOW) == 1
    first = path.read_text(encoding="utf-8")
    assert first.splitlines()[0] == ",".join(LEDGER_COLUMNS)
    late = make_row(1, run="late", priced_at=TIP - timedelta(hours=1))
    assert append_rows(path, [late, make_row(2)], TIP - timedelta(minutes=30)) == 2
    second = path.read_text(encoding="utf-8")
    assert append_only_problems(first, second) == []
    rows = read_rows(path)
    assert [(r["game_id"], r["run"]) for r in rows] == [
        ("1", "morning"),
        ("1", "late"),
        ("2", "morning"),
    ]
    assert rows[0]["priced_at"] == "2026-11-20T13:00:00Z" and rows[0]["kalshi_implied_margin"] == ""


@pytest.mark.parametrize(
    "bad",
    [
        make_row(priced_at=TIP),  # priced exactly at tip
        make_row(priced_at=TIP + timedelta(minutes=1)),  # after tip
        make_row(run="noon"),
        make_row(side="over"),
        make_row(tier="lock"),
        make_row(cover_prob=1.2),
        make_row(model_margin=None),
        make_row(flags="a,b"),
        {k: v for k, v in make_row().items() if k != "book"},  # missing column
        {**make_row(), "home_points": 70},  # a result in the ledger: extra column
    ],
)
def test_bad_rows_are_refused_and_nothing_is_written(tmp_path, bad):
    path = ledger_path(tmp_path, 2027)
    append_rows(path, [make_row(9)], NOW)
    before = path.read_bytes()
    with pytest.raises(LedgerError):
        append_rows(path, [make_row(8), bad], NOW)
    assert path.read_bytes() == before


def test_a_game_that_tipped_before_the_write_is_refused(tmp_path):
    path = ledger_path(tmp_path, 2027)
    with pytest.raises(LedgerError):
        append_rows(path, [make_row()], TIP + timedelta(seconds=1))
    assert not path.exists()


def test_duplicate_key_is_refused(tmp_path):
    path = ledger_path(tmp_path, 2027)
    append_rows(path, [make_row(1)], NOW)
    with pytest.raises(LedgerError, match="duplicate"):
        append_rows(path, [make_row(1)], NOW)
