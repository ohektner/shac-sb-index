"""Run the private-table SQL checks and print how many problem rows each one finds.

Run as:
    uv run python -m shac.tables.check --data-dir <private repo>

Prints counts only (no names, scores or lines), so the output is safe for public CI logs.
Exits 1 if any check finds a row, or if a table file is missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from shac.tables.build import TABLE_NAMES
from shac.teams.check import split_checks

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_PATH = REPO_ROOT / "sql" / "checks" / "tables.sql"


def open_tables(tables_dir: Path) -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    """One DuckDB view per parquet table. Returns the connection and any missing tables."""
    con = duckdb.connect()
    missing = []
    for name in TABLE_NAMES:
        path = tables_dir / f"{name}.parquet"
        if not path.exists():
            missing.append(name)
            continue
        target = path.as_posix().replace("'", "''")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{target}')")
    return con, missing


def run_checks(con: duckdb.DuckDBPyConnection, sql_text: str) -> list[tuple[str, int]]:
    """(check name, number of problem rows) for every check in the SQL file."""
    return [(name, len(con.execute(query).fetchall())) for name, query in split_checks(sql_text)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    args = parser.parse_args(argv)

    con, missing = open_tables(args.data_dir / "tables")
    for name in missing:
        print(f"missing table: {name}")
    failed = bool(missing)
    if not missing:
        for name, n in run_checks(con, SQL_PATH.read_text(encoding="utf-8")):
            print(f"{name}: {n}")
            failed |= n > 0
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
