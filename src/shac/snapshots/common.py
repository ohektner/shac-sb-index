"""Small helpers shared by the snapshot jobs.

Both jobs do the same few things: read the clock, save raw bytes without ever
overwriting an existing file, hash the saved file, and add one row to a public
status log (a CSV). They live here so each job stays short.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# A "clock" is any function that returns the current time in UTC. Tests pass a fake one.
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current time in UTC (timezone-aware)."""
    return datetime.now(UTC)


def iso_z(moment: datetime) -> str:
    """Format a UTC time like 2026-11-09T13:17:04Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_stamp(moment: datetime) -> str:
    """Format a UTC time for file names, like 20261109T131704Z."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def dated_folder(moment: datetime) -> str:
    """Return the YYYY/MM/DD folder (forward slashes) for a UTC time."""
    return moment.astimezone(UTC).strftime("%Y/%m/%d")


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hash of some bytes as a hex string."""
    return hashlib.sha256(data).hexdigest()


def gzip_bytes(data: bytes) -> bytes:
    """Gzip some bytes. mtime is fixed at 0 so the same input gives the same output."""
    return gzip.compress(data, mtime=0)


def write_new_file(path: Path, data: bytes) -> None:
    """Write bytes to a brand-new file. Raises FileExistsError if the path already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Mode "xb" means "create, and fail if it is already there".
    with open(path, "xb") as handle:
        handle.write(data)


def append_log_row(log_path: Path, columns: list[str], row: dict[str, object]) -> None:
    """Append one row to a CSV log, writing the header first if the file is new or empty."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not log_path.exists() or log_path.stat().st_size == 0
    with open(log_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        if needs_header:
            writer.writeheader()
        writer.writerow({c: "" if row.get(c) is None else row[c] for c in columns})


def read_log_rows(log_path: Path) -> list[dict[str, str]]:
    """Return every row of a CSV log as a list of dicts (empty if there is no log yet)."""
    if not log_path.exists():
        return []
    with open(log_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_iso_z(text: str) -> datetime:
    """Turn a stamp like 2026-11-09T13:17:04Z back into a UTC time."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
