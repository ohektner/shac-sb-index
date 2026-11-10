"""Run the team-map SQL checks and print how many problem rows each one finds.

Run as:
    uv run python -m shac.teams.check --data-dir <private repo> [--map-dir data]

Prints counts only (no names or raw data), so the output is safe for public CI logs.
Exits 1 if any check other than (d)/(e) finds rows, or if (d)/(e) find a name that is
not waiting in the review file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.teams import sources

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_PATH = REPO_ROOT / "sql" / "checks" / "team_map.sql"
REVIEWABLE = {"d_torvik_names_unmatched", "e_odds_names_unmatched"}


def split_checks(sql_text: str) -> list[tuple[str, str]]:
    """Split the SQL file on '-- check: <name>' markers into (name, query) pairs."""
    parts = re.split(r"^-- check: (\S+)\s*$", sql_text, flags=re.MULTILINE)
    return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def load_tables(con: duckdb.DuckDBPyConnection, data_dir: Path, map_dir: Path) -> None:
    """Register the map CSVs and the raw-file tables the SQL expects."""
    teams = pd.read_csv(map_dir / "teams.csv")
    aliases = pd.read_csv(
        map_dir / "team_aliases.csv",
        dtype={"source": str, "alias": str, "valid_to_season": "Int64"},
        keep_default_na=False,
        na_values={"valid_to_season": [""]},
    )
    review = pd.read_csv(map_dir / "team_map_review.csv", dtype=str, keep_default_na=False)
    cbbd = sources.load_cbbd_teams(data_dir)[["season", "team_id"]]
    tables = {
        "teams": teams,
        "aliases": aliases,
        "review": review,
        "cbbd_team_seasons": cbbd,
        "cbbd_games": sources.load_cbbd_games(data_dir),
        "torvik_names": sources.load_torvik_names(data_dir),
        "odds_names": sources.load_odds_names(data_dir),
    }
    for name, df in tables.items():
        con.register(name, df)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)

    con = duckdb.connect()
    load_tables(con, args.data_dir, args.map_dir)
    failed = False
    for name, query in split_checks(SQL_PATH.read_text(encoding="utf-8")):
        result = con.execute(query).df()
        if name in REVIEWABLE:
            in_review = int(result["in_review"].sum()) if len(result) else 0
            not_in_review = len(result) - in_review
            print(f"{name}: {len(result)} (in review file: {in_review}, not: {not_in_review})")
            failed |= not_in_review > 0
        else:
            print(f"{name}: {len(result)}")
            failed |= len(result) > 0
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
