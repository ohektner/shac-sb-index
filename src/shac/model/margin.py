"""The margin model: predicted home margin from point-in-time ratings (task 1.8).

What it is: a straight-line fit (ordinary least squares, no intercept) of the final home
margin on a few inputs built in sql/model/features.sql. The simplest version, "core", is
the Torvik baseline with two learned numbers instead of fixed ones:

    pred_margin = slope * rating_margin + home_court * home

The baseline fixes slope at 1. Here both are fit on past seasons.

What it learns from: final scores, never a line (docs/DECISIONS.md, 2026-11-11).

How it is tested on the past (docs/PREREGISTRATION.md, "Seasons"):

- walk_forward: each scored season is predicted by a fit on the eligible seasons before
  it. 2020-21 is never trained on.
- leave_one_season_out: within a set of training seasons, each season is predicted by a
  fit on the others. Used to pick the inputs (compare_feature_sets) and, in task 1.9, to
  give the calibration step edges that no model saw during fitting.

Which inputs are used is the FEATURE_SET constant below. It was chosen by
compare_feature_sets on the three train-only seasons (2019, 2020, 2022), so the held-out
seasons had no say in it.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from shac.tables.build import REPO_ROOT, query_df

FEATURES_SQL = REPO_ROOT / "sql" / "model" / "features.sql"
SEASONS_SQL = REPO_ROOT / "sql" / "scorecard" / "seasons.sql"

PREDICTOR = "margin_model"

# Candidate input lists. "core" is the starting point; each of the others adds ONE idea
# to it, so a comparison says what that idea is worth.
FEATURE_SETS: dict[str, list[str]] = {
    # The baseline's two parts, with learned weights.
    "core": ["rating_margin", "home"],
    # Are ratings worth less (or more) early in the season, when they lean on preseason
    # guesses? Lets the slope differ in November and in December.
    "early": ["rating_margin", "home", "rm_nov", "rm_dec"],
    # Does a rest advantage matter?
    "rest": ["rating_margin", "home", "rest_diff"],
    # Do offense and defense gaps count the same?
    "split": ["off_margin", "def_margin", "home"],
    # Is home court worth a different amount in conference games?
    "conf": ["rating_margin", "home", "home_conf"],
}

# The inputs the model uses. Change it only with a DECISIONS.md entry and the
# compare_feature_sets output that supports it.
FEATURE_SET = "core"

ALL_FEATURES = sorted({name for names in FEATURE_SETS.values() for name in names})


def season_roles() -> pd.DataFrame:
    """Season -> role ('train_only', 'excluded', 'held_out'), from sql/scorecard/seasons.sql."""
    con = duckdb.connect()
    con.execute(SEASONS_SQL.read_text(encoding="utf-8"))
    out = query_df(con, "SELECT season, role FROM season_roles ORDER BY season")
    con.close()
    return out


def training_seasons(test_season: int, roles: pd.DataFrame | None = None) -> list[int]:
    """The seasons a fit for `test_season` may learn from: earlier, and never 2020-21.

    Works for the live season too (2027 learns from every eligible season before it).
    """
    roles = season_roles() if roles is None else roles
    ok = (roles["season"] < test_season) & (roles["role"] != "excluded")
    return sorted(int(s) for s in roles.loc[ok, "season"])


def build_features(game_history: pd.DataFrame) -> pd.DataFrame:
    """One row per game: the model's inputs (sql/model/features.sql) plus the final margin.

    `home_margin` is the label. It is joined on here, outside the feature query, so the
    query itself never sees a score.
    """
    con = duckdb.connect()
    con.register("game_history", game_history)
    features = query_df(con, FEATURES_SQL.read_text(encoding="utf-8"))
    con.close()
    label = game_history[["game_id", "home_margin"]]
    return features.merge(label, on="game_id", how="left", validate="one_to_one")


@dataclass
class MarginFit:
    """A fitted model: the input names, one weight per input, and what it was fit on."""

    feature_names: list[str]
    weights: np.ndarray
    train_seasons: list[int]
    n_train_games: int

    def predict(self, features: pd.DataFrame) -> pd.Series:
        """Predicted home margin. Empty where any input is missing."""
        x = features[self.feature_names].astype("float64")
        usable = x.notna().all(axis=1)
        pred = pd.Series(np.nan, index=features.index, dtype="float64")
        pred[usable] = x[usable].to_numpy() @ self.weights
        return pred

    def weights_dict(self) -> dict[str, float]:
        return dict(zip(self.feature_names, (float(w) for w in self.weights), strict=True))


def fit(features: pd.DataFrame, train_seasons: list[int], feature_set: str) -> MarginFit:
    """Least-squares fit on the final games of `train_seasons` that have every input.

    No intercept: with no rating gap at a neutral site there is nothing to tell the two
    teams apart, so the prediction should be zero.
    """
    names = FEATURE_SETS[feature_set]
    rows = features[features["season"].isin(train_seasons)]
    rows = rows.dropna(subset=[*names, "home_margin"])
    if rows.empty:
        raise ValueError(f"no usable training games in seasons {train_seasons}")
    model = LinearRegression(fit_intercept=False)
    model.fit(rows[names].to_numpy(dtype="float64"), rows["home_margin"].to_numpy("float64"))
    return MarginFit(names, model.coef_, sorted(train_seasons), len(rows))


def walk_forward(
    game_history: pd.DataFrame, feature_set: str = FEATURE_SET
) -> tuple[pd.DataFrame, dict[int, MarginFit]]:
    """Predict every scored season (held out, and 2020-21) from the seasons before it.

    Returns the predictions in the scorecard's format (predictor, game_id, pred_margin,
    cover_prob; cover_prob is empty until task 1.9) with how each was made, and the fit
    used for each season.
    """
    features = build_features(game_history)
    roles = season_roles()
    scored = roles.loc[roles["role"].isin(["held_out", "excluded"]), "season"]
    frames, fits = [], {}
    for season in (int(s) for s in scored):
        train = training_seasons(season, roles)
        fits[season] = fit(features, train, feature_set)
        rows = features[features["season"] == season]
        frames.append(
            pd.DataFrame(
                {
                    "predictor": PREDICTOR,
                    "game_id": rows["game_id"],
                    "pred_margin": fits[season].predict(rows),
                    "cover_prob": np.nan,
                    "feature_set": feature_set,
                    "n_train_games": fits[season].n_train_games,
                    "train_seasons": " ".join(str(s) for s in train),
                }
            )
        )
    out = pd.concat(frames, ignore_index=True).sort_values("game_id", ignore_index=True)
    return out, fits


def leave_one_season_out(
    features: pd.DataFrame, seasons: list[int], feature_set: str = FEATURE_SET
) -> pd.DataFrame:
    """Predict each of `seasons` from a fit on the others.

    Returns game_id, season, pred_margin and home_margin for those seasons' games. No
    game's prediction comes from a fit that saw that game, or anything from its season.
    """
    frames = []
    for season in seasons:
        others = [s for s in seasons if s != season]
        rows = features[features["season"] == season]
        frames.append(
            pd.DataFrame(
                {
                    "game_id": rows["game_id"],
                    "season": season,
                    "pred_margin": fit(features, others, feature_set).predict(rows),
                    "home_margin": rows["home_margin"],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def compare_feature_sets(features: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """How well each candidate input list predicts FINAL SCORES, leave-one-season-out.

    Scored on games every candidate can predict. One row per candidate:

      mae            mean absolute error against the final margin, all seasons together
      mae_<season>   the same, one season at a time
      gain_vs_core   core's error minus this one's (positive = better than core)
      gain_se        the standard error of that gain (game-by-game differences). It
                     treats games as independent, but a season's predictions share one
                     fit, so it runs a little small. That is why the rule below also
                     asks for a win in every season.
      seasons_better how many of the seasons it beat core in

    The rule for keeping an added idea, fixed before the first run: its gain is at least
    2 standard errors AND it beats core in every season. Otherwise the simpler model stays.
    """
    errors = {}
    for name in FEATURE_SETS:
        loso = leave_one_season_out(features, seasons, name).set_index("game_id")
        errors[name] = (loso["pred_margin"] - loso["home_margin"]).abs()
    err = pd.DataFrame(errors).dropna()
    season_of = features.set_index("game_id")["season"].reindex(err.index)
    rows = []
    for name in FEATURE_SETS:
        gain = err["core"] - err[name]
        by_season = err[name].groupby(season_of).mean()
        gain_by_season = gain.groupby(season_of).mean()
        row = {"feature_set": name, "n_games": len(err), "mae": err[name].mean()}
        row.update({f"mae_{int(s)}": v for s, v in by_season.items()})
        row["gain_vs_core"] = gain.mean()
        row["gain_se"] = gain.std(ddof=1) / np.sqrt(len(gain))
        row["seasons_better"] = int((gain_by_season > 0).sum())
        rows.append(row)
    return pd.DataFrame(rows)
