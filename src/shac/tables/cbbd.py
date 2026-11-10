"""Turn de-duplicated CBBD records into the games, results, player_box and cbbd_lines tables.

Only games between two D-I teams are kept. A team is D-I in a season when that season is
inside its `first_season`..`last_season` range in `data/teams.csv`.

All timestamps are pandas UTC timestamps, so they are written to parquet as UTC.
"""

from __future__ import annotations

import pandas as pd

from shac.tables.raw import Deduped

GAMES_COLUMNS = [
    "game_id",
    "season",
    "season_type",
    "tip_at",
    "tip_tbd",
    "home_team_id",
    "away_team_id",
    "neutral_site",
    "conference_game",
    "status",
    "fetched_at",
]
RESULTS_COLUMNS = [
    "game_id",
    "home_points",
    "away_points",
    "home_margin",
    "overtimes",
    "fetched_at",
]
PLAYER_BOX_COLUMNS = [
    "game_id",
    "team_id",
    "athlete_id",
    "player_name",
    "minutes",
    "played",
    "starter",
    "fetched_at",
]
CBBD_LINES_COLUMNS = [
    "game_id",
    "provider",
    "home_spread",
    "home_spread_open",
    "total",
    "total_open",
    "home_moneyline",
    "away_moneyline",
    "suspect_sign",
    "fetched_at",
]

# Men's college games have two halves; every extra period is an overtime.
REGULATION_PERIODS = 2


def utc(values: pd.Series) -> pd.Series:
    """Parse ISO 8601 strings ("...Z" or "+00:00") into UTC timestamps."""
    return pd.to_datetime(values, utc=True, format="ISO8601")


def is_di(teams: pd.DataFrame, team_ids: pd.Series, seasons: pd.Series) -> pd.Series:
    """True where the team was D-I that season, per the ranges in `teams.csv`."""
    ranges = teams.set_index("team_id")[["first_season", "last_season"]]
    first = team_ids.map(ranges["first_season"])
    last = team_ids.map(ranges["last_season"])
    return (first.notna() & (seasons >= first) & (seasons <= last)).fillna(False).astype(bool)


def overtimes_from_periods(home_periods, away_periods) -> int | None:
    """Number of overtimes, from the per-period point lists.

    Two periods = no overtime, three = one overtime, and so on. If the lists are missing,
    shorter than two halves, or disagree in length, we don't know, so return None.
    """
    home = home_periods or []
    away = away_periods or []
    if len(home) != len(away) or len(home) < REGULATION_PERIODS:
        return None
    return len(home) - REGULATION_PERIODS


def build_games(deduped: Deduped, teams: pd.DataFrame) -> pd.DataFrame:
    """One row per D-I vs D-I game (game_id = CBBD game id)."""
    rows = [
        {
            "game_id": g["id"],
            "season": g["season"],
            "season_type": g.get("seasonType"),
            "tip_at": g.get("startDate"),
            "tip_tbd": g.get("startTimeTbd"),
            "home_team_id": g.get("homeTeamId"),
            "away_team_id": g.get("awayTeamId"),
            "neutral_site": g.get("neutralSite"),
            "conference_game": g.get("conferenceGame"),
            "status": g.get("status"),
            "fetched_at": fetched_at,
        }
        for fetched_at, g in deduped.records
    ]
    df = pd.DataFrame(rows, columns=GAMES_COLUMNS)
    if df.empty:
        return _typed_games(df)
    keep = is_di(teams, df["home_team_id"], df["season"]) & is_di(
        teams, df["away_team_id"], df["season"]
    )
    return _typed_games(df[keep])


