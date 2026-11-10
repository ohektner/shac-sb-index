"""Read raw CBBD and Odds API files, and de-duplicate records that appear in several files.

Every raw file is gzipped JSON wrapped like {"fetched_at": ..., "data": [...], ...}.
CBBD fetch windows overlap at their edges, and windows that hit a row cap were split, so
the same game can sit in two or more files. We keep the copy from the most recently
fetched file (ties broken by file name, so the result never depends on disk order) and
count how many extra copies were dropped.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path


def read_wrapped(path: Path) -> dict:
    """Load one raw file: the wrapper dict with `fetched_at` and `data`."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass
class Deduped:
    """De-duplicated records plus how many duplicate copies were dropped.

    `records` are (fetched_at, record) pairs, one per key.
    """

    records: list[tuple[str, dict]]
    n_raw: int
    n_duplicates: int


def dedupe_files(wrapped_files: list[tuple[str, dict]], key: Callable[[dict], Hashable]) -> Deduped:
    """Keep one record per key: the one from the most recently fetched file.

    `wrapped_files` is a list of (file name, wrapper dict). Files are processed oldest
    first, so a later file simply overwrites an earlier copy of the same key.
    """
    ordered = sorted(wrapped_files, key=lambda item: (item[1]["fetched_at"], item[0]))
    kept: dict[Hashable, tuple[str, dict]] = {}
    n_raw = 0
    for _name, wrapped in ordered:
        for record in wrapped.get("data") or []:
            n_raw += 1
            kept[key(record)] = (wrapped["fetched_at"], record)
    return Deduped(records=list(kept.values()), n_raw=n_raw, n_duplicates=n_raw - len(kept))


def load_endpoint(data_dir: Path, slug: str) -> list[tuple[str, dict]]:
    """All raw files for one CBBD endpoint folder, as (file name, wrapper) pairs."""
    folder = data_dir / "cbbd" / "raw" / slug
    return [(p.name, read_wrapped(p)) for p in sorted(folder.glob("*.json.gz"))]


def load_odds_files(data_dir: Path) -> list[tuple[str, dict]]:
    """All Odds API snapshot files, as (file name, wrapper) pairs."""
    folder = data_dir / "odds" / "raw"
    return [(p.name, read_wrapped(p)) for p in sorted(folder.rglob("odds_*.json.gz"))]
