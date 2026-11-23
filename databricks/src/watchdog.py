"""Watchdog (docs/DATABRICKS.md, D3): catch silence, not just failures.

The nightly job only runs when new files arrive, so if uploads stop (GitHub down, PC
off), it never runs and never fails, and nothing would email anyone. This job runs on
Databricks' own clock every evening and fails (emailing the owner) when:

1. `shac nightly` has no successful run in the last MAX_RUN_AGE_HOURS, or
2. the newest CBBD fetch or daily Torvik pull is more than MAX_DATA_AGE_HOURS old.

Arguments: catalog, schema, nightly job id. Pause it with the nightly job in April.
"""

import sys
import time

from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession

MAX_RUN_AGE_HOURS = 30
MAX_DATA_AGE_HOURS = 48


def last_success_hours(job_id: int) -> float | None:
    w = WorkspaceClient()
    now_ms = time.time() * 1000
    for run in w.jobs.list_runs(job_id=job_id, completed_only=True, limit=25):
        state = run.state.result_state.value if run.state and run.state.result_state else ""
        if state == "SUCCESS" and run.end_time:
            return (now_ms - run.end_time) / 3_600_000
    return None


def main(catalog: str, schema: str, nightly_job_id: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    problems = []

    age = last_success_hours(int(nightly_job_id))
    print(f"shac nightly: last success {'never' if age is None else f'{age:.1f} h ago'}")
    if age is None or age > MAX_RUN_AGE_HOURS:
        problems.append(f"no successful nightly run in {MAX_RUN_AGE_HOURS} h")

    row = spark.sql(
        f"""
        SELECT
          (SELECT max(fetched_at) FROM {ns}.silver_games) AS cbbd_latest,
          (SELECT max(as_of) FROM {ns}.silver_ratings_daily WHERE source = 'torvik_daily')
            AS torvik_latest,
          current_timestamp() AS now
        """
    ).first()
    for label, latest in (("CBBD games", row.cbbd_latest), ("Torvik daily", row.torvik_latest)):
        hours = None if latest is None else (row.now - latest).total_seconds() / 3600
        print(f"{label}: newest {latest} UTC ({'none' if hours is None else f'{hours:.1f} h old'})")
        if hours is None or hours > MAX_DATA_AGE_HOURS:
            problems.append(f"no new {label} data in {MAX_DATA_AGE_HOURS} h")

    if problems:
        raise SystemExit(
            "WATCHDOG: "
            + "; ".join(problems)
            + ". Check: GitHub Actions (cbbd-ingest, databricks-sync), the PC Torvik task "
            "log (%LOCALAPPDATA%\\shac-sb-index\\torvik-task.log), and shac nightly's runs."
        )
    print("all good")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
