-- Metric 4: ATS at -110 (PREREGISTRATION.md, "Scorecard metrics").
-- Record and win % of the predictor's side against the line. Pushes are counted
-- separately and left out of the win %. Reported, not trusted.
--
-- In backtests the line is the closing line (DECISIONS.md, 2026-11-11). Break-even at
-- -110 is 52.4%.
--
-- Reads: scored (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(*) FILTER (WHERE ats_result = 'win') AS wins,
    count(*) FILTER (WHERE ats_result = 'loss') AS losses,
    count(*) FILTER (WHERE ats_result = 'push') AS pushes,
    count(*) FILTER (WHERE ats_result = 'win')
        / nullif(count(*) FILTER (WHERE ats_result IN ('win', 'loss')), 0) AS win_pct,
    count(*) FILTER (WHERE close_margin IS NULL) AS n_left_out_no_close,
    count(*) FILTER (WHERE close_margin IS NOT NULL AND side_close = 0) AS n_left_out_no_side
FROM scored
GROUP BY predictor, scope
ORDER BY predictor, scope;
