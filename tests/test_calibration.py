"""Tests for calibration and tiers (task 1.9). The exit checks in docs/CALIBRATION.md, section 8.

No private data: every game below is made up. Numbered comments match the spec's list.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from shac.model import calibration as cal
from shac.model.calibration import (
    BASELINE,
    apply_cover_prob,
    baseline_leave_one_season_out,
    calibrate,
    calibrate_all,
    cover_prob,
    fit_slope,
    out_of_sample_margins,
    tier_for,
)
from shac.model.margin import PREDICTOR
from shac.scorecard import run
from test_scorecard import game, history

SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
PREDICTORS = (BASELINE, PREDICTOR)


def made_up(n_per_season=2600, seed=0, seasons=SEASONS, rounded=True):
    """Features and lines for made-up games: margin = rating + 3.4 * home + noise.

    The close is the true margin plus a little noise, so a predictor's edge is small and
    its side covers a bit more often as the edge grows (a positive slope). With
    `rounded`, margins are whole points and lines half points, so pushes happen.
    Returns (features, lines) in the shapes calibrate() takes.
    """
    rng = np.random.default_rng(seed)
    frames = []
    first_id = 1
    for season in seasons:
        n = n_per_season
        rating = rng.uniform(-15, 15, n)
        home = (rng.uniform(size=n) < 0.75).astype("float64")
        truth = rating + 3.4 * home
        margin = truth + rng.normal(0, 11, n)
        close = -(truth + rng.normal(0, 1.5, n))
        if rounded:
            margin, close = np.round(margin), np.round(close * 2) / 2
        frames.append(
            pd.DataFrame(
                {
                    "game_id": np.arange(first_id, first_id + n),
                    "season": season,
                    "rating_margin": rating,
                    "home": home,
                    "home_margin": margin,
                    "close_home_spread": close,
                }
            )
        )
        first_id += n
    both = pd.concat(frames, ignore_index=True)
    return both.drop(columns="close_home_spread"), both[["game_id", "close_home_spread"]]


@pytest.fixture(scope="module")
def data():
    return made_up()


def rewrite(features, lines, seasons, seed=99):
    """New random results and lines for every game in `seasons`; inputs untouched."""
    rng = np.random.default_rng(seed)
    f, ln = features.copy(), lines.copy()
    hit = f["season"].isin(seasons).to_numpy()
    f.loc[hit, "home_margin"] = np.round(rng.normal(0, 15, hit.sum()))
    ln.loc[hit, "close_home_spread"] = np.round(rng.normal(0, 10, hit.sum()) * 2) / 2
    return f, ln


# ---------- 1-4: the slope ----------


def test_1_hand_worked_slope():
    x = np.full(100, 2.0)
    covered = np.array([1] * 60 + [0] * 40)
    assert fit_slope(x, covered) == pytest.approx(math.log(0.6 / 0.4) / 2, abs=1e-6)
    assert fit_slope(x, covered) == pytest.approx(0.2027326, abs=1e-6)


def test_2_score_equation_is_zero_at_the_fit():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 8, 500)
    covered = (rng.uniform(size=500) < 1 / (1 + np.exp(-0.1 * x))).astype(int)
    b = fit_slope(x, covered)
    p = cover_prob(x, b)
    assert abs(np.sum(x * (covered - p))) < 1e-6


def test_3_zero_information_gives_a_flat_fifty_percent():
    x = np.full(100, 3.5)
    covered = np.array([1, 0] * 50)
    b = fit_slope(x, covered)
    assert b == 0.0
    assert np.all(cover_prob(np.array([0.5, 3.5, 20.0]), b) == 0.5)
    assert tier_for(0.5) == "none"


def test_4_a_negative_slope_is_kept_and_the_side_is_not_flipped():
    x = np.full(100, 2.0)
    covered = np.array([1] * 40 + [0] * 60)
    b = fit_slope(x, covered)
    assert b < 0
    p = float(cover_prob(2.0, b))
    assert p < 0.5
    assert tier_for(p) == "none"

    # Through the scorecard: the call is still the predictor's own side (home here).
    hist = history([game(1, 2023, margin=10, close=-3.0), game(2, 2023, margin=0, close=-3.0)])
    preds = pd.DataFrame(
        {"predictor": PREDICTOR, "game_id": [1, 2], "pred_margin": [5.0, 5.0], "cover_prob": np.nan}
    )
    lines = hist[["game_id", "cbbd_close_home_spread"]].rename(
        columns={"cbbd_close_home_spread": "close_home_spread"}
    )
    filled = apply_cover_prob(
        preds, lines, hist.set_index("game_id")["season"], {(PREDICTOR, 2023): b}
    )
    assert (filled["cover_prob"] < 0.5).all()
    assert filled["pred_margin"].tolist() == [5.0, 5.0]
    ats = run.score(hist, filled)["metric_4_ats"].set_index("scope").loc["2023"]
    assert (ats["wins"], ats["losses"]) == (1, 1)  # home covered game 1, not game 2


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_the_fit_raises_instead_of_returning_a_value_when_it_does_not_settle():
    # Every game covered: the likelihood keeps rising with b, so the loop never stops.
    with pytest.raises(RuntimeError, match="did not converge"):
        fit_slope(np.full(50, 2.0), np.ones(50))


# ---------- 5: left-out games ----------


def test_5_pushes_zero_edges_and_missing_closes_leave_the_slope_alone(data):
    features, lines = data
    # Extra games with no rating gap at a neutral site: no predictor learns anything from
    # them (rating 0, home 0), and both predict 0 for them.
    extra = pd.DataFrame(
        {
            "game_id": [900001, 900002, 900003, 900004, 900005],
            "season": [2019, 2020, 2022, 2019, 2020],
            "rating_margin": 0.0,
            "home": 0.0,
            "home_margin": [-5.0, 4.0, 7.0, 3.0, -2.0],
        }
    )
    extra_lines = pd.DataFrame(
        {
            "game_id": extra["game_id"],
            # push (0 + 5 != 0 edge, -5 + 5 = 0 ats), push, zero edge, no close, no close
            "close_home_spread": [5.0, -4.0, 0.0, np.nan, np.nan],
        }
    )
    more_f = pd.concat([features, extra], ignore_index=True)
    more_l = pd.concat([lines, extra_lines], ignore_index=True)
    for predictor in PREDICTORS:
        before = calibrate(features, lines, predictor, 2023)
        after = calibrate(more_f, more_l, predictor, 2023)
        assert after["slope_b"] == pytest.approx(before["slope_b"], abs=1e-12)
        assert after["n_fit_games"] == before["n_fit_games"]
        assert after["n_left_out_push"] == before["n_left_out_push"] + 2
        assert after["n_left_out_no_side"] == before["n_left_out_no_side"] + 1
        assert after["n_left_out_no_close"] == before["n_left_out_no_close"] + 2


# ---------- 6-8: no leakage ----------


def test_6_walk_forward_a_seasons_slope_ignores_its_own_and_later_seasons(data):
    features, lines = data
    for predictor in PREDICTORS:
        b = calibrate(features, lines, predictor, 2024)["slope_b"]
        f, ln = rewrite(features, lines, [2024, 2025, 2026])
        assert calibrate(f, ln, predictor, 2024)["slope_b"] == b
        f, ln = rewrite(features, lines, [2022])
        assert calibrate(f, ln, predictor, 2024)["slope_b"] != pytest.approx(b, abs=1e-6)


def test_7_2020_21_is_in_no_fit_and_its_own_row_uses_2019_and_2020(data):
    features, lines = data
    seasons = [2021, 2023, 2024, 2025, 2026, 2027]
    f, ln = rewrite(features, lines, [2021])
    for predictor in PREDICTORS:
        for season in seasons:
            assert (
                calibrate(f, ln, predictor, season)["slope_b"]
                == calibrate(features, lines, predictor, season)["slope_b"]
            )
        assert calibrate(features, lines, predictor, 2021)["fit_seasons"] == "2019 2020"


def test_8_leave_one_season_out_never_sees_the_season_it_predicts(data):
    features, _ = data
    fit_seasons = [2019, 2020, 2022, 2023]
    f, _ = rewrite(features, data[1], [2022])
    for predictor in PREDICTORS:
        before = out_of_sample_margins(features, predictor, fit_seasons).set_index("game_id")
        after = out_of_sample_margins(f, predictor, fit_seasons).set_index("game_id")
        in_2022 = before["season"] == 2022
        assert np.allclose(before.loc[in_2022, "pred_margin"], after.loc[in_2022, "pred_margin"])
        # ...while the other seasons, which learn from 2022, do move.
        assert not np.allclose(
            before.loc[~in_2022, "pred_margin"], after.loc[~in_2022, "pred_margin"]
        )


# ---------- 9: the baseline's leave-one-out, by hand ----------


def test_9_baseline_leave_one_season_out_matches_a_hand_calculation():
    # Residual (margin - rating) at home: 2019: 4; 2020: 2; 2022: 6. Neutral games and an
    # unknown site never enter H. H_2019 = (2 + 6) / 2 = 4, H_2020 = 5, H_2022 = 3.
    features = pd.DataFrame(
        {
            "game_id": [1, 2, 3, 4, 5],
            "season": [2019, 2019, 2020, 2022, 2022],
            "rating_margin": [1.0, 2.0, 0.0, -1.0, 3.0],
            "home": [1.0, 0.0, 1.0, 1.0, np.nan],
            "home_margin": [5.0, 10.0, 2.0, 5.0, 40.0],
        }
    )
    out = baseline_leave_one_season_out(features, [2019, 2020, 2022]).set_index("game_id")
    assert out.loc[1, "pred_margin"] == pytest.approx(1 + 4)
    assert out.loc[2, "pred_margin"] == pytest.approx(2.0)  # neutral: no home court
    assert out.loc[3, "pred_margin"] == pytest.approx(0 + 5)
    assert out.loc[4, "pred_margin"] == pytest.approx(-1 + 3)
    assert pd.isna(out.loc[5, "pred_margin"])  # unknown site: no prediction


def test_9_the_two_predictors_fit_the_same_games(data):
    features, lines = data
    fits, _ = calibrate_all(features, lines, [2021, 2023, 2024, 2025, 2026, 2027])
    counts = ["n_fit_games", "n_left_out_no_close", "n_left_out_no_side", "n_left_out_push"]
    by_predictor = fits.set_index(["scored_season", "predictor"])[counts].unstack("predictor")
    for col in counts:
        assert (by_predictor[col][BASELINE] == by_predictor[col][PREDICTOR]).all()
    assert len(fits) == 12
    assert (fits["status"] == "fit").all()
    assert fits["n_left_out_push"].gt(0).all()  # rounded data: pushes exist and are counted


def test_differing_fit_counts_stop_the_run(data, monkeypatch):
    features, lines = data
    real = cal.out_of_sample_margins

    def drop_one_for_the_model(features, predictor, fit_seasons):
        out = real(features, predictor, fit_seasons)
        if predictor == PREDICTOR:
            out.loc[out.index[0], "pred_margin"] = np.nan
        return out

    monkeypatch.setattr(cal, "out_of_sample_margins", drop_one_for_the_model)
    with pytest.raises(ValueError, match="fit counts differ"):
        calibrate_all(features, lines, [2023])


# ---------- 10-11: tiers and the reliability table ----------


def test_10_tier_for_and_the_sql_agree_at_the_cutoffs():
    probs = {1: 0.5299999, 2: 0.53, 3: 0.5599999, 4: 0.56}
    hist = history([game(g, 2023, margin=10, close=-3.0) for g in probs])
    preds = pd.DataFrame(
        {
            "predictor": "test_model",
            "game_id": list(probs),
            "pred_margin": 5.0,
            "cover_prob": list(probs.values()),
        }
    )
    con = run.scored_connection(hist, preds)
    sql = con.execute(
        "SELECT game_id, tier FROM scored WHERE scope = '2023' ORDER BY game_id"
    ).fetchall()
    con.close()
    assert dict(sql) == {g: tier_for(p) for g, p in probs.items()}
    assert [tier_for(p) for p in probs.values()] == ["none", "lean", "lean", "pick"]


@pytest.mark.parametrize(("n", "thin"), [(99, True), (100, False)])
def test_11_a_group_under_100_games_is_thin(n, thin):
    hist = history([game(g, 2023, margin=10 if g % 2 else 0, close=-3.0) for g in range(1, n + 1)])
    preds = pd.DataFrame(
        {
            "predictor": "test_model",
            "game_id": range(1, n + 1),
            "pred_margin": 5.0,
            "cover_prob": 0.51,
        }
    )
    m = run.score(hist, preds)["metric_5b_reliability"]
    row = m[(m["scope"] == "2023") & (m["cover_prob_group"] == "50-52%")].iloc[0]
    assert row["n_games"] == n
    assert row["n_wins"] == (n + 1) // 2
    assert bool(row["thin"]) is thin
    assert pd.isna(row["actual_cover_rate"]) is thin
    assert not pd.isna(row["wilson95_low"])


# ---------- 12: minimum to fit ----------


def test_12_one_fit_season_is_not_enough(data):
    features, lines = data
    # 2021's fit seasons are 2019 and 2020; with 2020 empty, leave-one-season-out has
    # nothing to learn 2019 from, so no game has an out-of-sample margin.
    only_2019 = features[features["season"] != 2020]
    for predictor in PREDICTORS:
        row = calibrate(only_2019, lines, predictor, 2021)
        assert row["status"] == "not_fit"
        assert row["slope_b"] is None and row["edge_for_lean"] is None
        assert row["n_fit_games"] == 0


def test_12_4999_games_is_not_enough_and_leaves_cover_prob_empty():
    # Unrounded made-up data: no pushes and no zero edges, so every priced game is fit.
    features, lines = made_up(n_per_season=2500, rounded=False, seasons=[2019, 2020, 2021])
    # Ids: 2019 = 1-2500, 2020 = 2501-5000, 2021 = 5001-7500. Drop one 2020 game.
    keep = features[features["game_id"] != 5000]
    for predictor in PREDICTORS:
        assert calibrate(features, lines, predictor, 2021)["status"] == "fit"  # 5,000
        row = calibrate(keep, lines, predictor, 2021)
        assert (row["n_fit_games"], row["status"]) == (4999, "not_fit")
    preds = pd.DataFrame(
        {"predictor": BASELINE, "game_id": [5001], "pred_margin": [3.0], "cover_prob": np.nan}
    )
    filled = apply_cover_prob(
        preds, lines, features.set_index("game_id")["season"], {(BASELINE, 2021): None}
    )
    assert filled["cover_prob"].isna().all()


# ---------- 13: the refusal switch ----------


def test_13_the_run_still_refuses_while_the_backtest_close_is_undecided(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(run, "BACKTEST_CLOSE_DECIDED", False)
    assert run.main(["--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 1
    assert "Not run" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


# ---------- end to end through the scorecard ----------


@pytest.fixture(scope="module")
def end_to_end():
    """A made-up game_history big enough to fit every season, run through the scorecard."""
    rng = np.random.default_rng(5)
    rows, game_id = [], 0
    for season in [*SEASONS, 2027]:
        for i in range(2600 if season != 2027 else 10):
            game_id += 1
            rating = float(rng.uniform(-15, 15))
            neutral = i % 4 == 0
            truth = rating + (0.0 if neutral else 3.4)
            close = -round((truth + float(rng.normal(0, 1.5))) * 2) / 2
            rows.append(
                game(
                    game_id,
                    season,
                    rating_margin=rating,
                    margin=round(truth + float(rng.normal(0, 11))),
                    close=close,
                    open_=close + float(rng.choice([-1.0, 0.0, 1.0])),
                    neutral=neutral,
                )
            )
    hist = history(rows)
    baseline = run.baseline_predictions(hist)
    model, _ = run.walk_forward(hist)
    preds = pd.concat(
        [baseline[run.PREDICTION_COLUMNS], model[run.PREDICTION_COLUMNS]], ignore_index=True
    )
    filled, fits, games_by_fit = run.calibrate_predictions(hist, preds)
    out = run.score(hist, filled)
    out["calibration_fit"] = fits
    out["calibration_check"] = run.calibration_check(hist, filled, games_by_fit)
    out["calibration_carryover"] = run.calibration_carryover(hist, filled, fits)
    return hist, filled, out


def test_end_to_end_meets_the_real_data_done_conditions(end_to_end):
    hist, filled, out = end_to_end
    fits = out["calibration_fit"]
    assert len(fits) == 12
    assert set(fits["scored_season"]) == {2021, 2023, 2024, 2025, 2026, 2027}
    assert fits["slope_b"].between(-0.05, 0.20).all()
    for name in ("metric_5a_brier", "metric_5b_reliability", "metric_6_tiers", "calibration_check"):
        assert set(out[name]["predictor"]) == set(PREDICTORS), name
    cov = out["coverage"].set_index(["predictor", "scope"])
    assert (cov["n_with_cover_prob"] == cov["n_with_close"]).all()  # no zero edges here


def test_end_to_end_cover_prob_uses_that_seasons_slope(end_to_end):
    hist, filled, out = end_to_end
    fits = out["calibration_fit"].set_index(["predictor", "scored_season"])["slope_b"]
    merged = filled.merge(hist[["game_id", "season", "cbbd_close_home_spread"]], on="game_id")
    row = merged[(merged["season"] == 2025) & (merged["predictor"] == PREDICTOR)].iloc[0]
    x = abs(row["pred_margin"] + row["cbbd_close_home_spread"])
    assert row["cover_prob"] == pytest.approx(1 / (1 + math.exp(-fits[(PREDICTOR, 2025)] * x)))
    # Fit seasons and the live season get no backtest cover_prob.
    assert merged.loc[merged["season"].isin([2019, 2020, 2022]), "cover_prob"].isna().all()


def test_end_to_end_isotonic_check_and_carryover(end_to_end):
    _, _, out = end_to_end
    check = out["calibration_check"].set_index(["predictor", "scope"])
    brier = out["metric_5a_brier"].set_index(["predictor", "scope"])
    assert np.allclose(check["brier_logistic"], brier.loc[check.index, "brier_score"])
    assert (check["brier_coin_flip"] == 0.25).all()
    assert (check["n_games"] == brier.loc[check.index, "n_games"]).all()
    carry = out["calibration_carryover"]
    assert set(carry["predictor"]) == set(PREDICTORS)
    assert "2021" not in set(carry["scope"])  # held-out seasons and pooled only
    assert {"n_wins", "wilson95_low", "wilson95_high", "thin"} <= set(carry.columns)
