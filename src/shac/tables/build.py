"""Rebuild every private table from the raw files. Safe to run again: it overwrites.

Run as:
    uv run python -m shac.tables.build --data-dir <private repo> [--map-dir data]

Reads (private repo):  cbbd/raw/{games,lines,games-players}/*.json.gz, odds/raw/**/*.json.gz,
                       torvik/timemachine/**/*.json.gz, torvik/raw/**/*_team_results_*.csv.gz
Reads (public repo):   data/teams.csv, data/team_aliases.csv
Writes (private repo): tables/{games,results,player_box,cbbd_lines,odds_snapshots,
                       odds_event_matches,closing_lines,ratings_daily}.parquet

Prints counts only (no team names, scores or lines), so the output is safe for public CI
logs. No network calls.
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

import duckdb
import pandas as pd

from shac.tables import cbbd, odds, raw, torvik

REPO_ROOT = Path(__file__).resolve().parents[3]
CLOSING_SQL = REPO_ROOT / "sql" / "tables" / "closing_lines.sql"

# US bookmaker keys at The Odds API (docs/VERIFIED.md). Pinnacle and the other `eu`
# region books are kept in odds_snapshots but never enter the US median.
US_BOOKS = [
    "betonlineag",
    "betmgm",
    "betrivers",
    "betus",
    "bovada",
    "williamhill_us",
    "draftkings",
    "fanatics",
    "fanduel",
    "lowvig",
    "mybookieag",
]

TABLE_NAMES = [
    "games",
    "results",
    "player_box",
    "cbbd_lines",
    "odds_snapshots",
    "odds_event_matches",
    "closing_lines",
    "ratings_daily",
]


def load_map(map_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The public team map: teams.csv and team_aliases.csv."""
    teams = pd.read_csv(map_dir / "teams.csv")
    aliases = pd.read_csv(
        map_dir / "team_aliases.csv",
        dtype={"source": str, "alias": str, "valid_to_season": "Int64"},
        keep_default_na=False,
        na_values={"valid_to_season": [""]},
    )
    return teams, aliases


