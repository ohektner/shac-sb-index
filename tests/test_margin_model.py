"""Tests for the margin model and its walk-forward fit (task 1.8).

No private data: every game below is made up, with answers worked out by hand.

Four parts:
- the inputs (sql/model/features.sql),
- the fit recovers weights it should recover,
- no leakage: a season's predictions use earlier seasons only, never 2020-21, never a
  score from the game itself or later, never a line,
- the scorecard accepts the model's rows next to the baseline's.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from shac.model.margin import (
    ALL_FEATURES,
    FEATURE_SETS,
    FEATURES_SQL,
    PREDICTOR,
    build_features,
    compare_feature_sets,
    fit,
    leave_one_season_out,
    training_seasons,
    walk_forward,
)
from shac.scorecard.run import baseline_predictions, score
from shac.tables.history import BENCHMARK_COLUMNS, FEATURE_COLUMNS, KEY_COLUMNS
from test_scorecard import game, history

SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]


def made_up_history(slope=0.9, home_court=3.0, noise=0.0, seed=0) -> pd.DataFrame:
    """40 games a season, 2019 to 2026. margin = slope * rating + home_court * home.

    2020-21 follows a different rule on purpose (margin = 50), so any fit that used it
    would be visibly wrong. Every 4th game is at a neutral site.
    """
    rng = np.random.default_rng(seed)
    rows = []
    game_id = 0
    for season in SEASONS:
        for i in range(40):
            game_id += 1
            rating = float(rng.uniform(-15, 15))
            neutral = i % 4 == 0
            margin = slope * rating + (0.0 if neutral else home_court)
            margin += float(rng.normal(0, noise)) if noise else 0.0
            if season == 2021:
                margin = 50.0
            rows.append(
                game(
                    game_id,
                    season,
                    rating_margin=rating,
                    margin=margin,
                    close=-rating,
                    open_=-rating + 1,
                    neutral=neutral,
                )
            )
    return history(rows)


def predictions(hist: pd.DataFrame, feature_set="core") -> pd.Series:
    out, _ = walk_forward(hist, feature_set)
    return out.set_index("game_id")["pred_margin"]


# ---------- the inputs ----------


def test_rating_and_home_inputs():
    hist = history(
        [
            game(1, 2023, rating_margin=4.0, margin=1),
            game(2, 2023, rating_margin=-2.0, margin=1, neutral=True),
            game(3, 2023, margin=1, rated=False),
        ]
    )
    f = build_features(hist).set_index("game_id")
    assert f.loc[1, "rating_margin"] == pytest.approx(4.0)
    assert f.loc[1, "home"] == 1.0
    assert f.loc[2, "home"] == 0.0
    assert np.isnan(f.loc[3, "rating_margin"])  # no ratings: no input, so no prediction


def test_offense_and_defense_halves_add_up():
    # T = 70 possessions. Offense gap 112 - 104 = 8 -> 5.6. Defense gap 100 - 98 = 2 -> 1.4.
    row = game(1, 2023, margin=0)
    row.update(home_adj_o=112.0, home_adj_d=98.0, away_adj_o=104.0, away_adj_d=100.0)
    f = build_features(history([row])).iloc[0]
    assert f["off_margin"] == pytest.approx(5.6)
    assert f["def_margin"] == pytest.approx(1.4)
    assert f["rating_margin"] == pytest.approx(7.0)


def test_early_season_inputs_follow_the_ratings_file_date():
    rows = []
    for game_id, date in [
        (1, dt.date(2022, 11, 20)),
        (2, dt.date(2022, 12, 5)),
        (3, dt.date(2023, 2, 1)),
    ]:
        row = game(game_id, 2023, rating_margin=6.0, margin=0)
        row["rating_date"] = date
        rows.append(row)
    f = build_features(history(rows)).set_index("game_id")
    assert list(f["rm_nov"]) == pytest.approx([6.0, 0.0, 0.0])
    assert list(f["rm_dec"]) == pytest.approx([0.0, 6.0, 0.0])


def test_rest_days_skip_cancelled_games_and_cap_at_seven():
    # Team 1 hosts team 2 on Dec 1 (both openers: 7 and 7), then:
    #   Dec 3: team 1 hosts team 3. Team 1 rested 2 days, team 3 is on its opener (7): -5.
    #   Dec 4: team 2 hosts team 1, but the game is cancelled.
    #   Dec 6: team 1 hosts team 2. Team 1 last PLAYED Dec 3 (3 days); team 2 last played
    #          Dec 1 (5 days). The cancelled game doesn't count: 3 - 5 = -2.
    #   Dec 20: team 1 hosts team 2 again, 14 days later for both, capped at 7: 0.
    def at(game_id, day, home, away, status="final"):
        row = game(game_id, 2023, margin=1)
        row.update(
            tip_at=pd.Timestamp(f"2022-12-{day:02d}T00:00:00Z"),
            home_team_id=home,
            away_team_id=away,
            status=status,
        )
        return row

    rows = [
        at(1, 1, 1, 2),
        at(2, 3, 1, 3),
        at(3, 4, 2, 1, "cancelled"),
        at(4, 6, 1, 2),
        at(5, 20, 1, 2),
    ]
    f = build_features(history(rows)).set_index("game_id")
    assert f.loc[1, "rest_diff"] == pytest.approx(0.0)
    assert f.loc[2, "rest_diff"] == pytest.approx(-5.0)
    assert f.loc[4, "rest_diff"] == pytest.approx(-2.0)
    assert f.loc[5, "rest_diff"] == pytest.approx(0.0)


def test_conference_home_input_is_zero_at_a_neutral_site():
    a = game(1, 2023, margin=0)
    a["conference_game"] = True
    b = game(2, 2023, margin=0, neutral=True)
    b["conference_game"] = True
    c = game(3, 2023, margin=0)
    f = build_features(history([a, b, c])).set_index("game_id")
    assert list(f["home_conf"]) == [1.0, 0.0, 0.0]


def varied_history() -> pd.DataFrame:
    """made_up_history with four teams, a game every other day and dated ratings files,
    so the rest, early-season and conference inputs all take real, different values."""
    hist = made_up_history(noise=8.0)
    n = hist.groupby("season").cumcount()
    start = pd.to_datetime((hist["season"] - 1).astype(str) + "-11-10T00:00:00Z", utc=True)
    hist["tip_at"] = start + pd.to_timedelta(n * 2 + (n % 3 == 0) * 0.5, unit="D")
    hist["home_team_id"] = 1 + n % 4
    hist["away_team_id"] = 1 + (n + 1 + n % 3) % 4
    hist["rating_date"] = (hist["tip_at"] - pd.Timedelta(days=1)).dt.date
    hist["conference_game"] = n % 2 == 0
    hist["home_adj_d"] = 98.0 + n % 5  # so the defense half varies too
    return hist


def test_varied_history_exercises_every_input():
    f = build_features(varied_history())
    for name in ALL_FEATURES:
        assert f[name].nunique() > 2 or name in ("home", "home_conf"), name
    assert f["home_conf"].nunique() == 2
    assert (f["rm_nov"] != 0).any() and (f["rm_dec"] != 0).any() and (f["rest_diff"] != 0).any()


def test_inputs_never_read_a_score_or_a_line():
    """Blank every score and line: no input may move."""
    hist = varied_history()
    blanked = hist.copy()
    for col in ["home_points", "away_points", "home_margin", "overtimes", *BENCHMARK_COLUMNS]:
        blanked[col] = None
    before = build_features(hist)[ALL_FEATURES]
    after = build_features(blanked)[ALL_FEATURES]
    pd.testing.assert_frame_equal(before, after)


def test_a_games_inputs_ignore_its_own_and_later_games_status():
    """Rest days read `status`, but only of games that tipped earlier."""
    hist = varied_history()
    before = build_features(hist).set_index("game_id")[ALL_FEATURES]
    cutoff = hist.loc[hist["season"] == 2023, "tip_at"].sort_values().iloc[20]
    changed = hist.copy()
    changed.loc[changed["tip_at"] >= cutoff, "status"] = "cancelled"
    after = build_features(changed).set_index("game_id")[ALL_FEATURES]
    # Games at or before the cutoff tip: nothing moves, the cutoff game's own status included.
    unaffected = hist.loc[hist["tip_at"] <= cutoff, "game_id"]
    pd.testing.assert_frame_equal(before.loc[unaffected], after.loc[unaffected])
    # And status does matter going forward, so the test can't pass by status being ignored.
    assert not before["rest_diff"].equals(after["rest_diff"])


def test_two_games_at_the_same_tip_time_are_not_each_others_rest():
    def at(game_id, when, home, away):
        row = game(game_id, 2023, margin=1)
        row.update(tip_at=pd.Timestamp(when), home_team_id=home, away_team_id=away)
        return row

    # Team 1 is listed in two games at the same minute (a data quirk). Neither is "before"
    # the other: team 1 is on an opener (7) in both, as are teams 2 and 3.
    rows = [at(1, "2022-12-01T00:00:00Z", 1, 2), at(2, "2022-12-01T00:00:00Z", 1, 3)]
    f = build_features(history(rows)).set_index("game_id")
    assert list(f["rest_diff"]) == pytest.approx([0.0, 0.0])


def test_every_feature_set_uses_known_inputs_only():
    for names in FEATURE_SETS.values():
        assert set(names) <= set(ALL_FEATURES)
    # The feature query's source columns are all key or feature columns, plus `status`.
    sql = FEATURES_SQL.read_text(encoding="utf-8")
    for forbidden in ["home_margin", "home_points", "away_points", "overtimes", *BENCHMARK_COLUMNS]:
        assert forbidden not in sql
    assert {"rating_date", "home_adj_o", "neutral_site"} <= set(KEY_COLUMNS + FEATURE_COLUMNS)


# ---------- the fit ----------


def test_fit_recovers_the_weights_that_made_the_data():
    features = build_features(made_up_history(slope=0.9, home_court=3.0))
    f = fit(features, [2019, 2020, 2022], "core")
    assert f.weights_dict() == pytest.approx({"rating_margin": 0.9, "home": 3.0})
    assert f.n_train_games == 120


def test_prediction_is_slope_times_rating_plus_home_court():
    hist = made_up_history(slope=0.9, home_court=3.0)
    pred = predictions(hist)
    features = build_features(hist).set_index("game_id")
    test_rows = features[features["season"] == 2023]
    expected = 0.9 * test_rows["rating_margin"] + 3.0 * test_rows["home"]
    assert pred.loc[test_rows.index].to_numpy() == pytest.approx(expected.to_numpy())


def test_unrated_and_unknown_site_games_get_no_prediction():
    hist = made_up_history()
    extra = history(
        [game(9001, 2023, margin=3, rated=False), game(9002, 2023, rating_margin=1.0, margin=3)]
    )
    extra.loc[extra["game_id"] == 9002, "neutral_site"] = pd.NA
    pred = predictions(pd.concat([hist, extra], ignore_index=True))
    assert np.isnan(pred.loc[9001])
    assert np.isnan(pred.loc[9002])


# ---------- walk-forward and no leakage ----------


def test_training_seasons_are_earlier_and_never_2021():
    assert training_seasons(2021) == [2019, 2020]
    assert training_seasons(2023) == [2019, 2020, 2022]
    assert training_seasons(2026) == [2019, 2020, 2022, 2023, 2024, 2025]
    assert training_seasons(2027) == [2019, 2020, 2022, 2023, 2024, 2025, 2026]  # live


def test_walk_forward_scores_held_out_seasons_and_2021_only():
    hist = made_up_history()
    out, fits = walk_forward(hist)
    seasons = hist.set_index("game_id")["season"].reindex(out["game_id"])
    assert sorted(seasons.unique()) == [2021, 2023, 2024, 2025, 2026]
    assert sorted(fits) == [2021, 2023, 2024, 2025, 2026]
    assert (out["predictor"] == PREDICTOR).all()
    assert out["cover_prob"].isna().all()  # task 1.9
    assert fits[2023].train_seasons == [2019, 2020, 2022]


def test_2021_is_never_trained_on():
    # 2020-21 margins are all 50 in the made-up data. If any fit used them, the weights
    # would be far from 0.9 and 3.
    _, fits = walk_forward(made_up_history())
    for season in [2023, 2024, 2025, 2026]:
        assert fits[season].weights_dict() == pytest.approx({"rating_margin": 0.9, "home": 3.0})


def test_a_seasons_predictions_ignore_its_own_and_later_results():
    hist = made_up_history(noise=8.0)
    before = predictions(hist)
    for cutoff in [2023, 2024, 2025, 2026]:
        changed = hist.copy()
        later = changed["season"] >= cutoff
        changed.loc[later, "home_margin"] = changed.loc[later, "home_margin"] + 37.0
        after = predictions(changed)
        this_season = hist.loc[hist["season"] == cutoff, "game_id"]
        assert after.loc[this_season].to_numpy() == pytest.approx(
            before.loc[this_season].to_numpy()
        )


def test_an_earlier_seasons_results_do_move_the_fit():
    # The other direction, so the test above can't pass by the model ignoring everything.
    hist = made_up_history(noise=8.0)
    changed = hist.copy()
    changed.loc[changed["season"] == 2022, "home_margin"] += 37.0
    this_season = hist.loc[hist["season"] == 2023, "game_id"]
    assert not np.allclose(
        predictions(hist).loc[this_season], predictions(changed).loc[this_season]
    )


def test_predictions_ignore_every_line():
    hist = made_up_history(noise=8.0)
    changed = hist.copy()
    changed["cbbd_close_home_spread"] = 99.0
    changed["cbbd_open_home_spread"] = -99.0
    changed["own_close_home_spread"] = 5.0
    changed["pinnacle_close_home_spread"] = 5.0
    for name in FEATURE_SETS:
        pd.testing.assert_series_equal(predictions(hist, name), predictions(changed, name))


def test_leave_one_season_out_never_sees_the_season_it_predicts():
    hist = made_up_history(noise=8.0)
    seasons = [2019, 2020, 2022]
    before = leave_one_season_out(build_features(hist), seasons).set_index("game_id")
    changed = hist.copy()
    changed.loc[changed["season"] == 2020, "home_margin"] += 37.0
    after = leave_one_season_out(build_features(changed), seasons).set_index("game_id")
    in_2020 = before["season"] == 2020
    assert after.loc[in_2020, "pred_margin"].to_numpy() == pytest.approx(
        before.loc[in_2020, "pred_margin"].to_numpy()
    )
    assert not np.allclose(after.loc[~in_2020, "pred_margin"], before.loc[~in_2020, "pred_margin"])
    assert len(before) == 120  # every game of the three seasons, once


def test_compare_feature_sets_matches_a_by_hand_calculation():
    seasons = [2019, 2020, 2022]
    features = build_features(varied_history())
    table = compare_feature_sets(features, seasons).set_index("feature_set")
    assert set(table.index) == set(FEATURE_SETS)
    assert table.loc["core", "gain_vs_core"] == 0.0
    assert table["n_games"].nunique() == 1  # every candidate scored on the same games
    assert table["n_games"].iloc[0] == 120

    # Redo "rest" against "core" by hand from the leave-one-season-out predictions.
    def abs_error(name):
        loso = leave_one_season_out(features, seasons, name).set_index("game_id")
        return (loso["pred_margin"] - loso["home_margin"]).abs()

    gain = abs_error("core") - abs_error("rest")
    rest = table.loc["rest"]
    assert rest["mae"] == pytest.approx(abs_error("rest").mean())
    assert rest["gain_vs_core"] == pytest.approx(gain.mean())
    assert rest["gain_se"] == pytest.approx(gain.std(ddof=1) / np.sqrt(120))
    season_of = features.set_index("game_id")["season"].reindex(gain.index)
    assert rest["seasons_better"] == int((gain.groupby(season_of).mean() > 0).sum())
    assert rest["mae_2020"] == pytest.approx(abs_error("rest")[season_of == 2020].mean())


# ---------- the scorecard takes the model's rows ----------


def test_scorecard_scores_the_model_next_to_the_baseline():
    hist = made_up_history(noise=8.0)
    model, _ = walk_forward(hist)
    both = pd.concat([baseline_predictions(hist), model], ignore_index=True)
    out = score(hist, both)
    distance = out["metric_2_distance_to_close"]
    pooled = distance[distance["scope"] == "pooled"]
    assert set(pooled["predictor"]) == {"torvik_baseline", PREDICTOR}
    assert pooled["n_games"].nunique() == 1  # same games for both
