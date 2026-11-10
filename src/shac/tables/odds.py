"""Flatten Odds API snapshot files and match each event to a CBBD game.

`odds_snapshots` has one row per snapshot x event x book (spreads market only).
`odds_event_matches` has one row per Odds API event: the team ids its names map to and
how (or whether) it matched a game in `games`.

Matching rule: same home team id, same away team id, and the tip dates (UTC) no more than
one day apart. If the only game found has the teams the other way round, that is a
home/away disagreement between the two sources. It is reported (match_type `swapped`)
and the event's `game_id` is left empty: the book's home spread would have the wrong
sign for that game, so it must not be used without a person looking at it.
"""

from __future__ import annotations

import pandas as pd

from shac.tables.cbbd import utc
from shac.teams.normalize import season_of

SNAPSHOT_COLUMNS = [
    "snapshot_at",
    "odds_event_id",
    "book",
    "commence_at",
    "home_team",
    "away_team",
    "home_spread",
    "home_price",
    "away_price",
    "market_last_update_at",
    "game_id",
]
MATCH_COLUMNS = [
    "odds_event_id",
    "commence_at",
    "season",
    "home_team_id",
    "away_team_id",
    "match_type",
    "game_id",
    "candidate_game_id",
]
MAX_TIP_DAYS_APART = 1


def flatten_snapshots(files: list[tuple[str, dict]]) -> tuple[pd.DataFrame, dict]:
    """One row per snapshot x event x book from the raw snapshot files.

    The home spread is the `point` of the outcome named like the event's home team. If no
    outcome carries the home team's name the row is kept with an empty spread and counted.
    """
    rows = []
    counts = {"no_home_outcome": 0, "duplicate_rows": 0}
    for _name, wrapped in files:
        snapshot_at = wrapped["fetched_at"]
        for event in wrapped.get("data") or []:
            for bookmaker in event.get("bookmakers") or []:
                for market in bookmaker.get("markets") or []:
                    if market.get("key") != "spreads":
                        continue
                    outcomes = {o.get("name"): o for o in market.get("outcomes") or []}
                    home = outcomes.get(event["home_team"])
                    away = outcomes.get(event["away_team"])
                    if home is None:
                        counts["no_home_outcome"] += 1
                    rows.append(
                        {
                            "snapshot_at": snapshot_at,
                            "odds_event_id": event["id"],
                            "book": bookmaker["key"],
                            "commence_at": event["commence_time"],
                            "home_team": event["home_team"],
                            "away_team": event["away_team"],
                            "home_spread": None if home is None else home.get("point"),
                            "home_price": None if home is None else home.get("price"),
                            "away_price": None if away is None else away.get("price"),
                            "market_last_update_at": market.get("last_update"),
                        }
                    )
    df = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS[:-1])
    before = len(df)
    df = df.drop_duplicates(["snapshot_at", "odds_event_id", "book"], keep="last")
    counts["duplicate_rows"] = before - len(df)
    for col in ["snapshot_at", "commence_at", "market_last_update_at"]:
        df[col] = utc(df[col])
    df["home_spread"] = df["home_spread"].astype("Float64")
    for col in ["home_price", "away_price"]:
        df[col] = df[col].astype("Int64")
    return df.reset_index(drop=True), counts


def resolve_odds_name(aliases: pd.DataFrame, name: str, season: int) -> int | None:
    """CBBD team id for an Odds API team name in a season, or None if not exactly one."""
    hits = aliases[
        (aliases["source"] == "odds_api")
        & (aliases["alias"] == name)
        & (aliases["valid_from_season"] <= season)
        & (aliases["valid_to_season"].fillna(9999) >= season)
    ]["team_id"].unique()
    return int(hits[0]) if len(hits) == 1 else None


def _nearest_game(candidates: pd.DataFrame, commence_at: pd.Timestamp) -> int | None:
    if candidates.empty:
        return None
    days_apart = (candidates["tip_at"].dt.normalize() - commence_at.normalize()).abs()
    close = candidates[days_apart <= pd.Timedelta(days=MAX_TIP_DAYS_APART)]
    if close.empty:
        return None
    gap = (close["tip_at"] - commence_at).abs()
    return int(close.loc[gap.idxmin(), "game_id"])


def match_events(
    snapshots: pd.DataFrame, games: pd.DataFrame, aliases: pd.DataFrame
) -> pd.DataFrame:
    """One row per Odds API event with its match to `games`.

    match_type is one of:
      `matched`       same home and away team, tip dates within a day; game_id is set
      `swapped`       only a game with home and away the other way round was found
      `no_game`       both names map to teams, but no game is in `games` (for example
                      2026-27, whose CBBD games are not ingested yet)
      `unmapped_team` a team name did not map to exactly one team id
    The event's names and tip time come from its most recent snapshot.
    """
    if snapshots.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    latest = snapshots.sort_values("snapshot_at").drop_duplicates("odds_event_id", keep="last")
    rows = []
    for ev in latest.itertuples(index=False):
        season = season_of(ev.commence_at.to_pydatetime())
        home_id = resolve_odds_name(aliases, ev.home_team, season)
        away_id = resolve_odds_name(aliases, ev.away_team, season)
        row = {
            "odds_event_id": ev.odds_event_id,
            "commence_at": ev.commence_at,
            "season": season,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "match_type": "unmapped_team",
            "game_id": None,
            "candidate_game_id": None,
        }
        if home_id is not None and away_id is not None:
            same = games[(games["home_team_id"] == home_id) & (games["away_team_id"] == away_id)]
            flipped = games[(games["home_team_id"] == away_id) & (games["away_team_id"] == home_id)]
            exact = _nearest_game(same, ev.commence_at)
            swapped = _nearest_game(flipped, ev.commence_at)
            if exact is not None:
                row.update(match_type="matched", game_id=exact)
            elif swapped is not None:
                row.update(match_type="swapped", candidate_game_id=swapped)
            else:
                row["match_type"] = "no_game"
        rows.append(row)
    df = pd.DataFrame(rows, columns=MATCH_COLUMNS)
    for col in ["season", "home_team_id", "away_team_id", "game_id", "candidate_game_id"]:
        df[col] = df[col].astype("Int64")
    return df.sort_values("odds_event_id").reset_index(drop=True)


def attach_game_ids(snapshots: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Add `game_id` to every snapshot row from the event's match (empty if not matched)."""
    out = snapshots.merge(matches[["odds_event_id", "game_id"]], on="odds_event_id", how="left")
    out["game_id"] = out["game_id"].astype("Int64")
    out = out[SNAPSHOT_COLUMNS]
    return out.sort_values(["snapshot_at", "odds_event_id", "book"]).reset_index(drop=True)
