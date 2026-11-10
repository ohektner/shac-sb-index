"""Print the data-quality findings behind docs/TABLES.md (aggregate counts and shares).

Run as:
    uv run python -m shac.tables.findings --data-dir <private repo>

Output is aggregates only (no team names, single scores or single lines). It is meant
for a local run when TABLES.md is refreshed, not for CI.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from shac.tables.build import query_df
from shac.tables.check import open_tables

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_PATH = REPO_ROOT / "sql" / "tables" / "findings.sql"


def split_findings(sql_text: str) -> list[tuple[str, str]]:
    """Split the SQL file on '-- finding: <name>' markers into (name, query) pairs."""
    parts = re.split(r"^-- finding: (\S+)\s*$", sql_text, flags=re.MULTILINE)
    return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    args = parser.parse_args(argv)

    con, missing = open_tables(args.data_dir / "tables")
    if missing:
        print("missing tables:", ", ".join(missing))
        return 1
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        for name, query in split_findings(SQL_PATH.read_text(encoding="utf-8")):
            print(f"== {name}")
            print(query_df(con, query).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
