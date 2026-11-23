"""Build the historical-odds tables from the archive pull (docs/EARLY_LINE.md, section 4).

Run as (after shac.tables.build and shac.ingest.odds_history):
    uv run python -m shac.tables.odds_history --data-dir <private repo> [--map-dir data]

Reads (private repo): odds/history/**/odds_hist_*.json.gz, tables/games.parquet
Reads (public repo):  data/teams.csv, data/team_aliases.csv
Writes (private repo):
    tables/odds_history.parquet           one row per snapshot x event x book, with game_id
    tables/odds_history_matches.parquet   one row per archive event and its match
    tables/odds_history_requests.parquet  one row per file: kind, season, game day, times

Events are matched to games exactly as live snapshots are (shac.tables.odds). Prints
counts, check 1 of section 4 (share of final games with a matched call line, per season)
and the team names that didn't map, so aliases can be added. No network.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.tables import odds, raw
from shac.tables.build import REPO_ROOT, US_BOOKS, load_map, query_df, write_parquet
from shac.tables.cbbd import utc

FOLDER = Path("odds") / "history"
EASTERN = "America/New_York"
COVERAGE_BAR = 0.90
REQUEST_COLUMNS = [
    "file",
    "kind",
    "season",
    "game_day",
    "requested_at",
    "snapshot_at",
    "pulled_at",
    "n_events",
]


def load_history_files(data_dir: Path) -> list[tuple[str, dict]]:
    """Every archive file, as (file name, wrapper) pairs, oldest request first."""
    folder = data_dir / FOLDER
    return [(p.name, raw.read_wrapped(p)) for p in sorted(folder.rglob("odds_hist_*.json.gz"))]


def requests_table(files: list[tuple[str, dict]]) -> pd.DataFrame:
    rows = [
        {
            "file": name,
            "kind": w.get("kind"),
            "season": w.get("season"),
            "game_day": w.get("game_day"),
            "requested_at": w.get("requested_at"),
            "snapshot_at": w.get("fetched_at"),
            "pulled_at": w.get("pulled_at"),
            "n_events": len(w.get("data") or []),
        }
        for name, w in files
    ]
    df = pd.DataFrame(rows, columns=REQUEST_COLUMNS)
    for col in ["requested_at", "snapshot_at", "pulled_at"]:
        df[col] = utc(df[col])
    df["season"] = df["season"].astype("Int64")
    df["game_day"] = pd.to_datetime(df["game_day"]).dt.date
    return df


def build(files: list[tuple[str, dict]], games: pd.DataFrame, aliases: pd.DataFrame):
    """The three tables and the flatten counts."""
    flat, counts = odds.flatten_snapshots(files)
    matches = odds.match_events(flat, games, aliases)
    history = odds.attach_game_ids(flat, matches)
    return history, matches, requests_table(files), counts


def coverage(history: pd.DataFrame, requests: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Check 1: per season, final games with a US-book line in their `evening` snapshot."""
    seasons = requests["season"].dropna().unique()
    g = games[(games["status"] == "final") & games["season"].isin(seasons)].copy()
    g["game_day"] = pd.to_datetime(g["tip_at"], utc=True).dt.tz_convert(EASTERN).dt.date
    evening = requests[requests["kind"] == "evening"][["game_day", "snapshot_at"]]
    lines = history[
        history["book"].isin(US_BOOKS) & history["home_spread"].notna() & history["game_id"].notna()
    ][["snapshot_at", "game_id"]].drop_duplicates()
    lines = lines.assign(has_call_line=True)
    lines["game_id"] = lines["game_id"].astype("int64")
    g["game_id"] = g["game_id"].astype("int64")
    out = g.merge(evening, on="game_day", how="left").merge(
        lines, on=["snapshot_at", "game_id"], how="left"
    )
    out["has_call_line"] = out["has_call_line"].fillna(False).astype(bool)
    per = out.groupby("season").agg(
        final_games=("game_id", "size"), with_call_line=("has_call_line", "sum")
    )
    per["share"] = per["with_call_line"] / per["final_games"]
    per["passes"] = per["share"] >= COVERAGE_BAR
    return per.reset_index()


def unmapped_names(matches: pd.DataFrame, history: pd.DataFrame) -> pd.Series:
    """Odds API team names in unmapped events, with how many events each is in."""
    events = matches[matches["match_type"] == "unmapped_team"]["odds_event_id"]
    latest = history[history["odds_event_id"].isin(events)].drop_duplicates("odds_event_id")
    names = pd.concat([latest["home_team"], latest["away_team"]])
    return names.value_counts()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)

    files = load_history_files(args.data_dir)
    if not files:
        print("no archive files under odds/history yet")
        return 1
    games_path = (args.data_dir / "tables" / "games.parquet").as_posix().replace("'", "''")
    con = duckdb.connect()
    games = query_df(con, f"SELECT * FROM read_parquet('{games_path}')")
    con.close()
    _teams, aliases = load_map(args.map_dir)

    history, matches, requests, counts = build(files, games, aliases)
    out = args.data_dir / "tables"
    write_parquet(history, out / "odds_history.parquet")
    write_parquet(matches, out / "odds_history_matches.parquet")
    write_parquet(requests, out / "odds_history_requests.parquet")

    print(f"archive files: {len(files)}; rows: {len(history)}; flatten counts: {counts}")
    print("archive events by match_type:")
    print(matches["match_type"].value_counts().sort_index().to_string())
    print("\ncheck 1, coverage (final games with a matched call line):")
    print(coverage(history, requests, games).to_string(index=False))
    names = unmapped_names(matches, history)
    if len(names):
        print(f"\nunmapped team names ({len(names)}), most events first:")
        print(names.head(60).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
