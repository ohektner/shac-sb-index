"""Freshness alarm (docs/DATABRICKS.md, D3): fail the nightly job when data stops arriving.

Fails if the newest CBBD fetch or the newest daily Torvik pull is more than 48 hours
old. A failed job emails Owen (job email_notifications). Pause the job when the season
ends in April, or this will fail every night. Arguments: catalog, schema.
"""

import sys

from pyspark.sql import SparkSession

MAX_AGE_HOURS = 48


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    row = spark.sql(
        f"""
        SELECT
          (SELECT max(fetched_at) FROM {ns}.silver_games) AS cbbd_latest,
          (SELECT max(as_of) FROM {ns}.silver_ratings_daily WHERE source = 'torvik_daily')
            AS torvik_latest,
          current_timestamp() AS now
        """
    ).first()
    stale = []
    for label, latest in (("CBBD games", row.cbbd_latest), ("Torvik daily", row.torvik_latest)):
        age = None if latest is None else (row.now - latest).total_seconds() / 3600
        print(f"{label}: newest {latest} UTC ({'none' if age is None else f'{age:.1f} h old'})")
        if age is None or age > MAX_AGE_HOURS:
            stale.append(label)
    if stale:
        raise SystemExit(
            f"STALE: no new {' or '.join(stale)} data in {MAX_AGE_HOURS} hours. Check the "
            "cbbd-ingest workflow, the databricks-sync workflow and the PC Torvik task."
        )
    print("fresh")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
