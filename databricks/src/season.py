"""The live season's record against CBBD's close (docs/DATABRICKS.md, D3).

Graded like the backtest scorecard: the model's side is the one its number favors
against the close; a win covers that close. Two scopes:

- live: each game's last prediction logged BEFORE tip (gold_prediction_log). Only games
  priced by the nightly job before they started; the honest live record.
- season: tonight's point-in-time rebuild of every season game (gold_live_predictions),
  including games played before the nightly job existed. Same model, same no-hindsight
  inputs.

Writes gold_season_scorecard (one row per scope) and gold_season_weekly (cumulative, by
week of tip, US Eastern). Arguments: catalog, schema.
"""

import sys

from pyspark.sql import SparkSession

GRADED = """
WITH live AS (
  SELECT game_id, pred_margin FROM (
    SELECT l.game_id, l.pred_margin,
           row_number() OVER (PARTITION BY l.game_id ORDER BY l.scored_at DESC) AS rn
    FROM {ns}.gold_prediction_log l
    WHERE l.scored_at < l.tip_at
  ) WHERE rn = 1
),
preds AS (
  SELECT 'live' AS scope, game_id, pred_margin FROM live
  UNION ALL
  SELECT 'season' AS scope, game_id, pred_margin FROM {ns}.gold_live_predictions
),
joined AS (
  SELECT p.scope, p.game_id, p.pred_margin, g.home_margin,
         g.cbbd_close_home_spread AS close_home_spread,
         -g.cbbd_close_home_spread AS close_margin,
         to_date(from_utc_timestamp(g.tip_at, 'America/New_York')) AS tip_date
  FROM preds p
  JOIN {ns}.gold_game_history g USING (game_id)
  WHERE g.status = 'final' AND g.home_margin IS NOT NULL
)
SELECT *,
  CASE WHEN close_margin IS NULL OR sign(pred_margin - close_margin) = 0 THEN NULL
       WHEN sign(pred_margin - close_margin) * (home_margin + close_home_spread) > 0 THEN 'win'
       WHEN sign(pred_margin - close_margin) * (home_margin + close_home_spread) < 0 THEN 'loss'
       ELSE 'push' END AS ats_result
FROM joined
"""

SCORECARD = """
SELECT scope,
  count(close_margin) AS n_games,
  max(tip_date) AS updated_through,
  avg(abs(pred_margin - close_margin)) AS mean_abs_distance_to_close,
  count(*) FILTER (WHERE ats_result = 'win') AS wins,
  count(*) FILTER (WHERE ats_result = 'loss') AS losses,
  count(*) FILTER (WHERE ats_result = 'push') AS pushes,
  count(*) FILTER (WHERE ats_result = 'win')
    / nullif(count(*) FILTER (WHERE ats_result IN ('win', 'loss')), 0) AS win_pct,
  avg(abs(pred_margin - home_margin)) FILTER (WHERE close_margin IS NOT NULL) AS mae_model,
  avg(abs(close_margin - home_margin)) AS mae_close,
  count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close
FROM graded GROUP BY scope
"""

WEEKLY = """
WITH weeks AS (
  SELECT scope, date_trunc('WEEK', tip_date) AS week_start,
         count(close_margin) AS n_games,
         sum(abs(pred_margin - close_margin)) AS sum_distance,
         count(*) FILTER (WHERE ats_result = 'win') AS wins,
         count(*) FILTER (WHERE ats_result = 'loss') AS losses
  FROM graded GROUP BY scope, date_trunc('WEEK', tip_date)
)
SELECT scope, CAST(week_start AS DATE) AS week_start, n_games, wins, losses,
  sum(n_games) OVER w AS cum_games,
  sum(wins) OVER w AS cum_wins,
  sum(losses) OVER w AS cum_losses,
  sum(wins) OVER w / nullif(sum(wins) OVER w + sum(losses) OVER w, 0) AS cum_win_pct,
  sum(sum_distance) OVER w / nullif(sum(n_games) OVER w, 0) AS cum_mean_abs_distance
FROM weeks
WINDOW w AS (PARTITION BY scope ORDER BY week_start)
"""


def main(catalog: str, schema: str) -> None:
    spark = SparkSession.builder.getOrCreate()
    ns = f"{catalog}.{schema}"
    spark.sql(GRADED.format(ns=ns)).createOrReplaceTempView("graded")
    for table, sql in (("gold_season_scorecard", SCORECARD), ("gold_season_weekly", WEEKLY)):
        df = spark.sql(sql)
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ns}.{table}")
    for row in spark.table(f"{ns}.gold_season_scorecard").orderBy("scope").collect():
        print(row.asDict())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