def _typed_games(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["game_id", "season", "home_team_id", "away_team_id"]:
        df[col] = df[col].astype("Int64")
    for col in ["tip_tbd", "neutral_site", "conference_game"]:
        df[col] = df[col].astype("boolean")
    df["tip_at"] = utc(df["tip_at"])
    df["fetched_at"] = utc(df["fetched_at"])
    return df.sort_values("game_id").reset_index(drop=True)


def build_results(deduped: Deduped, games: pd.DataFrame) -> pd.DataFrame:
    """One row per final D-I game that has both scores.

    `home_margin` is home points minus away points. Overtimes come from the number of
    periods in the period-points lists (see `overtimes_from_periods`).
    """
    final_ids = set(games.loc[games["status"] == "final", "game_id"].astype(int))
    rows = []
    for fetched_at, g in deduped.records:
        if g["id"] not in final_ids:
            continue
        home, away = g.get("homePoints"), g.get("awayPoints")
        if home is None or away is None:
            continue
        rows.append(
            {
                "game_id": g["id"],
                "home_points": home,
                "away_points": away,
                "home_margin": home - away,
                "overtimes": overtimes_from_periods(
                    g.get("homePeriodPoints"), g.get("awayPeriodPoints")
                ),
                "fetched_at": fetched_at,
            }
        )
    df = pd.DataFrame(rows, columns=RESULTS_COLUMNS)
    for col in ["game_id", "home_points", "away_points", "home_margin", "overtimes"]:
        df[col] = df[col].astype("Int64")
    df["fetched_at"] = utc(df["fetched_at"])
    return df.sort_values("game_id").reset_index(drop=True)


def did_play(minutes) -> bool:
    """The did-not-play rule: 0 or missing minutes means the player did not play."""
    return minutes is not None and not pd.isna(minutes) and minutes > 0


def build_player_box(deduped: Deduped, games: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per player per team per D-I game.

    Rows for games not in `games` (non-D-I opponents) are dropped. `minutes` is kept as
    CBBD gives it; `played` applies the did-not-play rule. Returns the table and counts of
    rows dropped (no athlete id, or the same athlete listed twice for a team in a game).
    """
    game_ids = set(games["game_id"].astype(int))
    rows = []
    dropped = {"no_athlete_id": 0, "duplicate_athlete": 0}
    for fetched_at, team_game in deduped.records:
        if team_game["gameId"] not in game_ids:
            continue
        for p in team_game.get("players") or []:
            if p.get("athleteId") is None:
                dropped["no_athlete_id"] += 1
                continue
            minutes = p.get("minutes")
            rows.append(
                {
                    "game_id": team_game["gameId"],
                    "team_id": team_game["teamId"],
                    "athlete_id": p["athleteId"],
                    "player_name": p.get("name"),
                    "minutes": None if minutes is None else float(minutes),
                    "played": did_play(minutes),
                    "starter": p.get("starter"),
                    "fetched_at": fetched_at,
                }
            )
    df = pd.DataFrame(rows, columns=PLAYER_BOX_COLUMNS)
    before = len(df)
    df = df.drop_duplicates(["game_id", "team_id", "athlete_id"], keep="last")
    dropped["duplicate_athlete"] = before - len(df)
    for col in ["game_id", "team_id", "athlete_id"]:
        df[col] = df[col].astype("Int64")
    df["minutes"] = df["minutes"].astype("Float64")
    df["played"] = df["played"].astype("boolean")
    df["starter"] = df["starter"].astype("boolean")
    df["fetched_at"] = utc(df["fetched_at"])
    df = df.sort_values(["game_id", "team_id", "athlete_id"]).reset_index(drop=True)
    return df, dropped


def suspect_sign(spread, home_moneyline, away_moneyline) -> bool:
    """True when a line's spread favorite and moneyline favorite are different teams.

    The spread favorite is home when the spread is negative. The moneyline favorite is
    the side with the lower (more negative) price. False when anything is missing, the
    spread is 0, or the two moneylines are equal, since then there is no favorite to compare.
    """
    values = [spread, home_moneyline, away_moneyline]
    if any(v is None or pd.isna(v) for v in values):
        return False
    if spread == 0 or home_moneyline == away_moneyline:
        return False
    return (spread < 0) != (home_moneyline < away_moneyline)


def build_cbbd_lines(deduped: Deduped, games: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per D-I game per line provider.

    CBBD's `spread` is the home team's spread (negative = home favored); the findings in
    docs/TABLES.md check this on every season. A lines record whose home/away teams differ
    from the games table is not used, and is counted instead of being flipped.
    """
    teams_by_game = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    home_of = teams_by_game["home_team_id"].to_dict()
    away_of = teams_by_game["away_team_id"].to_dict()
    rows = []
    counts = {"team_mismatch_games": 0, "duplicate_provider": 0}
    for fetched_at, rec in deduped.records:
        gid = rec["gameId"]
        if gid not in home_of:
            continue
        if rec.get("homeTeamId") != home_of[gid] or rec.get("awayTeamId") != away_of[gid]:
            counts["team_mismatch_games"] += 1
            continue
        for line in rec.get("lines") or []:
            rows.append(
                {
                    "game_id": gid,
                    "provider": line.get("provider"),
                    "home_spread": line.get("spread"),
                    "home_spread_open": line.get("spreadOpen"),
                    "total": line.get("overUnder"),
                    "total_open": line.get("overUnderOpen"),
                    "home_moneyline": line.get("homeMoneyline"),
                    "away_moneyline": line.get("awayMoneyline"),
                    "suspect_sign": suspect_sign(
                        line.get("spread"), line.get("homeMoneyline"), line.get("awayMoneyline")
                    ),
                    "fetched_at": fetched_at,
                }
            )
    df = pd.DataFrame(rows, columns=CBBD_LINES_COLUMNS)
    before = len(df)
    df = df.drop_duplicates(["game_id", "provider"], keep="last")
    counts["duplicate_provider"] = before - len(df)
    df["game_id"] = df["game_id"].astype("Int64")
    for col in ["home_spread", "home_spread_open", "total", "total_open"]:
        df[col] = df[col].astype("Float64")
    for col in ["home_moneyline", "away_moneyline"]:
        df[col] = df[col].astype("Int64")
    df["suspect_sign"] = df["suspect_sign"].astype(bool)
    df["fetched_at"] = utc(df["fetched_at"])
    df = df.sort_values(["game_id", "provider"]).reset_index(drop=True)
    return df, counts
