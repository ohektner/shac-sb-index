"""Tests for `rotation_players` (the G2 ground truth). Made-up teams, players and minutes."""

import pandas as pd

from shac.tables.rotation import POINT_IN_TIME_COLUMNS, build_rotation_players


def make_games(n_games: int, team_ids=(1, 2)) -> pd.DataFrame:
    tips = pd.date_range("2025-11-05T00:00:00Z", periods=n_games, freq="3D")
    return pd.DataFrame(
        {
            "game_id": range(100, 100 + n_games),
            "season": 2026,
            "tip_at": tips,
            "home_team_id": team_ids[0],
            "away_team_id": team_ids[1],
            "status": "final",
        }
    )


def box(rows):
    """rows: (game_id, team_id, athlete_id, minutes)."""
    df = pd.DataFrame(rows, columns=["game_id", "team_id", "athlete_id", "minutes"])
    df["player_name"] = "P" + df["athlete_id"].astype(str)
    df["played"] = df["minutes"] > 0
    df["starter"] = df["minutes"] >= 25
    df["fetched_at"] = pd.Timestamp("2026-11-10T00:00:00Z")
    return df


def ten_player_game(game_id, team_id=1, skip=(), bump=None):
    """Players 1..10 with minutes 38, 34, ..., 2 (player 1 plays most)."""
    rows = []
    for a in range(1, 11):
        if a in skip:
            continue
        minutes = 40 - 4 * a + 2
        if bump and a in bump:
            minutes = bump[a]
        rows.append((game_id, team_id, a, minutes))
    return rows


def row(df, game_id, athlete_id, team_id=1):
    hit = df[(df.game_id == game_id) & (df.team_id == team_id) & (df.athlete_id == athlete_id)]
    assert len(hit) == 1, (game_id, athlete_id, len(hit))
    return hit.iloc[0]


def test_first_game_has_no_rotation_and_top_eight_after():
    games = make_games(2)
    out = build_rotation_players(games, box(ten_player_game(100) + ten_player_game(101)))
    assert out[out.game_id == 100].empty
    g2 = out[(out.game_id == 101) & (out.team_id == 1)].set_index("athlete_id")
    assert g2["is_rotation"].sum() == 8
    assert g2.loc[1, "rotation_rank"] == 1 and g2.loc[8, "is_rotation"]
    assert not g2.loc[9, "is_rotation"] and not g2.loc[10, "is_rotation"]


def test_a_game_never_sees_its_own_minutes():
    games = make_games(3)
    base = box(ten_player_game(100) + ten_player_game(101) + ten_player_game(102))
    # Player 10 plays 40 minutes in game 102: game 102's rank must not move.
    changed = box(ten_player_game(100) + ten_player_game(101) + ten_player_game(102, bump={10: 40}))
    a = build_rotation_players(games, base)
    b = build_rotation_players(games, changed)
    key = ["game_id", "team_id", "athlete_id"]
    left = a[a.game_id <= 102].set_index(key)[POINT_IN_TIME_COLUMNS].sort_index()
    right = b[b.game_id <= 102].set_index(key)[POINT_IN_TIME_COLUMNS].sort_index()
    pd.testing.assert_frame_equal(left, right)
    assert row(b, 102, 10)["minutes"] == 40  # the outcome column did change


def test_injured_starter_stays_rotation_and_sat_is_recorded():
    games = make_games(4)
    rows = ten_player_game(100) + ten_player_game(101)
    rows += ten_player_game(102, skip=(1,)) + ten_player_game(103, skip=(1,))
    out = build_rotation_players(games, box(rows))
    g3, g4 = row(out, 102, 1), row(out, 103, 1)
    assert g3["is_rotation"] and not g3["played"] and g3["minutes"] == 0
    assert g3["games_sat_in_a_row_before"] == 0  # a new absence
    assert g4["is_rotation"] and g4["games_sat_in_a_row_before"] == 1  # still out
    assert g4["mpg_before"] == 38  # minutes per game PLAYED, not per team game


def test_missing_box_score_is_unknown_not_everyone_sat():
    games = make_games(3)
    # Team 1 has no box score for game 101.
    rows = ten_player_game(100) + ten_player_game(102)
    rows += [r for g in (100, 101, 102) for r in ten_player_game(g, team_id=2)]
    out = build_rotation_players(games, box(rows))
    assert out[(out.game_id == 101) & (out.team_id == 1)].empty
    p1 = row(out, 102, 1)
    assert p1["team_games_before"] == 1 and p1["games_sat_in_a_row_before"] == 0


def test_rank_ties_break_on_minutes_then_athlete_id():
    games = make_games(2)
    rows = [(100, 1, a, 20) for a in range(1, 11)] + [(101, 1, a, 20) for a in range(1, 11)]
    out = build_rotation_players(games, box(rows))
    g2 = out[out.game_id == 101].set_index("athlete_id")
    assert g2.loc[1, "rotation_rank"] == 1 and g2.loc[8, "rotation_rank"] == 8
    assert not g2.loc[9, "is_rotation"]


def test_player_listed_but_never_played_is_not_rotation():
    games = make_games(2)
    rows = ten_player_game(100) + [(100, 1, 99, 0)] + ten_player_game(101)
    out = build_rotation_players(games, box(rows))
    r = row(out, 101, 99)
    assert r["games_played_before"] == 0 and pd.isna(r["rotation_rank"])
    assert not r["is_rotation"]
