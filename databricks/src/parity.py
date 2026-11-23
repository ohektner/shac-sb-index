"""Parity checks: the Databricks tables equal the local Python/DuckDB output.

Run as a job task. Arguments: catalog, schema, suite (`d1` or `d2`).

- d1: the pipeline's tables vs the local `shac.tables.build` parquet.
- d2: model features, baseline and margin-model predictions, every walk-forward fit,
  the input comparison, and the scorecard metrics vs the local `shac.model.margin`
  and `shac.scorecard.run` output.

The local files are uploaded by hand to the landing volume (`parity/`) together with the
raw files they were built from. Each table is compared on its key, column by column:
doubles within a tolerance (sums run in a different order; CSV metrics are rounded to 4
decimals), everything else exactly, nulls included. Prints counts only and fails if any
table differs.
"""

import sys

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, FloatType

EXACT = 1e-9
CSV_4DP = 5.001e-5

# suite -> (Databricks table, local file, key columns, local columns out of scope, tolerance)
SUITES = {
    "d1": [
        ("silver_games", "games.parquet", ["game_id"], [], EXACT),
        ("silver_results", "results.parquet", ["game_id"], [], EXACT),
        ("silver_cbbd_lines", "cbbd_lines.parquet", ["game_id", "provider"], [], EXACT),
        (
            "silver_ratings_daily",
            "ratings_daily.parquet",
            ["source", "rating_date", "team_id", "as_of"],
            [],
            EXACT,
        ),
        (
            "gold_game_history",
            "game_history.parquet",
            ["game_id"],
            ["own_close_home_spread", "pinnacle_close_home_spread"],
            EXACT,
        ),
    ],
    "d2": [
        ("gold_model_features", "model_features.parquet", ["game_id"], ["home_margin"], EXACT),
        (
            "gold_baseline_predictions",
            "baseline_predictions.parquet",
            ["predictor", "game_id"],
            ["cover_prob"],
            EXACT,
        ),
        (
            "gold_model_predictions",
            "model_predictions.parquet",
            ["predictor", "game_id"],
            ["cover_prob"],
            EXACT,
        ),
        (
            "gold_model_fits",
            "walk_forward_weights.csv",
            ["feature_set", "test_season", "feature"],
            [],
            EXACT,
        ),
        ("gold_feature_comparison", "feature_comparison.csv", ["feature_set"], [], EXACT),
        (
            "gold_scorecard_coverage",
            "scorecard/coverage.csv",
            ["predictor", "scope"],
            ["n_with_cover_prob"],
            CSV_4DP,
        ),
        (
            "gold_scorecard_metric_2_distance_to_close",
            "scorecard/metric_2_distance_to_close.csv",
            ["predictor", "scope"],
            [],
            CSV_4DP,
        ),
        (
            "gold_scorecard_metric_3_error_vs_results",
            "scorecard/metric_3_error_vs_results.csv",
            ["predictor", "scope"],
            [],
            CSV_4DP,
        ),
        (
            "gold_scorecard_metric_4_ats",
            "scorecard/metric_4_ats.csv",
            ["predictor", "scope"],
            [],
            CSV_4DP,
        ),
    ],
}


def read_local(spark: SparkSession, path: str) -> DataFrame:
    if path.endswith(".csv"):
        df = spark.read.option("header", "true").option("inferSchema", "true").csv(path)
        # `scope` holds seasons and "pooled"; keep it text like the Databricks tables.
        return (
            df.withColumn("scope", F.col("scope").cast("string")) if "scope" in df.columns else df
        )
    return spark.read.parquet(path)


def compare(ours: DataFrame, local: DataFrame, keys: list[str], tol: float) -> dict:
    # Keys take the Databricks types, so CSV-inferred ints join to BIGINT and so on.
    local = local.select(
        *[F.col(k).cast(ours.schema[k].dataType).alias(k) for k in keys],
        *[c for c in local.columns if c not in keys],
    )
    shared = [c for c in local.columns if c in ours.columns and c not in keys]
    missing = [c for c in local.columns if c not in ours.columns]
    o = ours.select(*keys, *[F.col(c).alias(f"o__{c}") for c in shared])
    lo = local.select(*keys, *[F.col(c).alias(f"l__{c}") for c in shared])
    both = o.join(lo, on=keys, how="inner")
    o_keys = ours.select(*keys).withColumn("_o", F.lit(True))
    l_keys = local.select(*keys).withColumn("_l", F.lit(True))
    k = o_keys.join(l_keys, on=keys, how="full_outer")
    out = {
        "rows_databricks": ours.count(),
        "rows_local": local.count(),
        "missing_columns": missing,
        "only_databricks": k.filter(F.col("_l").isNull()).count(),
        "only_local": k.filter(F.col("_o").isNull()).count(),
    }
    checks = []
    for c in shared:
        a, b = F.col(f"o__{c}"), F.col(f"l__{c}")
        if isinstance(local.schema[c].dataType, (DoubleType, FloatType)) or isinstance(
            ours.schema[c].dataType, (DoubleType, FloatType)
        ):
            a, b = a.cast("double"), b.cast("double")
            same = (a.isNull() & b.isNull()) | (F.abs(a - b) <= tol)
        else:
            same = a.eqNullSafe(b.cast(ours.schema[c].dataType))
        checks.append(F.sum(F.when(F.coalesce(same, F.lit(False)), 0).otherwise(1)).alias(c))
    diffs = both.agg(*checks).first().asDict() if checks else {}
    out["column_diffs"] = {c: n for c, n in diffs.items() if n}
    return out


def main(catalog: str, schema: str, suite: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    root = f"/Volumes/{catalog}/{schema}/landing/parity"
    failed = []
    for table, file, keys, out_of_scope, tol in SUITES[suite]:
        ours = spark.table(f"{catalog}.{schema}.{table}")
        local = read_local(spark, f"{root}/{file}").drop(*out_of_scope)
        res = compare(ours, local, keys, tol)
        ok = (
            not res["missing_columns"]
            and res["only_databricks"] == 0
            and res["only_local"] == 0
            and not res["column_diffs"]
        )
        print(f"{'PASS' if ok else 'FAIL'} {table} vs {file}: {res}")
        if not ok:
            failed.append(table)
    if failed:
        raise SystemExit(f"parity failed: {failed}")
    print(f"parity {suite}: every table matches the local output")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "d1")
