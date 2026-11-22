"""The wrong-team line check in sql/tables/game_history.sql (DECISIONS.md 2026-11-22).

A CBBD close is dropped when it is 10+ points from a plain Torvik number for the game and
its mirror image (same number, other team) is at least 3 times closer. Made-up teams:
every team has adj_d 100 and tempo 70, so rating_margin = (adj_o_home - adj_o_away) * 0.7,
plus 3.4 at a true home game.
"""

from __future__ import annotations

import pandas as pd

from shac.tables.history import build_game_history
from shac.tables.torvik import time_machine_as_of

UTC_FETCH = pd.Timestamp("2026-11-10T21:30:00Z")
TIP = "2018-11-07T00:00:00Z"  # after the 2018-11-05 file is known (06:00 ET Nov 6)

# team_id -> adj_o. Team 9 is not in the file.
ADJ_O = {1: 120.0, 2: 100.0, 5: 106.0}

# (game_id, home, away, neutral_site, cbbd close, what should happen)
CASES = [
    # Torvik: home by 14.0 at a neutral site.
    (1, 1, 2, True, 13.5, "dropped"),  # the same number on the away team: wrong team
    (2, 1, 2, True, -13.5, "kept"),  # agrees
    (3, 1, 2, True, 2.0, "kept"),  # 16 off, but the mirror is 12 off: a real disagreement
    # Torvik: home by 14.0 + 3.4 = 17.4 at the home team's floor.
    (4, 1, 2, False, 17.0, "dropped"),
    (5, 1, 2, False, -17.0, "kept"),
    # Torvik: home by 4.2. Mirror is closer, but only 8.2 off: too small to tell.
    (6, 5, 2, True, 4.0, "kept"),
    # Unknown site: no Torvik number, no check.
    (7, 1, 2, None, 13.5, "kept"),
    # A team missing from the ratings file: no check.
    (8, 9, 2, True, 13.5, "kept"),
]


def ratings() -> pd.DataFrame:
    df = pd.DataFrame({"team_id": list(ADJ_O), "adj_o": list(ADJ_O.values())})
    df["season"] = 2019
    dates = pd.to_datetime(pd.Series(["2018-11-05"] * len(df)))
    df["rating_date"] = dates.dt.date
    df["source"] = "torvik_time_machine"
    df["adj_d"] = 100.0
    df["adj_tempo"] = 70.0
    df["barthag"] = 0.5
    df["rank"] = df["team_id"]
    df["wins"] = 0
    df["losses"] = 0
    df["as_of"] = time_machine_as_of(dates).to_numpy()
    df["fetched_at"] = UTC_FETCH
    return df


def games() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "game_id": [c[0] for c in CASES],
            "season": 2019,
            "tip_at": pd.to_datetime([TIP] * len(CASES), utc=True),
            "home_team_id": [c[1] for c in CASES],
            "away_team_id": [c[2] for c in CASES],
            "neutral_site": pd.array([c[3] for c in CASES], dtype="boolean"),
        }
    )
    df["season_type"] = "regular"
    df["tip_tbd"] = False
    df["conference_game"] = False
    df["status"] = "final"
    df["fetched_at"] = UTC_FETCH
    return df


def results(home_margin: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": [c[0] for c in CASES],
            "home_points": 70 + home_margin,
            "away_points": 70,
            "home_margin": home_margin,
            "overtimes": 0,
            "fetched_at": UTC_FETCH,
        }
    )


def closes() -> pd.DataFrame:
    n = len(CASES)
    cbbd = pd.DataFrame(
        {
            "game_id": [c[0] for c in CASES],
            "source": "cbbd",
            "close_home_spread": [c[4] for c in CASES],
            "close_snapshot_at": pd.Series([pd.NaT] * n, dtype="datetime64[ns, UTC]"),
            "minutes_before_tip": pd.Series([None] * n, dtype="Int32"),
            "n_books": 2,
            "open_home_spread": [c[4] + 0.5 for c in CASES],
            "open_snapshot_at": pd.Series([pd.NaT] * n, dtype="datetime64[ns, UTC]"),
            "basis": "median_cbbd_providers",
        }
    )
    # Our own snapshot close for game 1 is matched by team name and is never touched.
    own = cbbd.iloc[[0]].assign(
        source="own_snapshots", close_home_spread=-14.0, basis="median_us_books"
    )
    return pd.concat([cbbd, own], ignore_index=True)


def build(home_margin: int = 5) -> pd.DataFrame:
    return build_game_history(games(), results(home_margin), ratings(), closes()).set_index(
        "game_id"
    )


def test_wrong_team_lines_are_dropped_and_others_kept():
    h = build()
    for gid, *_, close, expect in CASES:
        row = h.loc[gid]
        if expect == "dropped":
            assert row["cbbd_close_basis"] == "dropped_wrong_team", gid
            assert pd.isna(row["cbbd_close_home_spread"]), gid
            assert pd.isna(row["cbbd_open_home_spread"]), gid
        else:
            assert row["cbbd_close_basis"] == "median_cbbd_providers", gid
            assert row["cbbd_close_home_spread"] == close, gid
            assert row["cbbd_open_home_spread"] == close + 0.5, gid


def test_own_snapshot_close_is_never_touched():
    h = build()
    assert h.loc[1, "own_close_home_spread"] == -14.0


def test_check_never_reads_the_result():
    a = build(home_margin=30)
    b = build(home_margin=-30)
    cols = ["cbbd_close_home_spread", "cbbd_open_home_spread", "cbbd_close_basis"]
    pd.testing.assert_frame_equal(a[cols], b[cols])
