-- Coverage: how many games each predictor is scored on, and how many were dropped so
-- that every predictor is scored on the same games (DuckDB). Task 1.7.
--
-- n_scored_games is the same for every predictor in a scope, by construction.
--
-- Reads: graded_all (sql/scorecard/scored_games.sql).

SELECT
    predictor,
    scope,
    count(*) AS n_final_games_predicted,
    count(*) FILTER (WHERE shared) AS n_scored_games,
    count(*) FILTER (WHERE NOT shared) AS n_left_out_another_predictor_has_none,
    count(*) FILTER (WHERE shared AND close_margin IS NOT NULL) AS n_with_close,
    count(*) FILTER (WHERE shared AND close_margin IS NOT NULL AND open_margin IS NOT NULL)
        AS n_with_open_and_close,
    count(*) FILTER (WHERE shared AND cover_prob IS NOT NULL) AS n_with_cover_prob
FROM graded_all
GROUP BY predictor, scope
ORDER BY predictor, scope;
