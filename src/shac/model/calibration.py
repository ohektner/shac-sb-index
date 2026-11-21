"""Calibration and tiers: turn an edge against the close into a cover probability (task 1.9).

Every rule here is fixed in docs/CALIBRATION.md, written before any number against a line
was computed. Nothing in this module makes a choice of its own.

Definitions (per predictor, per game; CALIBRATION.md section 1):

    close_margin = -close_home_spread
    edge         = pred_margin - close_margin = pred_margin + close_home_spread
    side         = sign(edge)                   (+1 home, -1 away, none at 0)
    x            = abs(edge)                    (points, no cap)
    ats_margin   = home_margin + close_home_spread
    covered      = 1 if side * ats_margin > 0, 0 if < 0 (not defined on a push)
    cover_prob   = 1 / (1 + exp(-b * x))        (one b per predictor per scored season)

The curve has one parameter and no intercept, so zero edge is exactly 50%. `b` may come
out zero or negative: it is kept as it is (never clamped, never flipped, never refit).

For scored season S, `b` is fit on the fit seasons `training_seasons(S)` (never 2020-21),
using out-of-sample margins: each fit season is predicted by a fit on the other fit
seasons only (leave one season out).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from shac.model.margin import FEATURE_SET, PREDICTOR, leave_one_season_out, training_seasons

BASELINE = "torvik_baseline"
PREDICTORS = (BASELINE, PREDICTOR)

PICK_CUTOFF = 0.56
LEAN_CUTOFF = 0.53

MIN_FIT_SEASONS = 2
MIN_FIT_GAMES = 5000

MAX_ITERATIONS = 100
TOLERANCE = 1e-10

FIT_COLUMNS = [
    "predictor",
    "scored_season",
    "fit_seasons",
    "status",
    "n_fit_games",
    "n_left_out_no_close",
    "n_left_out_no_side",
    "n_left_out_push",
    "fit_cover_rate",
    "slope_b",
    "edge_for_lean",
    "edge_for_pick",
    "sigma",
]


def fit_slope(x, covered) -> float:
    """Maximum-likelihood slope, no penalty, no intercept: the exact loop in section 3."""
    x = np.asarray(x, dtype="float64")
    covered = np.asarray(covered, dtype="float64")
    b = 0.0
    for _ in range(MAX_ITERATIONS):
        p = 1.0 / (1.0 + np.exp(-b * x))
        step = float(np.sum(x * (covered - p)) / np.sum(x * x * p * (1.0 - p)))
        b += step
        if abs(step) < TOLERANCE:
            return b
    raise RuntimeError(f"calibration slope did not converge in {MAX_ITERATIONS} steps")


def cover_prob(x, b: float):
    """1 / (1 + exp(-b * x)). Works on a number or an array."""
    return 1.0 / (1.0 + np.exp(-b * np.asarray(x, dtype="float64")))


def tier_for(prob: float | None) -> str | None:
    """'pick', 'lean' or 'none', on the unrounded number. Same rule as scored_games.sql."""
    if prob is None or (isinstance(prob, float) and math.isnan(prob)):
        return None
    if prob >= PICK_CUTOFF:
        return "pick"
    if prob >= LEAN_CUTOFF:
        return "lean"
    return "none"


def baseline_leave_one_season_out(features: pd.DataFrame, fit_seasons: list[int]) -> pd.DataFrame:
    """The Torvik baseline, each fit season predicted from the OTHER fit seasons.

    For fit season T: H_T = mean of (home_margin - rating_margin) over games in the other
    fit seasons with home = 1 and both values present. Then
    pred_margin = rating_margin + H_T * home (empty if rating_margin or home is empty).
    Returns game_id, season, pred_margin, home_margin, like leave_one_season_out.
    """
    frames = []
    for season in fit_seasons:
        others = features[features["season"].isin([s for s in fit_seasons if s != season])]
        train = others[
            (others["home"] == 1) & others["home_margin"].notna() & others["rating_margin"].notna()
        ]
        home_court = float((train["home_margin"] - train["rating_margin"]).mean())
        rows = features[features["season"] == season]
        frames.append(
            pd.DataFrame(
                {
                    "game_id": rows["game_id"],
                    "season": season,
                    "pred_margin": rows["rating_margin"] + home_court * rows["home"],
                    "home_margin": rows["home_margin"],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def out_of_sample_margins(
    features: pd.DataFrame, predictor: str, fit_seasons: list[int]
) -> pd.DataFrame:
    """Leave-one-season-out margins for the fit seasons, for either predictor.

    If some fold has nothing to learn from (only one fit season has games), no game gets
    an out-of-sample margin, for either predictor: the fit is then `not_fit`.
    """
    if predictor == BASELINE:
        return baseline_leave_one_season_out(features, fit_seasons)
    if predictor != PREDICTOR:
        raise ValueError(f"unknown predictor {predictor!r}")
    try:
        return leave_one_season_out(features, fit_seasons, FEATURE_SET)
    except ValueError:  # a fold with no usable training games (margin.fit)
        rows = features[features["season"].isin(fit_seasons)]
        return pd.DataFrame(
            {
                "game_id": rows["game_id"],
                "season": rows["season"],
                "pred_margin": np.nan,
                "home_margin": rows["home_margin"],
            }
        )


def fit_games(
    features: pd.DataFrame, lines: pd.DataFrame, predictor: str, scored_season: int
) -> tuple[pd.DataFrame, dict]:
    """The games a fit for (predictor, scored_season) uses, and what each rule dropped.

    `features` is build_features(game_history); `lines` has game_id and close_home_spread
    (the scorecard's close). Returns the fit games (season, x, covered) and a dict with
    fit_seasons, the three left-out counts and sigma.

    Base: games in the fit seasons with a home_margin and a pred_margin. From those, in
    order: no close, then edge = 0 (no side), then ats_margin = 0 (push).
    """
    seasons = training_seasons(scored_season)
    margins = out_of_sample_margins(features, predictor, seasons)
    games = margins.merge(
        lines[["game_id", "close_home_spread"]], on="game_id", how="left", validate="one_to_one"
    )
    final = games[games["home_margin"].notna()]
    priced = final[final["pred_margin"].notna()]

    no_close = priced["close_home_spread"].isna()
    with_close = priced[~no_close]
    edge = with_close["pred_margin"] + with_close["close_home_spread"]
    no_side = edge == 0
    sided = with_close[~no_side]
    edge = edge[~no_side]
    ats_margin = sided["home_margin"] + sided["close_home_spread"]
    push = ats_margin == 0
    used = sided[~push]
    side = np.sign(edge[~push])
    covered = (side * ats_margin[~push] > 0).astype("int64")

    out = pd.DataFrame(
        {
            "game_id": used["game_id"].to_numpy(),
            "season": used["season"].to_numpy(),
            "x": edge[~push].abs().to_numpy(),
            "covered": covered.to_numpy(),
        }
    )

    # sigma: over final games with a close in the fit seasons; no prediction needed,
    # pushes and zero-edge games included, no mean subtracted.
    closed = final[final["close_home_spread"].notna()]
    resid = closed["home_margin"] + closed["close_home_spread"]
    sigma = float(np.sqrt(np.mean(resid**2))) if len(resid) else None

    counts = {
        "fit_seasons": " ".join(str(s) for s in seasons),
        "n_left_out_no_close": int(no_close.sum()),
        "n_left_out_no_side": int(no_side.sum()),
        "n_left_out_push": int(push.sum()),
        "sigma": sigma,
    }
    return out, counts


def fit_row(games: pd.DataFrame, counts: dict, predictor: str, scored_season: int) -> dict:
    """The calibration_fit.csv row from fit_games' output. See CALIBRATION.md 2-3, 6."""
    n_seasons = games["season"].nunique()
    row = {
        "predictor": predictor,
        "scored_season": scored_season,
        "fit_seasons": counts["fit_seasons"],
        "status": "not_fit",
        "n_fit_games": len(games),
        "n_left_out_no_close": counts["n_left_out_no_close"],
        "n_left_out_no_side": counts["n_left_out_no_side"],
        "n_left_out_push": counts["n_left_out_push"],
        "fit_cover_rate": float(games["covered"].mean()) if len(games) else None,
        "slope_b": None,
        "edge_for_lean": None,
        "edge_for_pick": None,
        "sigma": counts["sigma"],
    }
    if n_seasons < MIN_FIT_SEASONS or len(games) < MIN_FIT_GAMES:
        return row
    b = fit_slope(games["x"], games["covered"])
    row["status"] = "fit"
    row["slope_b"] = b
    if b > 0:
        row["edge_for_lean"] = math.log(LEAN_CUTOFF / (1 - LEAN_CUTOFF)) / b
        row["edge_for_pick"] = math.log(PICK_CUTOFF / (1 - PICK_CUTOFF)) / b
    return row


def calibrate(
    features: pd.DataFrame, lines: pd.DataFrame, predictor: str, scored_season: int
) -> dict:
    """One calibration_fit.csv row for (predictor, scored_season)."""
    games, counts = fit_games(features, lines, predictor, scored_season)
    return fit_row(games, counts, predictor, scored_season)


def calibrate_all(
    features: pd.DataFrame, lines: pd.DataFrame, scored_seasons: list[int]
) -> tuple[pd.DataFrame, dict[tuple[str, int], pd.DataFrame]]:
    """calibration_fit rows for both predictors and every season, plus each fit's games.

    The two predictors need the same inputs, so their fit counts must be equal
    (CALIBRATION.md section 5). If they differ this raises: stop and report to Owen.
    """
    rows, games_by_fit = [], {}
    for season in scored_seasons:
        for predictor in PREDICTORS:
            games, counts = fit_games(features, lines, predictor, season)
            games_by_fit[(predictor, season)] = games
            rows.append(fit_row(games, counts, predictor, season))
    fits = pd.DataFrame(rows, columns=FIT_COLUMNS)
    count_cols = ["n_fit_games", "n_left_out_no_close", "n_left_out_no_side", "n_left_out_push"]
    differs = fits.groupby("scored_season")[count_cols].nunique().gt(1).any(axis=1)
    if differs.any():
        seasons = ", ".join(str(s) for s in differs[differs].index)
        raise ValueError(
            f"the two predictors' fit counts differ for {seasons}; stop and report to Owen "
            "(docs/CALIBRATION.md, section 5)"
        )
    return fits, games_by_fit


def isotonic_check(
    scored: pd.DataFrame, games_by_fit: dict[tuple[str, int], pd.DataFrame]
) -> pd.DataFrame:
    """calibration_check.csv: the logistic curve next to an isotonic one (section 6).

    `scored` has one row per decided scored game (wins and losses with a cover_prob):
    predictor, scope, season, x, covered, cover_prob. For each (predictor, season) an
    isotonic curve is fit on that fit's games and applied to the same scored games.
    n_tier_differs counts those games whose tier would change. Printed, nothing more:
    the logistic curve is the one used.
    """
    from sklearn.isotonic import IsotonicRegression

    frames = []
    for (predictor, season), rows in scored.groupby(["predictor", "season"]):
        games = games_by_fit[(predictor, int(season))]
        iso = IsotonicRegression(increasing=True, y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(games["x"].to_numpy("float64"), games["covered"].to_numpy("float64"))
        rows = rows.copy()
        rows["iso_prob"] = iso.predict(rows["x"].to_numpy("float64"))
        frames.append(rows)
    if not frames:
        return pd.DataFrame(
            columns=[
                "predictor",
                "scope",
                "n_games",
                "brier_logistic",
                "brier_isotonic",
                "brier_coin_flip",
                "n_tier_differs",
            ]
        )
    rows = pd.concat(frames, ignore_index=True)
    rows["sq_log"] = (rows["cover_prob"] - rows["covered"]) ** 2
    rows["sq_iso"] = (rows["iso_prob"] - rows["covered"]) ** 2
    rows["differs"] = [
        tier_for(a) != tier_for(b)
        for a, b in zip(rows["cover_prob"], rows["iso_prob"], strict=True)
    ]
    out = (
        rows.groupby(["predictor", "scope"])
        .agg(
            n_games=("covered", "size"),
            brier_logistic=("sq_log", "mean"),
            brier_isotonic=("sq_iso", "mean"),
            n_tier_differs=("differs", "sum"),
        )
        .reset_index()
    )
    out.insert(5, "brier_coin_flip", 0.25)
    out["n_tier_differs"] = out["n_tier_differs"].astype("int64")
    return out


def apply_cover_prob(
    predictions: pd.DataFrame,
    lines: pd.DataFrame,
    season_of: pd.Series,
    slopes: dict[tuple[str, int], float | None],
) -> pd.DataFrame:
    """Fill `cover_prob` in the scorecard's predictions (CALIBRATION.md section 4).

    For each game: cover_prob = 1 / (1 + exp(-b_S * x)) when it has a close, edge != 0 and
    its (predictor, season) has a slope; empty otherwise. Pushes keep their cover_prob.
    `season_of` maps game_id -> season; `slopes` maps (predictor, season) -> b or None.
    """
    out = predictions.copy()
    close = out["game_id"].map(lines.set_index("game_id")["close_home_spread"])
    season = out["game_id"].map(season_of)
    edge = out["pred_margin"] + close
    b = pd.Series(
        [
            slopes.get((p, int(s))) if pd.notna(s) else None
            for p, s in zip(out["predictor"], season, strict=True)
        ],
        index=out.index,
        dtype="float64",
    )
    usable = edge.notna() & (edge != 0) & b.notna()
    probs = pd.Series(np.nan, index=out.index, dtype="float64")
    probs[usable] = 1.0 / (1.0 + np.exp(-b[usable] * edge[usable].abs()))
    out["cover_prob"] = probs
    return out
