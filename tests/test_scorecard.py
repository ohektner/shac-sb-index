"""Tests for the Torvik baseline and the scorecard metrics (task 1.7).

No private data: every game, rating and line below is made up, and every expected number
is worked out by hand in the comments, so a change to the SQL that moves a metric fails here.

Three parts:
- the baseline formula and its home-court fit (docs/PREREGISTRATION.md, "The Torvik baseline"),
- no leakage: a season's home-court constant uses earlier seasons only, never 2020-21,
- each scorecard metric against hand-computed answers.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from shac.scorecard import run
from shac.scorecard.run import METRICS, SQL_DIR, baseline_fit, baseline_predictions, score
from shac.tables.history import HISTORY_COLUMNS

# ---------- fixtures ----------


def game(
    game_id,
    season,
    rating_margin=0.0,
    margin=None,
    close=None,
    open_=None,
    neutral=False,
    rated=True,
):
    """One game_history row.

    Both tempos and the file's mean tempo are 70, so expected possessions are 70 and the
    rating part of the baseline is (EM_home - EM_away) * 0.7. The home offense is set so
    that the rating part comes out to `rating_margin`. `close` and `open_` are home spreads.
    """
    row = dict.fromkeys(HISTORY_COLUMNS)
    row.update(
        game_id=game_id,
        season=season,
        season_type="regular",
        tip_at=pd.Timestamp(f"{season - 1}-12-01T00:00:00Z"),
        tip_tbd=False,
        neutral_site=neutral,
        conference_game=False,
        home_team_id=1,
        away_team_id=2,
        ratings_source="torvik_time_machine",
        file_n_teams=360,
        file_mean_tempo=70.0,
        home_adj_o=100.0 + rating_margin / 0.7 if rated else None,
        home_adj_d=100.0 if rated else None,
        home_adj_tempo=70.0 if rated else None,
        away_adj_o=100.0,
        away_adj_d=100.0,
        away_adj_tempo=70.0,
        status="final" if margin is not None else "scheduled",
        home_margin=margin,
        cbbd_close_home_spread=close,
        cbbd_open_home_spread=open_,
    )
    return row


def history(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=HISTORY_COLUMNS)
    numeric = [
        "home_adj_o",
        "home_adj_d",
        "home_adj_tempo",
        "away_adj_o",
        "away_adj_d",
        "away_adj_tempo",
        "file_mean_tempo",
        "home_margin",
        "cbbd_close_home_spread",
        "cbbd_open_home_spread",
    ]
    df[numeric] = df[numeric].astype("float64")
    df["neutral_site"] = df["neutral_site"].astype("boolean")  # may be unknown (empty)
    return df


# Home-court fit. "Residual" = actual margin - rating part, on non-neutral games.
#   2019: residuals 4 and 2, plus a neutral game (residual 30) that must not count.
#   2020: residual 1.
#   2021: residual 20. Excluded season: must never be trained on.
#   2022: residual 6, plus an unrated game and an unplayed game that must not count.
#   2023: residual 5.
# So H(2021) = (4 + 2 + 1) / 3, H(2023) = (4 + 2 + 1 + 6) / 4 = 3.25 (pooled over games;
# the average of season averages would be 3.33), H(2024) = (13 + 5) / 5 = 3.6.
FIT_ROWS = [
    game(1, 2019, rating_margin=2.0, margin=6),
    game(2, 2019, rating_margin=-1.0, margin=1),
    game(3, 2019, rating_margin=0.0, margin=30, neutral=True),
    game(4, 2020, rating_margin=3.0, margin=4),
    game(5, 2021, rating_margin=0.0, margin=20),
    game(6, 2022, rating_margin=0.0, margin=6),
    game(7, 2022, margin=40, rated=False),
    game(8, 2022, rating_margin=50.0, margin=None),
    game(9, 2023, rating_margin=4.0, margin=9),
    game(10, 2023, rating_margin=-2.0, margin=1, neutral=True),
    game(11, 2023, margin=3, rated=False),
    game(12, 2024, rating_margin=1.5, margin=0),
    game(13, 2025, rating_margin=0.0, margin=2),
    game(14, 2026, rating_margin=0.0, margin=2),
    game(15, 2027, rating_margin=0.0, margin=2),
]
FIT_HISTORY = history(FIT_ROWS)


def baseline(hist=FIT_HISTORY) -> pd.DataFrame:
    return baseline_predictions(hist).set_index("game_id")


# ---------- the baseline formula ----------


def test_rating_part_matches_the_preregistered_formula():
    # EM_home = 112 - 98 = 14, EM_away = 104 - 100 = 4, gap 10 points per 100 possessions.
    # T = 72 * 66 / 68 = 69.882 possessions. Rating part = 10 * 69.882 / 100 = 6.9882.
    row = game(1, 2023, margin=0, neutral=True)
    row.update(home_adj_o=112.0, home_adj_d=98.0, home_adj_tempo=72.0)
    row.update(away_adj_o=104.0, away_adj_d=100.0, away_adj_tempo=66.0, file_mean_tempo=68.0)
    b = baseline(history([*FIT_ROWS[:8], row]))
    assert b.loc[1, "rating_margin"] == pytest.approx(10 * (72 * 66 / 68) / 100)
    assert b.loc[1, "pred_margin"] == pytest.approx(6.98824, abs=1e-5)  # neutral: H = 0


def test_home_court_is_the_mean_residual_of_non_neutral_training_games():
    b = baseline()
    assert b.loc[5, "home_court"] == pytest.approx(7 / 3)  # 2021: from 2019 and 2020
    assert b.loc[9, "home_court"] == pytest.approx(3.25)  # 2023: 2019, 2020, 2022
    assert b.loc[12, "home_court"] == pytest.approx(3.6)  # 2024: adds 2023
    assert b.loc[9, "n_train_games"] == 4  # neutral, unrated and unplayed games don't count
    assert b.loc[9, "train_seasons"] == "2019 2020 2022"
    assert b.loc[5, "train_seasons"] == "2019 2020"
    assert b.loc[14, "train_seasons"] == "2019 2020 2022 2023 2024 2025"


def test_home_court_is_added_at_home_and_not_at_a_neutral_site():
    b = baseline()
    assert b.loc[9, "pred_margin"] == pytest.approx(4.0 + 3.25)
    assert b.loc[10, "pred_margin"] == pytest.approx(-2.0)


def test_a_game_with_a_missing_rating_gets_no_prediction_and_is_counted():
    b = baseline()
    assert pd.isna(b.loc[11, "pred_margin"])
    fit = baseline_fit(FIT_HISTORY, baseline_predictions(FIT_HISTORY)).set_index("season")
    assert fit.loc[2023, "n_final_games"] == 3
    assert fit.loc[2023, "n_predicted"] == 2
    assert fit.loc[2023, "n_left_out_team_not_in_file"] == 1
    assert fit.loc[2023, "n_left_out_no_ratings_file"] == 0
    assert fit.loc[2023, "n_left_out_neutral_unknown"] == 0
    assert fit.loc[2023, "home_court"] == pytest.approx(3.25)


def test_each_reason_a_game_is_left_out_is_counted_separately():
    no_file = game(16, 2023, margin=5, rated=False)
    no_file.update(file_mean_tempo=None, away_adj_o=None, away_adj_d=None, away_adj_tempo=None)
    unknown_site = game(17, 2023, rating_margin=1.0, margin=5, neutral=None)
    hist = history([*FIT_ROWS, no_file, unknown_site])
    b = baseline_predictions(hist)
    fit = baseline_fit(hist, b).set_index("season")
    assert fit.loc[2023, "n_final_games"] == 5
    assert fit.loc[2023, "n_predicted"] == 2
    assert fit.loc[2023, "n_left_out_no_ratings_file"] == 1
    assert fit.loc[2023, "n_left_out_team_not_in_file"] == 1
    assert fit.loc[2023, "n_left_out_neutral_unknown"] == 1


def test_an_unknown_neutral_site_gets_no_prediction_and_stays_out_of_the_fit():
    # In 2022 it would be a training game with residual 100 if it were counted.
    unknown = game(18, 2022, rating_margin=0.0, margin=100, neutral=None)
    unknown_2023 = game(19, 2023, rating_margin=1.0, margin=5, neutral=None)
    b = baseline(history([*FIT_ROWS, unknown, unknown_2023]))
    assert b.loc[9, "home_court"] == pytest.approx(3.25)
    assert pd.isna(b.loc[19, "pred_margin"])
    assert b.loc[19, "rating_margin"] == pytest.approx(1.0)


def test_only_held_out_seasons_and_2021_get_predictions():
    seasons = set(FIT_HISTORY.set_index("game_id").loc[baseline().index, "season"])
    assert seasons == {2021, 2023, 2024, 2025, 2026}  # not 2019, 2020, 2022 or live 2027


def test_the_baseline_has_no_cover_probability_yet():
    assert baseline()["cover_prob"].isna().all()  # task 1.9 fills it


# ---------- no leakage ----------


def changed(hist: pd.DataFrame, seasons, **columns) -> pd.DataFrame:
    out = hist.copy()
    for column, value in columns.items():
        out.loc[out["season"].isin(seasons), column] = value
    return out


def test_home_court_never_uses_the_test_season_or_later():
    # Rewrite every result from 2023 on. 2023's fit and predictions must not move.
    before = baseline()
    after = baseline(changed(FIT_HISTORY, range(2023, 2028), home_margin=99.0))
    for game_id in (5, 9, 10):
        assert after.loc[game_id, "pred_margin"] == before.loc[game_id, "pred_margin"]
    assert after.loc[9, "home_court"] == pytest.approx(3.25)
    assert after.loc[12, "home_court"] != before.loc[12, "home_court"]  # the edit did land


def test_2021_is_never_trained_on():
    before = baseline()
    after = baseline(changed(FIT_HISTORY, [2021], home_margin=99.0))
    later = [9, 10, 12, 13, 14]
    assert after.loc[later, "home_court"].tolist() == before.loc[later, "home_court"].tolist()


def test_a_games_own_result_never_reaches_its_own_prediction():
    for season in (2021, 2023, 2024, 2025, 2026):
        after = baseline(changed(FIT_HISTORY, [season], home_margin=-77.0))
        ids = FIT_HISTORY.loc[FIT_HISTORY["season"] == season, "game_id"]
        before = baseline()
        pd.testing.assert_series_equal(
            after.loc[ids, "pred_margin"], before.loc[ids, "pred_margin"]
        )


def test_closing_lines_never_reach_the_baseline():
    after = baseline(
        changed(
            FIT_HISTORY,
            range(2019, 2028),
            cbbd_close_home_spread=25.5,
            cbbd_open_home_spread=-30.0,
            own_close_home_spread=12.0,
            pinnacle_close_home_spread=-12.0,
        )
    )
    pd.testing.assert_frame_equal(after, baseline())
    sql = (SQL_DIR / "baseline_predictions.sql").read_text(encoding="utf-8").lower()
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert "spread" not in code and "points" not in code


# ---------- the metrics ----------

# Made-up predictions, results and lines. Spreads are home spreads; the line's implied
# home margin is minus the spread.
#
#   id season pred margin close open | vs close            | CLV (open to close)   | dist
#   21 2023    5    10    -3    -2   | home, 10-3>0  win   | home, 3 - 2 = +1      | 2
#   22 2023   -4    -1    +2    +3   | away, -(−1+2) loss  | away, -(−2 − −3) = -1 | 2
#   23 2023    1     6    -6   none  | away, 6-6=0   push  | no open: left out     | 5
#   24 2023    2     3    -2    -4   | on the line: no side| away, -(2 - 4) = +2   | 0
#   25 2023    7     0   none  none  | no close: left out  | left out              | -
#   26 2024    3     8    -1    -1   | home, 8-1>0   win   | home, 1 - 1 = 0       | 2
#   27 2021    0     4    +1   none  | home, 4+1>0   win   | left out              | 1
#   28 2022, 29 2027: not scored seasons.  30 2023: not played.
METRIC_ROWS = [
    game(21, 2023, margin=10, close=-3.0, open_=-2.0),
    game(22, 2023, margin=-1, close=2.0, open_=3.0),
    game(23, 2023, margin=6, close=-6.0),
    game(24, 2023, margin=3, close=-2.0, open_=-4.0),
    game(25, 2023, margin=0),
    game(26, 2024, margin=8, close=-1.0, open_=-1.0),
    game(27, 2021, margin=4, close=1.0),
    game(28, 2022, margin=4, close=1.0, open_=1.0),
    game(29, 2027, margin=4, close=1.0, open_=1.0),
    game(30, 2023, margin=None, close=-3.0, open_=-2.0),
]
METRIC_PREDS = {21: 5, 22: -4, 23: 1, 24: 2, 25: 7, 26: 3, 27: 0, 28: 9, 29: 9, 30: 9}


def predictions(pred_by_game, cover_prob=None, predictor="test_model") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "predictor": predictor,
            "game_id": list(pred_by_game),
            "pred_margin": [float(v) for v in pred_by_game.values()],
            "cover_prob": pd.Series(
                [(cover_prob or {}).get(g) for g in pred_by_game], dtype="float64"
            ),
        }
    )


@pytest.fixture(scope="module")
def metrics():
    out = score(history(METRIC_ROWS), predictions(METRIC_PREDS))
    return {name: df.set_index("scope") for name, df in out.items()}


def test_scopes_are_each_scored_season_and_pooled_held_out(metrics):
    assert set(metrics["metric_2_distance_to_close"].index) == {"2021", "2023", "2024", "pooled"}
    assert (metrics["metric_2_distance_to_close"]["close_source"] == "cbbd").all()


def test_distance_to_close(metrics):
    m = metrics["metric_2_distance_to_close"]
    assert m.loc["2023", "n_games"] == 4
    assert m.loc["2023", "mean_abs_distance_to_close"] == pytest.approx((2 + 2 + 5 + 0) / 4)
    assert m.loc["2023", "n_left_out_no_close"] == 1
    assert m.loc["pooled", "n_games"] == 5  # 2023 and 2024; 2021 is never pooled
    assert m.loc["pooled", "mean_abs_distance_to_close"] == pytest.approx(11 / 5)
    assert m.loc["2021", "mean_abs_distance_to_close"] == pytest.approx(1.0)


def test_error_vs_results(metrics):
    m = metrics["metric_3_error_vs_results"]
    assert m.loc["2023", "n_games"] == 4
    assert m.loc["2023", "mae_prediction"] == pytest.approx((5 + 3 + 5 + 1) / 4)
    assert m.loc["2023", "mae_closing_line"] == pytest.approx((7 + 1 + 0 + 1) / 4)
    assert m.loc["2023", "n_all_games"] == 5
    assert m.loc["2023", "mae_prediction_all_games"] == pytest.approx((5 + 3 + 5 + 1 + 7) / 5)
    assert m.loc["pooled", "mae_prediction"] == pytest.approx(19 / 5)
    assert m.loc["pooled", "mae_closing_line"] == pytest.approx(16 / 5)


def test_ats(metrics):
    m = metrics["metric_4_ats"]
    row = m.loc["2023"]
    assert (row["wins"], row["losses"], row["pushes"]) == (1, 1, 1)
    assert row["win_pct"] == pytest.approx(0.5)  # the push is left out
    assert row["n_left_out_no_close"] == 1
    assert row["n_left_out_no_side"] == 1
    pooled = m.loc["pooled"]
    assert (pooled["wins"], pooled["losses"], pooled["pushes"]) == (2, 1, 1)
    assert pooled["win_pct"] == pytest.approx(2 / 3)
    assert m.loc["2021", "wins"] == 1


def test_clv(metrics):
    m = metrics["metric_1_clv"]
    row = m.loc["2023"]  # CLV values +1, -1, +2
    assert row["n_games"] == 3
    assert row["mean_clv"] == pytest.approx(2 / 3)
    sd = math.sqrt(((1 - 2 / 3) ** 2 + (-1 - 2 / 3) ** 2 + (2 - 2 / 3) ** 2) / 2)
    assert row["ci95_low"] == pytest.approx(2 / 3 - 1.96 * sd / math.sqrt(3))
    assert row["ci95_high"] == pytest.approx(2 / 3 + 1.96 * sd / math.sqrt(3))
    assert row["share_positive"] == pytest.approx(2 / 3)
    assert row["n_left_out_no_open"] == 2
    assert row["n_left_out_no_close"] == 0
    pooled = m.loc["pooled"]  # adds game 26, CLV 0
    assert pooled["n_games"] == 4
    assert pooled["mean_clv"] == pytest.approx(0.5)
    assert pooled["share_positive"] == pytest.approx(0.5)
    assert m.loc["2021", "n_games"] == 0 and m.loc["2021", "n_left_out_no_open"] == 1


def test_calibration_and_tier_tables_are_empty_without_cover_probabilities(metrics):
    for name in ("metric_5a_brier", "metric_5b_reliability", "metric_6_tiers"):
        assert len(metrics[name]) == 0


def test_every_metric_runs_and_reports_each_predictor():
    hist = history(METRIC_ROWS)
    both = pd.concat(
        [predictions(METRIC_PREDS), predictions(METRIC_PREDS, predictor="torvik_baseline")]
    )
    out = score(hist, both)
    assert set(out) == set(METRICS)
    assert set(out["metric_2_distance_to_close"]["predictor"]) == {"test_model", "torvik_baseline"}


def test_predictors_are_always_scored_on_the_same_games():
    # The baseline can't price game 21 (no rating); the model can't price game 26.
    # Both are scored only on the games both predict: 22, 23, 24, 25 in 2023, none in 2024.
    hist = history(METRIC_ROWS)
    base = predictions({**METRIC_PREDS, 21: float("nan")}, predictor="torvik_baseline")
    model = predictions({**METRIC_PREDS, 26: float("nan")}, predictor="model")
    out = score(hist, pd.concat([base, model]))

    dist = out["metric_2_distance_to_close"].set_index(["predictor", "scope"])
    for predictor in ("torvik_baseline", "model"):
        assert dist.loc[(predictor, "2023"), "n_games"] == 3  # 22, 23, 24 have a close
        assert dist.loc[(predictor, "pooled"), "n_games"] == 3
        assert dist.loc[(predictor, "pooled"), "mean_abs_distance_to_close"] == pytest.approx(
            (2 + 5 + 0) / 3
        )
    assert ("model", "2024") not in dist.index and ("torvik_baseline", "2024") not in dist.index

    cov = out["coverage"].set_index(["predictor", "scope"])
    assert cov.loc[("model", "pooled"), "n_final_games_predicted"] == 5  # 21 to 25
    assert cov.loc[("model", "pooled"), "n_scored_games"] == 4
    assert cov.loc[("model", "pooled"), "n_left_out_another_predictor_has_none"] == 1
    assert cov.loc[("torvik_baseline", "pooled"), "n_final_games_predicted"] == 5  # 22 to 26
    assert cov.loc[("torvik_baseline", "pooled"), "n_scored_games"] == 4
    assert cov.loc[("torvik_baseline", "2024"), "n_scored_games"] == 0
    assert cov.loc[("model", "2023"), "n_with_close"] == 3
    assert cov.loc[("model", "2023"), "n_with_open_and_close"] == 2  # 22 and 24


def test_a_game_predicted_twice_by_one_predictor_is_refused():
    twice = pd.concat([predictions(METRIC_PREDS), predictions({21: 6})])
    with pytest.raises(ValueError, match="repeated"):
        score(history(METRIC_ROWS), twice)


def test_the_command_line_run_refuses_until_the_backtest_close_is_decided(tmp_path, capsys):
    # Task 1.2 decides the closing line. Until then no real number may be produced.
    # When 1.2 is settled and the flag is flipped, delete this test in the same PR.
    assert run.BACKTEST_CLOSE_DECIDED is False
    assert run.main(["--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 1
    assert "Not run" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


# Calibration and tiers. Every game: prediction 5, close -3 (implied home margin 3), so
# the side is always home, and open -2, so CLV is always +1. A margin of 10 is a win, 0 a
# loss, 3 a push.
#
#   id  cover_prob result | group    | tier
#   41  0.49       loss   | below 50 | none
#   42  0.51       win    | 50-52    | none
#   43  0.51       loss   | 50-52    | none
#   44  0.52       win    | 52-54    | none   (a group includes its lower edge)
#   45  0.53       win    | 52-54    | lean   (53% is a Lean)
#   46  0.55       loss   | 54-56    | lean
#   47  0.56       win    | 56-58    | pick   (56% is a Pick)
#   48  0.57       win    | 56-58    | pick
#   49  0.57       loss   | 56-58    | pick
#   50  0.58       push   | (out)    | pick   (pushes: counted, left out of rates)
#   51  0.60       win    | 60+      | pick
CAL = {
    41: (0.49, 0),
    42: (0.51, 10),
    43: (0.51, 0),
    44: (0.52, 10),
    45: (0.53, 10),
    46: (0.55, 0),
    47: (0.56, 10),
    48: (0.57, 10),
    49: (0.57, 0),
    50: (0.58, 3),
    51: (0.60, 10),
}


@pytest.fixture(scope="module")
def cal_metrics():
    hist = history([game(g, 2023, margin=m, close=-3.0, open_=-2.0) for g, (_p, m) in CAL.items()])
    preds = predictions(dict.fromkeys(CAL, 5), cover_prob={g: p for g, (p, _m) in CAL.items()})
    return score(hist, preds)


def test_brier_score(cal_metrics):
    m = cal_metrics["metric_5a_brier"].set_index("scope")
    decided = [(p, 1 if margin == 10 else 0) for p, margin in CAL.values() if margin != 3]
    expected = sum((p - won) ** 2 for p, won in decided) / len(decided)
    assert m.loc["2023", "n_games"] == 10
    assert m.loc["2023", "brier_score"] == pytest.approx(expected)
    assert m.loc["pooled", "brier_score"] == pytest.approx(expected)


def test_reliability_table(cal_metrics):
    m = cal_metrics["metric_5b_reliability"]
    m = m[m["scope"] == "2023"].set_index("cover_prob_group")
    # All six fixed groups are listed even when empty; "below 50%" only because of game 41.
    groups = ["below 50%", "50-52%", "52-54%", "54-56%", "56-58%", "58-60%", "60%+"]
    assert list(m.index) == groups
    assert m["n_games"].tolist() == [1, 2, 2, 1, 3, 0, 1]
    assert pd.isna(m.loc["58-60%", "actual_cover_rate"])
    assert m.loc["50-52%", "predicted_cover_rate"] == pytest.approx(0.51)
    assert m.loc["50-52%", "actual_cover_rate"] == pytest.approx(0.5)
    assert m.loc["52-54%", "predicted_cover_rate"] == pytest.approx(0.525)
    assert m.loc["52-54%", "actual_cover_rate"] == pytest.approx(1.0)
    assert m.loc["56-58%", "predicted_cover_rate"] == pytest.approx((0.56 + 0.57 + 0.57) / 3)
    assert m.loc["56-58%", "actual_cover_rate"] == pytest.approx(2 / 3)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def test_per_tier_record(cal_metrics):
    m = cal_metrics["metric_6_tiers"]
    m = m[m["scope"] == "2023"].set_index("tier")
    assert list(m.index) == ["pick", "lean"]

    pick = m.loc["pick"]
    assert (pick["n_calls"], pick["wins"], pick["losses"], pick["pushes"]) == (5, 3, 1, 1)
    assert pick["win_pct"] == pytest.approx(0.75)
    low, high = wilson(3, 4)
    assert pick["wilson95_low"] == pytest.approx(low)
    assert pick["wilson95_high"] == pytest.approx(high)
    assert (low, high) == pytest.approx((0.3006, 0.9544), abs=1e-4)  # a known value
    assert pick["gap_to_break_even"] == pytest.approx(0.75 - 0.524)
    assert pick["n_clv_games"] == 5 and pick["mean_clv"] == pytest.approx(1.0)
    assert pick["n_clv_left_out_side_differs"] == 0
    assert pick["n_left_out_no_close_or_side"] == 0

    lean = m.loc["lean"]
    assert (lean["n_calls"], lean["wins"], lean["losses"], lean["pushes"]) == (2, 1, 1, 0)
    assert lean["win_pct"] == pytest.approx(0.5)


def test_tier_clv_only_counts_calls_whose_side_is_the_same_at_open_and_close():
    # Three Picks, all predicted 5 and all home wins against a close of -3 (implied 3).
    #   61: open -2 (implied 2): home at the open too. CLV = 3 - 2 = +1. Counted.
    #   62: open -7 (implied 7): AWAY at the open, so the line crossed the prediction.
    #       Its CLV (+4) belongs to the away side, not to this home call. Left out.
    #   63: no close at all: can't be graded. Left out of the record and counted.
    hist = history(
        [
            game(61, 2023, margin=10, close=-3.0, open_=-2.0),
            game(62, 2023, margin=10, close=-3.0, open_=-7.0),
            game(63, 2023, margin=10),
        ]
    )
    preds = predictions(dict.fromkeys([61, 62, 63], 5), cover_prob=dict.fromkeys([61, 62, 63], 0.6))
    out = score(hist, preds)
    pick = out["metric_6_tiers"].set_index(["scope", "tier"]).loc[("2023", "pick")]
    assert (pick["n_calls"], pick["wins"]) == (2, 2)
    assert pick["n_clv_games"] == 1 and pick["mean_clv"] == pytest.approx(1.0)
    assert pick["n_clv_left_out_side_differs"] == 1
    assert pick["n_left_out_no_close_or_side"] == 1
    # The overall CLV metric still counts both games, each for its side at the open.
    clv = out["metric_1_clv"].set_index("scope").loc["2023"]
    assert clv["n_games"] == 2 and clv["mean_clv"] == pytest.approx((1 + 4) / 2)
