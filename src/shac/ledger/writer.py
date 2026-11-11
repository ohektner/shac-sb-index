"""Write rows to the predictions ledger, and check that it is append-only.

The ledger is the public record of every call (`ledger/season=YYYY/ledger.csv`, contract
in docs/ARCHITECTURE.md). Its rules, enforced here and in tests/test_ledger_append_only.py:

- Rows are only ever added at the end. An existing line is never changed, reordered or
  removed. `append_rows` opens the file in append mode and never rewrites it.
- A row is written only if `priced_at < tip_at`. A game that has started is refused.
- (`game_id`, `run`, `priced_at`) is unique across the whole file.
- Results are never in the ledger; the scorecard joins them at query time.

The pricing job (still to come: it needs the model from task 1.8) builds rows and calls
`append_rows`. Its commit to `main` is the public timestamp.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

LEDGER_ROOT = Path("ledger")

LEDGER_COLUMNS = [
    "game_id",
    "run",
    "priced_at",
    "tip_at",
    "home_team_id",
    "away_team_id",
    "model_version",
    "model_margin",
    "line",
    "book",
    "line_snapshot_at",
    "kalshi_implied_margin",
    "cover_prob",
    "side",
    "tier",
    "flags",
]
KEY_COLUMNS = ("game_id", "run", "priced_at")
RUNS = {"morning", "late"}
SIDES = {"home", "away"}
TIERS = {"pick", "lean", "none"}
TIMESTAMP_COLUMNS = ("priced_at", "tip_at", "line_snapshot_at")
FLOAT_COLUMNS = ("model_margin", "line", "cover_prob")
NULLABLE = {"kalshi_implied_margin", "flags"}


class LedgerError(ValueError):
    """A row breaks a ledger rule. Nothing is written when this is raised."""


def ledger_path(root: Path, season: int) -> Path:
    """`<root>/ledger/season=YYYY/ledger.csv`."""
    return root / LEDGER_ROOT / f"season={season}" / "ledger.csv"


def format_timestamp(value: datetime) -> str:
    """UTC, second precision, ISO with a Z: 2026-11-20T23:00:00Z."""
    if value.tzinfo is None:
        raise LedgerError("timestamps must carry a time zone")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(text: str) -> datetime:
    """Read a ledger timestamp back (the format written by `format_timestamp`)."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_value(column: str, value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        if column in NULLABLE:
            return ""
        raise LedgerError(f"{column} is required")
    if column in TIMESTAMP_COLUMNS:
        if not isinstance(value, datetime):
            raise LedgerError(f"{column} must be a datetime")
        return format_timestamp(value)
    if column in FLOAT_COLUMNS or column == "kalshi_implied_margin":
        return repr(float(value))
    return str(value)


def format_row(row: Mapping[str, object]) -> dict[str, str]:
    """Check one row against the contract and return it as ledger text.

    Raises LedgerError for missing or extra columns, bad values, or a row priced at or
    after tip.
    """
    missing = set(LEDGER_COLUMNS) - set(row)
    extra = set(row) - set(LEDGER_COLUMNS)
    if missing or extra:
        raise LedgerError(f"columns wrong: missing {sorted(missing)}, extra {sorted(extra)}")
    if row["run"] not in RUNS:
        raise LedgerError(f"run must be one of {sorted(RUNS)}")
    if row["side"] not in SIDES:
        raise LedgerError(f"side must be one of {sorted(SIDES)}")
    if row["tier"] not in TIERS:
        raise LedgerError(f"tier must be one of {sorted(TIERS)}")
    prob = float(row["cover_prob"])
    if not 0.0 <= prob <= 1.0:
        raise LedgerError("cover_prob must be between 0 and 1")
    text = {c: _format_value(c, row[c]) for c in LEDGER_COLUMNS}
    if not row["priced_at"] < row["tip_at"]:
        raise LedgerError(f"game {row['game_id']}: priced at or after tip; refused")
    if "," in text["flags"] or "\n" in text["flags"]:
        raise LedgerError("flags are semicolon-separated words")
    return text


def read_rows(path: Path) -> list[dict[str, str]]:
    """The ledger's rows as text, in file order. An absent file has no rows."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != LEDGER_COLUMNS:
            raise LedgerError(f"{path}: header doesn't match the ledger columns")
        return list(reader)


def _csv_line(values: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(values)
    return buffer.getvalue()


def append_rows(path: Path, rows: Iterable[Mapping[str, object]], now: datetime) -> int:
    """Append rows to a season's ledger. Returns how many were written.

    Every row is checked first; if any fails, nothing is written. `now` is the moment of
    writing: a row whose game has already tipped by then is refused too, so a slow job
    can't record a call after tip-off.
    """
    formatted = []
    for row in rows:
        text = format_row(row)
        if not now < row["tip_at"]:
            raise LedgerError(f"game {row['game_id']}: tipped before the write; refused")
        formatted.append(text)
    if not formatted:
        return 0

    existing = read_rows(path)
    keys = {tuple(r[c] for c in KEY_COLUMNS) for r in existing}
    for text in formatted:
        key = tuple(text[c] for c in KEY_COLUMNS)
        if key in keys:
            raise LedgerError(f"duplicate ledger key {key}")
        keys.add(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    if not new_file and not path.read_bytes().endswith(b"\n"):
        raise LedgerError(f"{path}: last line has no newline; won't append")
    with path.open("a", newline="", encoding="utf-8") as handle:
        if new_file:
            handle.write(_csv_line(LEDGER_COLUMNS))
        for text in formatted:
            handle.write(_csv_line([text[c] for c in LEDGER_COLUMNS]))
    return len(formatted)


def append_only_problems(old_text: str, new_text: str) -> list[str]:
    """Why `new_text` isn't `old_text` plus lines added at the end (empty list = fine).

    Compares line by line: every old line must still be there, unchanged and in the same
    place. New lines may only follow them.
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    problems = []
    if old_text and not old_text.endswith("\n"):
        problems.append("the old file's last line had no newline")
    if len(new_lines) < len(old_lines):
        problems.append(f"{len(old_lines) - len(new_lines)} line(s) removed")
    for number, (old, new) in enumerate(zip(old_lines, new_lines, strict=False), start=1):
        if old != new:
            problems.append(f"line {number} changed")
    return problems
