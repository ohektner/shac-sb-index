-- The Torvik baseline: one predicted home margin per game (DuckDB). Task 1.7.
-- The formula is fixed in docs/PREREGISTRATION.md, "The Torvik baseline". Nothing here is tuned.
--
--     baseline_margin = (EM_h - EM_a) * T / 100 + H
--
--   EM  a team's adjusted offense minus adjusted defense (points per 100 possessions)
--   T   expected possessions: tempo_h * tempo_a / the mean tempo of every team in the file
--   H   home-court advantage: 0 at a neutral site, otherwise one constant per test season
--
-- Reads: game_history (feature columns for the rating part; home_margin of EARLIER seasons
-- only, to fit H) and season_roles (sql/scorecard/seasons.sql).
--
-- NO LEAKAGE: H for test season S is fit only on seasons before S (the `splits` join), and
-- never on 2020-21. A game's own result never reaches its own prediction.
--
-- A game with no ratings file, a team missing from its file, or an unknown neutral_site
-- gets an empty pred_margin; the scorecard leaves it out and baseline_fit.sql counts it.

WITH rated AS (
    -- The part of the formula that comes from the ratings file alone.
    SELECT
        game_id,
        season,
        neutral_site,
        home_margin,
        ((home_adj_o - home_adj_d) - (away_adj_o - away_adj_d))
            * (home_adj_tempo * away_adj_tempo / file_mean_tempo) / 100 AS rating_margin
    FROM game_history
),
splits AS (
    -- For each scored season, the seasons it may learn from: earlier, and never 2020-21.
    SELECT test.season AS test_season, train.season AS train_season
    FROM season_roles AS test
    JOIN season_roles AS train
      ON train.season < test.season AND train.role <> 'excluded'
    WHERE test.role IN ('held_out', 'excluded')
),
home_court AS (
    -- H: the value that makes the average error zero on non-neutral training games.
    -- That is the average of (actual margin - rating part), pooled over those games.
    SELECT
        s.test_season,
        avg(r.home_margin - r.rating_margin) AS home_court,
        count(*) AS n_train_games,
        array_to_string(list_sort(list(DISTINCT s.train_season)), ' ') AS train_seasons
    FROM splits AS s
    JOIN rated AS r ON r.season = s.train_season
    WHERE NOT r.neutral_site
      AND r.home_margin IS NOT NULL
      AND r.rating_margin IS NOT NULL
    GROUP BY s.test_season
)
SELECT
    'torvik_baseline' AS predictor,
    r.game_id,
    -- If neutral_site is unknown the game gets no prediction (and is not in the fit above).
    CASE
        WHEN r.neutral_site THEN r.rating_margin
        WHEN NOT r.neutral_site THEN r.rating_margin + h.home_court
    END AS pred_margin,
    -- The baseline's cover probability needs the calibration step (task 1.9).
    CAST(NULL AS DOUBLE) AS cover_prob,
    r.rating_margin,
    h.home_court,
    h.n_train_games,
    h.train_seasons
FROM rated AS r
JOIN home_court AS h ON h.test_season = r.season
ORDER BY r.game_id;
