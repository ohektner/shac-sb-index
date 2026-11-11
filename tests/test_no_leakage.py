"""No-leakage tests for game_history (task 1.6; ARCHITECTURE.md, "The no-leakage contract").

Two halves:

- Synthetic tests (always run, including CI): made-up teams, games and ratings that pin
  down each rule, so a change that breaks one fails here.
- Real-data tests (run only when SHAC_DATA_DIR points at the private data repo, after
  `shac.tables.build`). Run locally with:

      SHAC_DATA_DIR=../shac-sb-index-data uv run pytest tests/test_no_leakage.py

  They print nothing from the data; failures report counts and game ids only.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from shac.tables.build import query_df
from shac.tables.history import (
    BENCHMARK_COLUMNS,
    FEATURE_COLUMNS,
    KEY_COLUMNS,
    LABEL_COLUMNS,
    build_game_history,
)
from shac.tables.torvik import time_machine_as_of

POINT_IN_TIME = KEY_COLUMNS + FEATURE_COLUMNS


# ---------- synthetic fixtures ----------


def utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC")


def make_ratings(rows):
    """rows: (season, 'YYYY-MM-DD', team_id, adj_o). Time Machine rows, real as_of rule."""
    df = pd.DataFrame(rows, columns=["season", "rating_date", "team_id", "adj_o"])
    dates = pd.to_datetime(df["rating_date"])
    df["rating_date"] = dates.dt.date
    df["source"] = "torvik_time_machine"
    df["adj_d"] = 95.0
    df["adj_tempo"] = 68.0 + df["team_id"]
    df["barthag"] = 0.5
    df["rank"] = df["team_id"]
    df["wins"] = 0
    df["losses"] = 0
    df["as_of"] = time_machine_as_of(dates).to_numpy()
    df["fetched_at"] = utc("2026-11-10T21:30:00Z")
    return df


def make_games(rows):
    """rows: (game_id, season, tip in UTC, home, away)."""
    df = pd.DataFrame(rows, columns=["game_id", "season", "tip_at", "home_team_id", "away_team_id"])
    df["tip_at"] = pd.to_datetime(df["tip_at"], utc=True)
    df["season_type"] = "regular"
    df["tip_tbd"] = False
    df["neutral_site"] = False
    df["conference_game"] = False
    df["status"] = "final"
    df["fetched_at"] = utc("2026-11-10T15:00:00Z")
    return df


def make_results(games):
    rng = np.random.default_rng(0)
    pts = rng.integers(55, 95, size=(len(games), 2))
    return pd.DataFrame(
        {
            "game_id": games["game_id"],
            "home_points": pts[:, 0],
            "away_points": pts[:, 1],
            "home_margin": pts[:, 0] - pts[:, 1],
            "overtimes": 0,
            "fetched_at": utc("2026-11-10T15:00:00Z"),
        }
    )


def make_closes(games):
    return pd.DataFrame(
        {
            "game_id": games["game_id"],
            "source": "cbbd",
            "close_home_spread": -3.5,
            "close_snapshot_at": pd.Series([pd.NaT] * len(games), dtype="datetime64[ns, UTC]"),
            "minutes_before_tip": pd.Series([None] * len(games), dtype="Int32"),
            "n_books": 1,
            "open_home_spread": -2.5,
            "open_snapshot_at": pd.Series([pd.NaT] * len(games), dtype="datetime64[ns, UTC]"),
            "basis": "draftkings",
        }
    )


# Three teams, files for Nov 5 to Nov 9, 2018 (season 2019) plus one 2018 file (season
# 2018) that must never reach a 2019 game. adj_o encodes the file's day, so a test can
# read off which file a game got: adj_o = 100 + day of month.
RATINGS = make_ratings(
    [(2018, "2018-04-01", t, 50.0) for t in (1, 2, 3)]
    + [(2019, f"2018-11-0{d}", t, 100.0 + d) for d in range(5, 10) for t in (1, 2, 3)]
)
GAMES = make_games(
    [
        # 7 p.m. Eastern on Nov 6: file Nov 5.
        (1, 2019, "2018-11-07T00:00:00Z", 1, 2),
        # 1:30 a.m. Eastern on Nov 8, a Hawaii game played the evening of Nov 7 local:
        # file Nov 6 (file Nov 7 already counts it).
        (2, 2019, "2018-11-08T06:30:00Z", 2, 3),
        # Exactly at 06:00 Eastern Nov 8, when file Nov 7 becomes known: still file Nov 6.
        (3, 2019, "2018-11-08T11:00:00Z", 1, 3),
        # Noon Eastern Nov 8: file Nov 7.
        (4, 2019, "2018-11-08T17:00:00Z", 1, 2),
        # Team 3 is missing from file Nov 8 (see below): no ratings for it, no fallback.
        (5, 2019, "2018-11-10T00:00:00Z", 3, 1),
        # Before the season's first file is known: no file at all (not the 2018 one).
        (6, 2019, "2018-11-05T23:00:00Z", 1, 2),
    ]
)
RATINGS = RATINGS[
    ~((RATINGS["team_id"] == 3) & (RATINGS["rating_date"].astype(str) == "2018-11-08"))
].reset_index(drop=True)


def build(games=GAMES, ratings=RATINGS):
    return build_game_history(games, make_results(games), ratings, make_closes(games)).set_index(
        "game_id"
    )


def file_day(history, game_id, side="home"):
    value = history.loc[game_id, f"{side}_adj_o"]
    return None if pd.isna(value) else int(round(value - 100))


# ---------- synthetic tests ----------


def test_every_ratings_as_of_is_strictly_before_tip():
    h = build()
    known = h["ratings_as_of"].notna()
    assert (h.loc[known, "ratings_as_of"] < h.loc[known, "tip_at"]).all()


def test_each_game_gets_the_latest_file_known_before_tip():
    h = build()
    assert file_day(h, 1) == 5
    assert file_day(h, 2) == 6  # late-night game: not file 7, which already counts it
    assert file_day(h, 3) == 6  # tip exactly at file 7's as_of: strictly before only
    assert file_day(h, 4) == 7


def test_missing_team_gets_no_ratings_and_no_older_file():
    h = build()
    assert file_day(h, 5, "home") is None  # team 3, missing from file Nov 8
    assert file_day(h, 5, "away") == 8  # the other side still comes from file Nov 8
    assert str(h.loc[5, "rating_date"])[:10] == "2018-11-08"


def test_no_file_from_an_earlier_season():
    h = build()
    assert pd.isna(h.loc[6, "rating_date"]) and pd.isna(h.loc[6, "home_adj_o"])


def test_file_mean_tempo_is_from_the_same_file():
    h = build()
    # Teams 1 to 3 have tempo 69, 70, 71; file Nov 8 is missing team 3.
    assert h.loc[1, "file_mean_tempo"] == pytest.approx(70.0)
    assert h.loc[5, "file_mean_tempo"] == pytest.approx(69.5)
    assert h.loc[5, "file_n_teams"] == 2


def truncate(cutoff: pd.Timestamp, games=GAMES, ratings=RATINGS):
    """Every input as it stood at `cutoff`: later games, their results and closes, and
    ratings not yet known, are gone."""
    g = games[games["tip_at"] < cutoff]
    r = ratings[ratings["as_of"] < cutoff]
    return build_game_history(g, make_results(g), r, make_closes(g)).set_index("game_id")


def comparable(df: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time columns only, with rating_date as a timestamp on both sides (a
    parquet read returns it as one, a fresh build as a date)."""
    out = df[[c for c in POINT_IN_TIME if c != "game_id"]].copy()
    out["rating_date"] = pd.to_datetime(out["rating_date"]).astype("datetime64[ns]")
    return out