def query_df(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    """Run a query and return a DataFrame.

    DuckDB's pandas conversion triggers a pandas deprecation warning about `inplace`
    inside DuckDB itself; it is not ours to fix, so it is silenced here only.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The inplace keyword")
        return con.execute(sql).df()


def build_closing_lines(
    odds_snapshots: pd.DataFrame, snapshot_times: pd.Series, cbbd_lines: pd.DataFrame
) -> pd.DataFrame:
    """Run sql/tables/closing_lines.sql over the in-memory tables."""
    con = duckdb.connect()
    con.register("odds_snapshots", odds_snapshots)
    con.register("snapshot_files", pd.DataFrame({"snapshot_at": snapshot_times}))
    con.register("us_books", pd.DataFrame({"book": US_BOOKS}))
    con.register("cbbd_lines", cbbd_lines)
    return query_df(con, CLOSING_SQL.read_text(encoding="utf-8"))


def build_all(data_dir: Path, map_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build every table in memory. Returns the tables and a dict of counts to report."""
    teams, aliases = load_map(map_dir)
    report: dict = {}

    games_raw = raw.dedupe_files(raw.load_endpoint(data_dir, "games"), key=lambda g: g["id"])
    games = cbbd.build_games(games_raw, teams)
    results = cbbd.build_results(games_raw, games)
    report["games_files"] = (games_raw.n_raw, games_raw.n_duplicates)

    players_raw = raw.dedupe_files(
        raw.load_endpoint(data_dir, "games-players"), key=lambda r: (r["gameId"], r["teamId"])
    )
    player_box, pb_dropped = cbbd.build_player_box(players_raw, games)
    report["player_files"] = (players_raw.n_raw, players_raw.n_duplicates)
    report["player_box_dropped"] = pb_dropped

    lines_raw = raw.dedupe_files(raw.load_endpoint(data_dir, "lines"), key=lambda r: r["gameId"])
    cbbd_lines, lines_counts = cbbd.build_cbbd_lines(lines_raw, games)
    report["lines_files"] = (lines_raw.n_raw, lines_raw.n_duplicates)
    report["lines_counts"] = lines_counts

    odds_files = raw.load_odds_files(data_dir)
    flat, odds_counts = odds.flatten_snapshots(odds_files)
    matches = odds.match_events(flat, games, aliases)
    odds_snapshots = odds.attach_game_ids(flat, matches)
    report["odds_counts"] = odds_counts
    report["odds_files"] = len(odds_files)

    snapshot_times = cbbd.utc(pd.Series([w["fetched_at"] for _n, w in odds_files], dtype=str))
    closing = build_closing_lines(odds_snapshots, snapshot_times, cbbd_lines)

    ratings_daily, ratings_counts = torvik.build_ratings_daily(
        torvik.load_time_machine(data_dir), torvik.load_daily(data_dir), aliases
    )
    report["ratings_counts"] = ratings_counts

    tables = {
        "games": games,
        "results": results,
        "player_box": player_box,
        "cbbd_lines": cbbd_lines,
        "odds_snapshots": odds_snapshots,
        "odds_event_matches": matches,
        "closing_lines": closing,
        "ratings_daily": ratings_daily,
    }
    return tables, report


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write one table with DuckDB (timestamps stay UTC), replacing the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    con = duckdb.connect()
    con.register("t", df)
    target = tmp.as_posix().replace("'", "''")
    con.execute(f"COPY (SELECT * FROM t) TO '{target}' (FORMAT parquet)")
    con.close()
    os.replace(tmp, path)


def print_report(tables: dict[str, pd.DataFrame], report: dict) -> None:
    """Counts only. Never names, scores or lines."""
    for label, key in [("games", "games_files"), ("lines", "lines_files")]:
        n_raw, n_dup = report[key]
        print(f"raw {label} records: {n_raw} (duplicates dropped: {n_dup})")
    n_raw, n_dup = report["player_files"]
    print(f"raw team-game player records: {n_raw} (duplicates dropped: {n_dup})")
    for name, n in report["player_box_dropped"].items():
        print(f"player_box rows dropped, {name}: {n}")
    for name, n in report["lines_counts"].items():
        print(f"cbbd_lines {name}: {n}")
    print(f"odds snapshot files: {report['odds_files']}")
    for name, n in report["odds_counts"].items():
        print(f"odds_snapshots {name}: {n}")

    for name, n in report["ratings_counts"].items():
        print(f"ratings_daily {name}: {n}")

    for name in TABLE_NAMES:
        print(f"table {name}: {len(tables[name])} rows")

    games = tables["games"]
    season_of_game = games.set_index("game_id")["season"]
    per_season = pd.DataFrame(
        {
            "games": games.groupby("season").size(),
            "results": tables["results"]["game_id"].map(season_of_game).value_counts(),
            "player_box": tables["player_box"]["game_id"].map(season_of_game).value_counts(),
            "cbbd_lines": tables["cbbd_lines"]["game_id"].map(season_of_game).value_counts(),
        }
    )
    per_season = per_season.fillna(0).astype(int).sort_index()
    print("rows per season:")
    print(per_season.to_string())

    matches = tables["odds_event_matches"]
    print("odds events by match_type:")
    for match_type, n in matches["match_type"].value_counts().sort_index().items():
        print(f"  {match_type}: {n}")
    if len(matches):
        rate = (matches["match_type"] == "matched").mean()
        print(f"odds event match rate: {rate:.1%} of {len(matches)} events")
    closing = tables["closing_lines"]
    print("closing_lines by source and basis:")
    for (source, basis), n in closing.groupby(["source", "basis"]).size().items():
        print(f"  {source} / {basis}: {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)

    tables, report = build_all(args.data_dir, args.map_dir)
    out_dir = args.data_dir / "tables"
    for name in TABLE_NAMES:
        write_parquet(tables[name], out_dir / f"{name}.parquet")
    print_report(tables, report)
    print(f"wrote {len(TABLE_NAMES)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
