-- Metric 2: distance to the close (PREREGISTRATION.md, "Scorecard metrics").
-- Mean absolute difference between the predicted margin and the closing line's implied
-- margin. The primary accuracy measure, and the one G1's "matches or beats Torvik" uses
-- (the 'pooled' row). Games with no closing line are left out and counted.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(close_margin) AS n_games,
    avg(abs(pred_margin - close_margin)) AS mean_abs_distance_to_close,
    count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close
FROM scored
GROUP BY predictor, scope
ORDER BY predictor, scope;
