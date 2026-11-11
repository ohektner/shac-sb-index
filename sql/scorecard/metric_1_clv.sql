-- Metric 1: CLV in points (PREREGISTRATION.md, "Scorecard metrics").
-- Mean with a 95% confidence interval (normal approximation: mean +/- 1.96 standard
-- errors) and the share of games with positive CLV. Backtests use open-to-close.
-- Games with no opening line are left out of CLV only, and counted here.
--
-- CBBD has no opening lines before 2024-25, so the 'pooled' row is in practice the
-- seasons that have them, not all four held-out seasons. n_left_out_no_open shows it.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(clv) AS n_games,
    avg(clv) AS mean_clv,
    avg(clv) - 1.96 * stddev_samp(clv) / sqrt(count(clv)) AS ci95_low,
    avg(clv) + 1.96 * stddev_samp(clv) / sqrt(count(clv)) AS ci95_high,
    avg(CASE WHEN clv > 0 THEN 1.0 ELSE 0.0 END) FILTER (WHERE clv IS NOT NULL)
        AS share_positive,
    count(*) FILTER (WHERE open_margin IS NULL) AS n_left_out_no_open,
    count(*) FILTER (WHERE open_margin IS NOT NULL AND close_margin IS NULL)
        AS n_left_out_no_close,
    count(*) FILTER (
        WHERE open_margin IS NOT NULL AND close_margin IS NOT NULL AND side_open = 0
    ) AS n_left_out_no_side
FROM scored
GROUP BY predictor, scope
ORDER BY predictor, scope;
