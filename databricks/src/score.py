"""Score the live season with the champion model (docs/DATABRICKS.md, D3).

Loads `<catalog>.<schema>.margin_model@champion` (v1 as frozen for 2027) and prices
every live-season game from gold_model_features (point-in-time inputs). Writes:

- gold_live_predictions: every live-season game's prediction as rebuilt tonight.
- gold_prediction_log: append-only. Each run adds a row for every game that has NOT
  tipped yet, unless the same prediction from the same model version is already there.
  A game's live call is its last row logged before tip.

Arguments: catalog, schema.
"""

import sys

import mlflow
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

LIVE_SEASON = 2027


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    mlflow.set_registry_uri("databricks-uc")
    name = f"{ns}.margin_model"
    version = mlflow.MlflowClient().get_model_version_by_alias(name, "champion").version
    model = mlflow.pyfunc.load_model(f"models:/{name}@champion")
    inputs = model.metadata.get_input_schema().input_names()

    games = spark.sql(
        f"""
        SELECT f.*, g.tip_at, g.home_team_id, g.away_team_id, g.ratings_as_of
        FROM {ns}.gold_model_features f
        JOIN {ns}.gold_game_history g USING (game_id)
        WHERE f.season = {LIVE_SEASON}
        """
    ).toPandas()
    usable = games[inputs].notna().all(axis=1)
    games = games[usable].copy()
    games["pred_margin"] = model.predict(games[inputs].astype("float64")).to_numpy()
    games["model_version"] = int(version)

    out = spark.createDataFrame(
        games[
            [
                "game_id",
                "season",
                "tip_at",
                "home_team_id",
                "away_team_id",
                "ratings_as_of",
                "pred_margin",
                "model_version",
            ]
        ]
    ).withColumn("scored_at", F.current_timestamp())
    out.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        f"{ns}.gold_live_predictions"
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {ns}.gold_prediction_log (
          game_id BIGINT, tip_at TIMESTAMP, pred_margin DOUBLE, model_version INT,
          ratings_as_of TIMESTAMP, scored_at TIMESTAMP
        ) COMMENT 'Append-only pre-tip predictions. A game''s call: its last row before tip.'
        """
    )
    out.where(F.col("tip_at") > F.col("scored_at")).select(
        "game_id", "tip_at", "pred_margin", "model_version", "ratings_as_of", "scored_at"
    ).createOrReplaceTempView("new_rows")
    spark.sql(
        f"""
        MERGE INTO {ns}.gold_prediction_log AS log
        USING new_rows AS n
        ON log.game_id = n.game_id AND log.model_version = n.model_version
           AND log.pred_margin = n.pred_margin
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    n_upcoming = spark.table("new_rows").count()
    print(
        f"champion v{version}: priced {len(games)} live-season games, {n_upcoming} not yet tipped"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
