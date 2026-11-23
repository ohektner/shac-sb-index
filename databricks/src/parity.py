"""D1 exit check: the Databricks tables equal the local `shac.tables.build` output.

Run as a job task after the pipeline. Arguments: catalog, schema. Reads the local
build's parquet files from the landing volume (`parity/<table>.parquet`, uploaded by
hand with the raw files they were built from) and compares every shared column, row by
row on the table's key. Doubles may differ by 1e-9 (sums in a different order); every
other type must match exactly, nulls included. Prints counts only, then fails if any
table differs.
"""

import sys

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, FloatType

TOLERANCE = 1e-9

# (Databricks table, local parquet, key columns, local columns left out of scope)
TABLES = [
    ("silver_games", "games", ["game_id"], []),
    ("silver_results", "results", ["game_id"], []),
    ("silver_cbbd_lines", "cbbd_lines", ["game_id", "provider"], []),
    ("silver_ratings_daily", "ratings_daily", ["source", "rating_date", "team_id", "as_of"], []),
    (
        "gold_game_history",
        "game_history",
        ["game_id"],
        ["own_close_home_spread", "pinnacle_close_home_spread"],
    ),
]


def compare(ours: DataFrame, local: DataFrame, keys: list[str]) -> dict:
    shared = [c for c in local.columns if c in ours.columns and c not in keys]
    missing = [c for c in local.columns if c not in ours.columns]
    o = ours.select(*keys, *[F.col(c).alias(f"o__{c}") for c in shared])
    lo = local.select(*keys, *[F.col(c).alias(f"l__{c}") for c in shared])
    both = o.join(lo, on=keys, how="inner")
    out = {
        "rows_databricks": ours.count(),
        "rows_local": local.count(),
        "missing_columns": missing,
    }
    # Rows only on one side: keys present on one side only.
    o_keys = ours.select(*keys).withColumn("_o", F.lit(True))
    l_keys = local.select(*keys).withColumn("_l", F.lit(True))
    k = o_keys.join(l_keys, on=keys, how="full_outer")
    out["only_databricks"] = k.filter(F.col("_l").isNull()).count()
    out["only_local"] = k.filter(F.col("_o").isNull()).count()

    types = dict(local.dtypes)
    checks = []
    for c in shared:
        a, b = F.col(f"o__{c}"), F.col(f"l__{c}")
        if isinstance(local.schema[c].dataType, (DoubleType, FloatType)):
            same = (a.isNull() & b.isNull()) | (F.abs(a - b) <= TOLERANCE)
        else:
            same = a.eqNullSafe(b.cast(ours.schema[c].dataType))
        checks.append(F.sum(F.when(F.coalesce(same, F.lit(False)), 0).otherwise(1)).alias(c))
    diffs = both.agg(*checks).first().asDict() if checks else {}
    out["column_diffs"] = {c: n for c, n in diffs.items() if n}
    out["types"] = {c: types[c] for c in out["column_diffs"]}
    return out


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    root = f"/Volumes/{catalog}/{schema}/landing/parity"
    failed = []
    for table, parquet, keys, out_of_scope in TABLES:
        ours = spark.table(f"{catalog}.{schema}.{table}")
        local = spark.read.parquet(f"{root}/{parquet}.parquet").drop(*out_of_scope)
        res = compare(ours, local, keys)
        ok = (
            not res["missing_columns"]
            and res["only_databricks"] == 0
            and res["only_local"] == 0
            and not res["column_diffs"]
        )
        print(f"{'PASS' if ok else 'FAIL'} {table} vs {parquet}: {res}")
        if not ok:
            failed.append(table)
    if failed:
        raise SystemExit(f"parity failed: {failed}")
    print("parity: every table matches the local build")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
