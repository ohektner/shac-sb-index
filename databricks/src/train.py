"""Train the margin model and log every candidate to MLflow (docs/DATABRICKS.md, D2).

Mirrors shac.model.margin: ordinary least squares with no intercept, fit on final scores
(never a line). Two parts:

1. Input selection, leave-one-season-out on the train-only seasons (2019, 2020, 2022):
   "core" (v1) and the four ideas that were tried and not kept. Each is an MLflow run
   with its errors and whether it met the pre-set rule (gain >= 2 standard errors AND
   better than core in every season).
2. Walk-forward fits of v1 for each scored season and the live season (2027), each
   learning only from eligible earlier seasons. The 2027 fit is v1 as frozen for the
   live season; it is registered in Unity Catalog as `<catalog>.<schema>.margin_model`
   with the alias `champion`.

Writes gold_feature_comparison, gold_model_fits and gold_model_predictions (scored
seasons only). Arguments: catalog, schema.
"""

import sys

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from pyspark.sql import SparkSession

FEATURE_SETS = {
    "core": ["rating_margin", "home"],
    "early": ["rating_margin", "home", "rm_nov", "rm_dec"],
    "rest": ["rating_margin", "home", "rest_diff"],
    "split": ["off_margin", "def_margin", "home"],
    "conf": ["rating_margin", "home", "home_conf"],
}
WHAT_IT_TESTS = {
    "core": "v1: the Torvik baseline's two parts with learned weights",
    "early": "ratings worth a different amount in November and December",
    "rest": "a rest-days advantage",
    "split": "offense and defense gaps weighted separately",
    "conf": "home court worth a different amount in conference games",
}
FEATURE_SET = "core"
SELECTION_SEASONS = [2019, 2020, 2022]
LIVE_SEASON = 2027
PREDICTOR = "margin_model"


def fit(features: pd.DataFrame, seasons: list[int], names: list[str]) -> tuple[np.ndarray, int]:
    rows = features[features["season"].isin(seasons)].dropna(subset=[*names, "home_margin"])
    if rows.empty:
        raise ValueError(f"no usable training games in seasons {seasons}")
    x = rows[names].to_numpy(dtype="float64")
    y = rows["home_margin"].to_numpy(dtype="float64")
    weights, *_ = np.linalg.lstsq(x, y, rcond=None)
    return weights, len(rows)


def predict(features: pd.DataFrame, names: list[str], weights: np.ndarray) -> pd.Series:
    x = features[names].astype("float64")
    usable = x.notna().all(axis=1)
    pred = pd.Series(np.nan, index=features.index, dtype="float64")
    pred[usable] = x[usable].to_numpy() @ weights
    return pred


