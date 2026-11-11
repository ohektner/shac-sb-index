"""Test when a Torvik Time Machine file is known (task 1.4). Prints aggregates only.

Run as (after `shac.tables.build`):
    uv run python -m shac.tables.timemachine_check --data-dir <private repo>

Runs sql/checks/timemachine_asof.sql. Torvik's W-L record is the clock: comparing how
many games a file counts with when CBBD says each game tipped shows which games a file
already includes. Results and how to read them: docs/TORVIK.md and the 2026-11-11 entry
in docs/DECISIONS.md. Local run only (needs the private data), not part of CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.tables import raw
from shac.tables.build import REPO_ROOT, query_df
from shac.tables.findings import split_findings

SQL_PATH = REPO_ROOT / "sql" / "checks" / "timemachine_asof.sql"


def team_games_any_opponent(data_dir: Path, teams: pd.DataFrame) -> pd.DataFrame:
    """One row per final CBBD game per D-I team in it, whatever the opponent's division.

    The `games` table keeps only D-I vs D-I games, but Torvik's records count games
    against non-D-I teams too, so this reads the raw CBBD files directly.
    """
    deduped = raw.dedupe_files(raw.load_endpoint(data_dir, "games"), key=lambda g: g["id"])
    di = {
        (int(tid), season)
        for tid, first, last in zip(
            teams["team_id"], teams["first_season"], teams["last_season"], strict=True
        )
        for season in range(int(first), int(last) + 1)
    }
    rows = []
    for _fetched, g in deduped.records:
        if str(g.get("status", "")).lower() != "final":
            continue
        season = int(g["season"])
        for side in ("homeTeamId", "awayTeamId"):
            tid = g.get(side)
            if tid is not None and (int(tid), season) in di:
                rows.append((season, int(g["id"]), int(tid), g["startDate"]))
    df = pd.DataFrame(rows, columns=["season", "game_id", "team_id", "tip_at"])
    df["tip_at"] = pd.to_datetime(df["tip_at"], utc=True)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)

    teams = pd.read_csv(args.map_dir / "teams.csv")
    ratings_path = (args.data_dir / "tables" / "ratings_daily.parquet").as_posix()
    con = duckdb.connect()
    con.execute(
        "CREATE VIEW tm AS "
        "SELECT season, rating_date, team_id, wins + losses AS games, as_of "
        f"FROM read_parquet('{ratings_path}') WHERE source = 'torvik_time_machine'"
    )
    con.register("team_games", team_games_any_opponent(args.data_dir, teams))
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        for name, query in split_findings(SQL_PATH.read_text(encoding="utf-8")):
            print(f"== {name}")
            print(query_df(con, query).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
