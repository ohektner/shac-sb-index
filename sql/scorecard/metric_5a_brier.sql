-- Metric 5, part 1: Brier score of cover_prob (PREREGISTRATION.md, "Scorecard metrics").
-- The average of (cover_prob - covered)^2, where covered is 1 for a win and 0 for a loss.
-- Lower is better; always saying 50% scores 0.25. Pushes are left out.
--
-- A predictor with no cover_prob yet (the baseline, until task 1.9) returns no rows.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(*) AS n_games,
    avg(power(cover_prob - CASE WHEN ats_result = 'win' THEN 1 ELSE 0 END, 2)) AS brier_score
FROM scored
WHERE cover_prob IS NOT NULL AND ats_result IN ('win', 'loss')
GROUP BY predictor, scope
ORDER BY predictor, scope;
