-- Metric 3: error vs. results (PREREGISTRATION.md, "Scorecard metrics").
-- Mean absolute error of the predicted margin against the final margin, next to the
-- closing line's own error on the same games. Reported, but results are noisy enough
-- that small differences mean little.
--
-- The side-by-side columns use only games that have a closing line, so both errors are
-- over the same games. mae_prediction_all_games covers every predicted game.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(close_margin) AS n_games,
    avg(abs(pred_margin - home_margin)) FILTER (WHERE close_margin IS NOT NULL)
        AS mae_prediction,
    avg(abs(close_margin - home_margin)) AS mae_closing_line,
    count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close,
    count(*) AS n_all_games,
    avg(abs(pred_margin - home_margin)) AS mae_prediction_all_games
FROM scored
GROUP BY predictor, scope
ORDER BY predictor, scope;