def leave_one_season_out(features: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    frames = []
    for season in SELECTION_SEASONS:
        others = [s for s in SELECTION_SEASONS if s != season]
        weights, _ = fit(features, others, names)
        rows = features[features["season"] == season]
        frames.append(
            pd.DataFrame(
                {
                    "game_id": rows["game_id"],
                    "pred_margin": predict(rows, names, weights),
                    "home_margin": rows["home_margin"],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def compare_feature_sets(features: pd.DataFrame) -> pd.DataFrame:
    errors = {}
    for name, names in FEATURE_SETS.items():
        loso = leave_one_season_out(features, names).set_index("game_id")
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


def training_seasons(roles: pd.DataFrame, test_season: int) -> list[int]:
    ok = (roles["season"] < test_season) & (roles["role"] != "excluded")
    return sorted(int(s) for s in roles.loc[ok, "season"])


class MarginModel(mlflow.pyfunc.PythonModel):
    """v1 as frozen for a season: predicted home margin = features @ weights."""

    def __init__(self, names: list[str], weights: list[float]):
        self.names = names
        self.weights = np.asarray(weights, dtype="float64")

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.Series:
        return predict(model_input, self.names, self.weights)


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    features = spark.sql(
        f"SELECT f.*, g.home_margin FROM {ns}.gold_model_features f "
        f"JOIN {ns}.gold_game_history g USING (game_id)"
    ).toPandas()
    for col in features.columns:
        if col not in ("game_id", "season"):
            features[col] = features[col].astype("float64")
    roles = spark.table(f"{ns}.ref_season_roles").toPandas()
    user = spark.sql("SELECT current_user()").first()[0]
    mlflow.set_experiment(f"/Users/{user}/shac-margin-model")

    # 1. Input selection.
    comparison = compare_feature_sets(features)
    with mlflow.start_run(run_name="input selection, leave one season out (2019, 2020, 2022)"):
        mlflow.set_tag(
            "rule", "keep an idea only if gain >= 2 SE and better than core every season"
        )
        for row in comparison.to_dict("records"):
            name = row["feature_set"]
            kept = name == "core" or (
                row["gain_vs_core"] >= 2 * row["gain_se"]
                and row["seasons_better"] == len(SELECTION_SEASONS)
            )
            with mlflow.start_run(run_name=name, nested=True):
                mlflow.log_params({"feature_set": name, "inputs": ",".join(FEATURE_SETS[name])})
                mlflow.set_tags({"tests": WHAT_IT_TESTS[name], "kept": str(kept).lower()})
                mlflow.log_metrics({k: float(v) for k, v in row.items() if k != "feature_set"})
    spark.createDataFrame(comparison).write.mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(f"{ns}.gold_feature_comparison")

    # 2. Walk-forward fits for every scored season and the live season, all five sets.
    scored = sorted(int(s) for s in roles.loc[roles["role"] != "train_only", "season"])
    fit_rows, pred_frames, live = [], [], None
    for season in [*scored, LIVE_SEASON]:
        train = training_seasons(roles, season)
        for name, names in FEATURE_SETS.items():
            weights, n_train = fit(features, train, names)
            fit_rows += [
                {
                    "feature_set": name,
                    "test_season": season,
                    "feature": f,
                    "weight": float(w),
                    "n_train_games": n_train,
                    "train_seasons": " ".join(str(s) for s in train),
                }
                for f, w in zip(names, weights, strict=True)
            ]
            if name != FEATURE_SET:
                continue
            if season == LIVE_SEASON:
                live = (names, weights, train, n_train)
                continue
            rows = features[features["season"] == season]
            pred_frames.append(
                pd.DataFrame(
                    {
                        "predictor": PREDICTOR,
                        "game_id": rows["game_id"],
                        "pred_margin": predict(rows, names, weights),
                        "feature_set": name,
                        "n_train_games": n_train,
                        "train_seasons": " ".join(str(s) for s in train),
                    }
                )
            )
    fits = pd.DataFrame(fit_rows)
    spark.createDataFrame(fits).write.mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(f"{ns}.gold_model_fits")
    preds = pd.concat(pred_frames, ignore_index=True)
    spark.createDataFrame(preds).write.mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(f"{ns}.gold_model_predictions")

    # 3. v1 as frozen for the live season -> Unity Catalog, alias `champion`.
    names, weights, train, n_train = live
    mlflow.set_registry_uri("databricks-uc")
    model_name = f"{ns}.margin_model"
    example = features[features["season"] == LIVE_SEASON][names].dropna().head(5)
    with mlflow.start_run(run_name=f"v1 frozen for {LIVE_SEASON}") as run:
        mlflow.log_params(
            {
                "feature_set": FEATURE_SET,
                "inputs": ",".join(names),
                "train_seasons": " ".join(str(s) for s in train),
                "n_train_games": n_train,
            }
        )
        mlflow.log_metrics({f"weight_{f}": float(w) for f, w in zip(names, weights, strict=True)})
        for season in scored:
            for r in fits[
                (fits["feature_set"] == FEATURE_SET) & (fits["test_season"] == season)
            ].itertuples():
                mlflow.log_metric(f"walk_forward_{r.feature}", r.weight, step=season)
        model = MarginModel(names, [float(w) for w in weights])
        info = mlflow.pyfunc.log_model(
            name="margin_model",
            python_model=model,
            input_example=example,
            signature=infer_signature(example, model.predict(None, example)),
            registered_model_name=model_name,
        )
    client = mlflow.MlflowClient()
    version = info.registered_model_version
    client.set_registered_model_alias(model_name, "champion", version)
    print(f"registered {model_name} v{version} as champion (run {run.info.run_id})")
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
