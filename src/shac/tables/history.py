"""`game_history`: every D-I game with point-in-time ratings, result and closes (task 1.6).

Built by sql/tables/game_history.sql from the other private tables, as the last step of
`shac.tables.build`. It is the one table the baseline and the model read history from.

Columns come in three groups, and only the first may ever feed a prediction:

- FEATURE_COLUMNS: known before tip. Their `as_of` is `ratings_as_of`, and
  tests/test_no_leakage.py checks it is strictly before `tip_at` for every row.
- LABEL_COLUMNS: the game's result and final status. What the model is trained and
  scored against.
- BENCHMARK_COLUMNS: closing lines. Used to score against the market, never as inputs
  (no-leakage rule 5).

KEY_COLUMNS describe the game itself (teams, tip time, neutral site) and are known when
the schedule is.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from shac.tables.build import REPO_ROOT, query_df

SQL_PATH = REPO_ROOT / "sql" / "tables" / "game_history.sql"

KEY_COLUMNS = [
    "game_id",
    "season",
    "season_type",
    "tip_at",
    "tip_tbd",
    "neutral_site",
    "conference_game",
    "home_team_id",
    "away_team_id",
]
FEATURE_COLUMNS = [
    "ratings_source",
    "rating_date",
    "ratings_as_of",
    "file_n_teams",
    "file_mean_tempo",
    "home_adj_o",
    "home_adj_d",
    "home_adj_tempo",
    "home_barthag",
    "home_rank",
    "away_adj_o",
    "away_adj_d",
    "away_adj_tempo",
    "away_barthag",
    "away_rank",
]
# `status` (final, cancelled, postponed) is only known after the game, so it sits here.
LABEL_COLUMNS = ["status", "home_points", "away_points", "home_margin", "overtimes"]
BENCHMARK_COLUMNS = [
    "cbbd_close_home_spread",
    "cbbd_open_home_spread",
    "cbbd_close_basis",
    "own_close_home_spread",
    "pinnacle_close_home_spread",
]
HISTORY_COLUMNS = KEY_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS + BENCHMARK_COLUMNS


def build_game_history(
    games: pd.DataFrame,
    results: pd.DataFrame,
    ratings_daily: pd.DataFrame,
    closing_lines: pd.DataFrame,
) -> pd.DataFrame:
    """Run sql/tables/game_history.sql over the four tables."""
    con = duckdb.connect()
    con.register("games", games)
    con.register("results", results)
    con.register("ratings_daily", ratings_daily)
    con.register("closing_lines", closing_lines)
    out = query_df(con, SQL_PATH.read_text(encoding="utf-8"))
    con.close()
    # DuckDB hands DATE columns to pandas as timestamps; keep rating_date a plain date
    # (empty where a game has no ratings file).
    dates = pd.to_datetime(out["rating_date"])
    out["rating_date"] = [d.date() if pd.notna(d) else None for d in dates]
    return out[HISTORY_COLUMNS]


def history_counts(history: pd.DataFrame) -> dict[str, int]:
    """Counts for the build printout: how many games lack a ratings file or a team."""
    no_file = history["rating_date"].isna()
    missing_team = ~no_file & (history["home_adj_o"].isna() | history["away_adj_o"].isna())
    # Days between the game's US Eastern date and the ratings file's date.
    tip_day = pd.to_datetime(history["tip_at"].dt.tz_convert("America/New_York").dt.date)
    age = (tip_day - pd.to_datetime(history["rating_date"])).dt.days
    return {
        "games": len(history),
        "no_ratings_file": int(no_file.sum()),
        "team_missing_from_file": int(missing_team.sum()),
        "file_dated_day_before": int((age == 1).sum()),
        "file_two_plus_days_old": int((age >= 2).sum()),
        "with_result": int(history["home_margin"].notna().sum()),
        "with_cbbd_close": int(history["cbbd_close_home_spread"].notna().sum()),
    }
