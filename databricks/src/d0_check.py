"""D0 check: read the test file in the landing volume and log one row to a Delta table.

Runs on Databricks serverless as a job task. Arguments: catalog, schema.
"""

import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    path = f"/Volumes/{catalog}/{schema}/landing/d0/hello.csv"
    rows = spark.read.option("header", True).csv(path)
    n = rows.count()
    if n == 0:
        raise SystemExit(f"{path} has no rows")
    (
        rows.limit(1)
        .select(F.col("source"), F.col("uploaded_at"))
        .withColumn("rows_read", F.lit(n))
        .withColumn("checked_at", F.current_timestamp())
        .write.mode("append")
        .saveAsTable(f"{catalog}.{schema}.d0_check")
    )
    print(f"ok: read {n} row(s) from {path}, appended to {catalog}.{schema}.d0_check")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
