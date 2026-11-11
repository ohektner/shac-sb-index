-- Metric 5, part 2: the reliability table (PREREGISTRATION.md, "Scorecard metrics").
-- Games grouped by predicted cover probability, with the predicted and the actual cover
-- rate and the count in each group. A well-calibrated predictor has the two rates close.
-- Pushes are left out.
--
-- Groups are fixed by the pre-registration: 50-52%, 52-54%, 54-56%, 56-58%, 58-60%, 60%+.
-- Each includes its lower edge. All six are always listed, with 0 games where empty.
-- A 'below 50%' group appears only if something falls under 50%, which shouldn't happen
-- (cover_prob is for the predictor's own side) and must not be hidden.
--
-- A predictor with no cover_prob yet (the baseline, until task 1.9) returns no rows.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

WITH grouped AS (
    SELECT
        *,
        CASE
            WHEN cover_prob < 0.50 THEN 0
            WHEN cover_prob < 0.52 THEN 1
            WHEN cover_prob < 0.54 THEN 2
            WHEN cover_prob < 0.56 THEN 3
            WHEN cover_prob < 0.58 THEN 4
            WHEN cover_prob < 0.60 THEN 5
            ELSE 6
        END AS group_order
    FROM scored
    WHERE cover_prob IS NOT NULL AND ats_result IN ('win', 'loss')
),
group_names AS (
    SELECT * FROM (
        VALUES
            (0, 'below 50%'), (1, '50-52%'), (2, '52-54%'), (3, '54-56%'),
            (4, '56-58%'), (5, '58-60%'), (6, '60%+')
    ) AS t(group_order, cover_prob_group)
),
summary AS (
    SELECT
        predictor,
        scope,
        group_order,
        count(*) AS n_games,
        avg(cover_prob) AS predicted_cover_rate,
        avg(CASE WHEN ats_result = 'win' THEN 1.0 ELSE 0.0 END) AS actual_cover_rate
    FROM grouped
    GROUP BY predictor, scope, group_order
),
wanted AS (
    -- Every predictor x scope that has any calibrated game, times the fixed groups.
    SELECT k.predictor, k.scope, n.group_order, n.cover_prob_group
    FROM (SELECT DISTINCT predictor, scope FROM grouped) AS k
    CROSS JOIN group_names AS n
)
SELECT
    w.predictor,
    w.scope,
    w.group_order,
    w.cover_prob_group,
    coalesce(s.n_games, 0) AS n_games,
    s.predicted_cover_rate,
    s.actual_cover_rate
FROM wanted AS w
LEFT JOIN summary AS s USING (predictor, scope, group_order)
WHERE w.group_order > 0 OR s.n_games IS NOT NULL
ORDER BY w.predictor, w.scope, w.group_order;