def assert_same_point_in_time_columns(full: pd.DataFrame, cut: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(
        comparable(full.loc[cut.index]), comparable(cut), check_dtype=False
    )


@pytest.mark.parametrize(
    "cutoff",
    [
        "2018-11-07T00:00:01Z",
        "2018-11-08T06:30:01Z",
        "2018-11-08T17:00:01Z",
        "2018-11-12T00:00:00Z",
    ],
)
def test_removing_later_games_and_data_changes_nothing_for_earlier_games(cutoff):
    cutoff = utc(cutoff)
    full = build()
    cut = truncate(cutoff)
    assert len(cut) > 0
    assert_same_point_in_time_columns(full, cut)


def test_changing_later_ratings_changes_nothing_for_earlier_games():
    # Rewrite every ratings row not yet known when game 2 tips: game 2 must not move.
    tip = GAMES.set_index("game_id").loc[2, "tip_at"]
    later = RATINGS.copy()
    later.loc[later["as_of"] >= tip, "adj_o"] = 999.0
    before, after = build(), build(ratings=later)
    for game_id in (1, 2, 3):
        assert before.loc[game_id, FEATURE_COLUMNS].equals(after.loc[game_id, FEATURE_COLUMNS])
    assert after.loc[4, "home_adj_o"] == 999.0  # the edit did land on a later game


def test_feature_columns_hold_no_result_or_market_information():
    assert not set(FEATURE_COLUMNS) & set(LABEL_COLUMNS + BENCHMARK_COLUMNS + KEY_COLUMNS)
    banned = ("margin", "points", "spread", "close", "overtime", "result", "won")
    assert not [c for c in FEATURE_COLUMNS if any(b in c for b in banned)]


def test_season_end_and_unknown_sources_are_never_used():
    rogue = make_ratings([(2019, "2018-11-05", t, 777.0) for t in (1, 2, 3)])
    rogue["source"] = "torvik_season_end"
    h = build(ratings=pd.concat([RATINGS, rogue], ignore_index=True))
    assert set(h["ratings_source"].dropna()) <= {"torvik_time_machine", "torvik_daily"}
    assert (h["home_adj_o"] != 777.0).all() and (h["away_adj_o"] != 777.0).all()


# ---------- real data (local only) ----------

DATA_DIR = os.environ.get("SHAC_DATA_DIR")
real = pytest.mark.skipif(not DATA_DIR, reason="set SHAC_DATA_DIR to the private data repo to run")


def load_tables(names: list[str]) -> dict[str, pd.DataFrame]:
    tables_dir = Path(DATA_DIR) / "tables"
    con = duckdb.connect()
    out = {}
    for name in names:
        path = (tables_dir / f"{name}.parquet").as_posix()
        out[name] = query_df(con, f"SELECT * FROM read_parquet('{path}')")
    return out


@pytest.fixture(scope="module")
def real_tables():
    return load_tables(["games", "results", "ratings_daily", "closing_lines", "game_history"])


@real
def test_real_every_ratings_as_of_is_before_tip(real_tables):
    h = real_tables["game_history"]
    known = h["ratings_as_of"].notna()
    bad = h.loc[known & (h["ratings_as_of"] >= h["tip_at"]), "game_id"]
    assert bad.empty, f"{len(bad)} games with ratings known at or after tip: {bad.head().tolist()}"


@real
def test_real_only_point_in_time_sources(real_tables):
    used = set(real_tables["game_history"]["ratings_source"].dropna())
    assert used <= {"torvik_time_machine", "torvik_daily"}


@real
def test_real_saved_table_matches_a_fresh_build(real_tables):
    t = real_tables
    fresh = build_game_history(t["games"], t["results"], t["ratings_daily"], t["closing_lines"])
    saved = t["game_history"]
    assert len(fresh) == len(saved)
    pd.testing.assert_frame_equal(
        comparable(fresh.set_index("game_id").sort_index()),
        comparable(saved.set_index("game_id").sort_index()),
        check_dtype=False,
    )


@real
def test_real_removing_later_games_changes_nothing(real_tables):
    t = real_tables
    full = build_game_history(
        t["games"], t["results"], t["ratings_daily"], t["closing_lines"]
    ).set_index("game_id")
    games = t["games"]
    # Three cutoffs per season: a quarter, half and three quarters of the way through.
    cutoffs = []
    for _season, g in games.groupby("season"):
        tips = g["tip_at"].sort_values()
        cutoffs += [tips.iloc[int(len(tips) * q)] for q in (0.25, 0.5, 0.75)]
    for cutoff in cutoffs:
        g = games[games["tip_at"] < cutoff]
        cut = build_game_history(
            g,
            t["results"][t["results"]["game_id"].isin(g["game_id"])],
            t["ratings_daily"][t["ratings_daily"]["as_of"] < cutoff],
            t["closing_lines"][t["closing_lines"]["game_id"].isin(g["game_id"])],
        ).set_index("game_id")
        assert_same_point_in_time_columns(full, cut)
