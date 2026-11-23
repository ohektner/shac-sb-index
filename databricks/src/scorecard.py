"""The backtest scorecard in Spark SQL (docs/DATABRICKS.md, D2).

Mirrors sql/scorecard/scored_games.sql and the coverage and metric 2, 3 and 4 queries:
the Torvik baseline and the margin model, graded against CBBD's close on the games both
can predict, per scored season and pooled over the held-out seasons. Calibration
(cover_prob, metrics 5-6) stays in Python and is not ported. Writes one table per metric,
gold_scorecard_<name>. Arguments: catalog, schema.
"""

import sys

from pyspark.sql import SparkSession

GRADED_ALL = """
WITH predictions AS (
  SELECT predictor, game_id, pred_margin, CAST(NULL AS DOUBLE) AS cover_prob
  FROM {ns}.gold_baseline_predictions
  UNION ALL
  SELECT predictor, game_id, pred_margin, CAST(NULL AS DOUBLE) AS cover_prob
  FROM {ns}.gold_model_predictions
),
shared_games AS (
  SELECT game_id
  FROM predictions
  WHERE pred_margin IS NOT NULL
  GROUP BY game_id
  HAVING count(DISTINCT predictor) = (SELECT count(DISTINCT predictor) FROM predictions)
),
joined AS (
  SELECT
    p.predictor, p.game_id, g.season, r.role, p.pred_margin, p.cover_prob, g.home_margin,
    g.cbbd_close_home_spread AS close_home_spread,
    -g.cbbd_close_home_spread AS close_margin,
    -g.cbbd_open_home_spread AS open_margin,
    s.game_id IS NOT NULL AS shared
  FROM predictions p
  JOIN {ns}.gold_game_history g USING (game_id)
  JOIN {ns}.ref_season_roles r ON r.season = g.season AND r.role IN ('held_out', 'excluded')
  LEFT JOIN shared_games s USING (game_id)
  WHERE p.pred_margin IS NOT NULL AND g.home_margin IS NOT NULL
),
sided AS (
  SELECT *,
         CAST(sign(pred_margin - close_margin) AS INT) AS side_close,
         CAST(sign(pred_margin - open_margin) AS INT) AS side_open
  FROM joined
),
graded AS (
  SELECT *,
    CASE WHEN side_close IS NULL OR side_close = 0 THEN NULL
         WHEN side_close * (home_margin + close_home_spread) > 0 THEN 'win'
         WHEN side_close * (home_margin + close_home_spread) < 0 THEN 'loss'
         ELSE 'push' END AS ats_result
  FROM sided
)
SELECT *, CAST(season AS STRING) AS scope FROM graded
UNION ALL
SELECT *, 'pooled' AS scope FROM graded WHERE role = 'held_out'
"""

METRICS = {
    "coverage": """
SELECT predictor, scope,
  count(*) AS n_final_games_predicted,
  count(*) FILTER (WHERE shared) AS n_scored_games,
  count(*) FILTER (WHERE NOT shared) AS n_left_out_another_predictor_has_none,
  count(*) FILTER (WHERE shared AND close_margin IS NOT NULL) AS n_with_close,
  count(*) FILTER (WHERE shared AND close_margin IS NOT NULL AND open_margin IS NOT NULL)
    AS n_with_open_and_close
FROM graded_all GROUP BY predictor, scope""",
    "metric_2_distance_to_close": """
SELECT predictor, scope,
  count(close_margin) AS n_games,
  avg(abs(pred_margin - close_margin)) AS mean_abs_distance_to_close,
  count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close
FROM scored GROUP BY predictor, scope""",
    "metric_3_error_vs_results": """
SELECT predictor, scope,
  count(close_margin) AS n_games,
  avg(abs(pred_margin - home_margin)) FILTER (WHERE close_margin IS NOT NULL) AS mae_prediction,
  avg(abs(close_margin - home_margin)) AS mae_closing_line,
  count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close,
  count(*) AS n_all_games,
  avg(abs(pred_margin - home_margin)) AS mae_prediction_all_games
FROM scored GROUP BY predictor, scope""",
    "metric_4_ats": """
SELECT predictor, scope,
  count(*) FILTER (WHERE ats_result = 'win') AS wins,
  count(*) FILTER (WHERE ats_result = 'loss') AS losses,
  count(*) FILTER (WHERE ats_result = 'push') AS pushes,
  count(*) FILTER (WHERE ats_result = 'win')
    / nullif(count(*) FILTER (WHERE ats_result IN ('win', 'loss')), 0) AS win_pct,
  count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close,
  count(*) FILTER (WHERE close_margin IS NOT NULL AND side_close = 0) AS n_left_out_no_side
FROM scored GROUP BY predictor, scope""",
}


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    spark.sql(GRADED_ALL.format(ns=ns)).createOrReplaceTempView("graded_all")
    spark.sql("SELECT * FROM graded_all WHERE shared").createOrReplaceTempView("scored")
    for name, sql in METRICS.items():
        df = spark.sql(f"SELECT 'cbbd' AS close_source, * FROM ({sql})")
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
            f"{ns}.gold_scorecard_{name}"
        )
        if name in ("metric_2_distance_to_close", "metric_4_ats"):
            for row in df.where("scope = 'pooled'").orderBy("predictor").collect():
                print(name, row.asDict())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
