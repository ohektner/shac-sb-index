"""`rotation_players`: rotation membership before each game, and who played (G2 truth).

Built by sql/tables/rotation_players.sql from `games` and `player_box`, as part of
`shac.tables.build`. One row per game, team and player already listed for that team
earlier in the season. The point-in-time columns (`*_before`, `rotation_rank`,
`is_rotation`) use only earlier games; the outcome columns (`played`, `minutes`,
`starter`) are this game's box score and never feed anything before tip.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from shac.tables.build import REPO_ROOT, query_df

SQL_PATH = REPO_ROOT / "sql" / "tables" / "rotation_players.sql"

POINT_IN_TIME_COLUMNS = [
    "team_games_before",
    "games_played_before",
    "minutes_before",
    "mpg_before",
    "games_sat_in_a_row_before",
    "rotation_rank",
    "is_rotation",
]
OUTCOME_COLUMNS = ["played", "minutes", "starter"]


def build_rotation_players(games: pd.DataFrame, player_box: pd.DataFrame) -> pd.DataFrame:
    """Run sql/tables/rotation_players.sql over `games` and `player_box`."""
    con = duckdb.connect()
    con.register("games", games)
    con.register("player_box", player_box)
    out = query_df(con, SQL_PATH.read_text(encoding="utf-8"))
    con.close()
    out["rotation_rank"] = out["rotation_rank"].astype("Int64")
    return out


def rotation_counts(rotation: pd.DataFrame) -> dict[str, int]:
    """Counts for the build printout."""
    rot = rotation[rotation["is_rotation"]]
    return {
        "rows": len(rotation),
        "team_games": int(rotation[["game_id", "team_id"]].drop_duplicates().shape[0]),
        "rotation_rows": len(rot),
        "rotation_sat": int((~rot["played"]).sum()),
        "rotation_sat_new_absence": int(
            ((~rot["played"]) & (rot["games_sat_in_a_row_before"] == 0)).sum()
        ),
    }
