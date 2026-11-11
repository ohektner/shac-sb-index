-- The baseline's fit and coverage, one row per scored season (DuckDB). Task 1.7.
-- PREREGISTRATION.md: games with a team missing from the ratings file are left out,
-- "and the count of such games is reported". These are those counts, next to H.
--
-- A final game can be left out for three reasons, counted separately:
--   no ratings file     the game tipped before the season's first file was known
--   team not in file    the file exists but one team (or both) isn't in it
--   neutral unknown     ratings are there but neutral_site is empty, so H can't be applied
--
-- Reads: baseline (the output of baseline_predictions.sql), game_history.

SELECT
    g.season,
    any_value(b.train_seasons) AS train_seasons,
    any_value(b.n_train_games) AS n_train_games,
    any_value(b.home_court) AS home_court,
    count(*) AS n_final_games,
    count(b.pred_margin) AS n_predicted,
    count(*) FILTER (WHERE g.file_mean_tempo IS NULL) AS n_left_out_no_ratings_file,
    count(*) FILTER (WHERE g.file_mean_tempo IS NOT NULL AND b.rating_margin IS NULL)
        AS n_left_out_team_not_in_file,
    count(*) FILTER (WHERE b.rating_margin IS NOT NULL AND b.pred_margin IS NULL)
        AS n_left_out_neutral_unknown
FROM baseline AS b
JOIN game_history AS g USING (game_id)
WHERE g.home_margin IS NOT NULL
GROUP BY g.season
ORDER BY g.season;
