-- Metric 6: per-tier record (PREREGISTRATION.md, "Scorecard metrics").
-- For Pick and for Lean: number of calls, win %, a 95% Wilson confidence interval, the
-- gap to the 52.4% break-even and mean CLV. The interval is always shown, so a small
-- sample reads as small. Pushes are counted and left out of the win %.
--
-- Wilson interval for w wins in n decided calls, p = w / n, z = 1.96:
--     centre = (p + z^2 / 2n) / (1 + z^2 / n)
--     half   = z * sqrt(p (1 - p) / n + z^2 / 4n^2) / (1 + z^2 / n)
--
-- A call is a tier game with a closing line and a side. Tier games without one can't be
-- graded; n_left_out_no_close_or_side counts them.
--
-- mean_clv: in a backtest the call's side is the side against the CLOSE, while CLV is
-- measured for the side against the OPEN. When the line crosses the prediction the two
-- differ, and that CLV would belong to the other side (and is positive by construction).
-- So mean_clv covers only calls where both sides agree (n_clv_games);
-- n_clv_left_out_side_differs counts the rest that had an open.
--
-- A predictor with no cover_prob yet (the baseline, until task 1.9) returns no rows.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

WITH calls AS (
    SELECT
        predictor,
        scope,
        tier,
        count(*) FILTER (WHERE ats_result IS NOT NULL) AS n_calls,
        count(*) FILTER (WHERE ats_result = 'win') AS wins,
        count(*) FILTER (WHERE ats_result = 'loss') AS losses,
        count(*) FILTER (WHERE ats_result = 'push') AS pushes,
        CAST(count(*) FILTER (WHERE ats_result IN ('win', 'loss')) AS DOUBLE) AS n,
        count(*) FILTER (WHERE ats_result IS NULL) AS n_left_out_no_close_or_side,
        count(clv) FILTER (WHERE ats_result IS NOT NULL AND side_open = side_close)
            AS n_clv_games,
        avg(clv) FILTER (WHERE ats_result IS NOT NULL AND side_open = side_close) AS mean_clv,
        count(clv) FILTER (WHERE ats_result IS NOT NULL AND side_open <> side_close)
            AS n_clv_left_out_side_differs
    FROM scored
    WHERE tier IN ('pick', 'lean')
    GROUP BY predictor, scope, tier
),
rates AS (
    SELECT *, wins / nullif(n, 0) AS p, 1.96 AS z FROM calls
)
SELECT
    predictor,
    scope,
    tier,
    n_calls,
    wins,
    losses,
    pushes,
    p AS win_pct,
    (p + z * z / (2 * n) - z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)))
        / (1 + z * z / n) AS wilson95_low,
    (p + z * z / (2 * n) + z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)))
        / (1 + z * z / n) AS wilson95_high,
    p - 0.524 AS gap_to_break_even,
    n_clv_games,
    mean_clv,
    n_clv_left_out_side_differs,
    n_left_out_no_close_or_side
FROM rates
ORDER BY predictor, scope, CASE tier WHEN 'pick' THEN 0 ELSE 1 END;
