-- model_features: the margin model's inputs, one row per game in game_history (DuckDB).
-- Task 1.8. Run by shac.model.margin.build_features.
--
-- Reads game_history only, and from it only KEY and FEATURE columns, plus the `status`
-- of EARLIER games (to skip cancelled and postponed games when counting rest days).
-- It never reads a score or a line. tests/test_margin_model.py checks that by blanking
-- every score and line and confirming no feature moves.
--
-- Every feature flips sign when the two teams swap places (or is zero at a neutral
-- site), so the model treats "home" and "away" the same way on a neutral floor.
--
--   rating_margin  the baseline's rating part: (EM_h - EM_a) * T / 100
--                  (docs/PREREGISTRATION.md, "The Torvik baseline")
--   home           1 at the home team's floor, 0 at a neutral site
--   off_margin     the offense half of rating_margin: (adj_o_h - adj_o_a) * T / 100
--   def_margin     the defense half: (adj_d_a - adj_d_h) * T / 100
--                  (off_margin + def_margin = rating_margin)
--   rm_nov         rating_margin if the ratings file is dated before December 1, else 0
--   rm_dec         rating_margin if the ratings file is dated in December, else 0
--   home_conf      1 for a conference game on the home team's floor, else 0
--   rest_diff      home team's days since its last game minus the away team's, each
--                  capped at 7 (a season opener counts as 7)

WITH team_games AS (
    -- One row per team per game, to find each team's previous game that season.
    -- Cancelled and postponed games were never played, so they don't count as rest.
    SELECT game_id, season, tip_at, home_team_id AS team_id
    FROM game_history
    WHERE status IS NULL OR status NOT IN ('cancelled', 'postponed')
    UNION ALL
    SELECT game_id, season, tip_at, away_team_id AS team_id
    FROM game_history
    WHERE status IS NULL OR status NOT IN ('cancelled', 'postponed')
),
rest AS (
    SELECT
        game_id,
        team_id,
        least(
            coalesce(
                date_diff(
                    'minute',
                    lag(tip_at) OVER (PARTITION BY season, team_id ORDER BY tip_at, game_id),
                    tip_at
                ) / 1440.0,
                7.0
            ),
            7.0
        ) AS rest_days
    FROM team_games
),
rated AS (
    SELECT
        g.game_id,
        g.season,
        g.neutral_site,
        g.conference_game,
        g.rating_date,
        g.home_adj_tempo * g.away_adj_tempo / g.file_mean_tempo AS possessions,
        g.home_adj_o - g.away_adj_o AS off_gap,
        g.away_adj_d - g.home_adj_d AS def_gap,
        hr.rest_days AS home_rest_days,
        ar.rest_days AS away_rest_days
    FROM game_history AS g
    LEFT JOIN rest AS hr ON hr.game_id = g.game_id AND hr.team_id = g.home_team_id
    LEFT JOIN rest AS ar ON ar.game_id = g.game_id AND ar.team_id = g.away_team_id
),
parts AS (
    SELECT
        *,
        (off_gap + def_gap) * possessions / 100 AS rating_margin,
        off_gap * possessions / 100 AS off_margin,
        def_gap * possessions / 100 AS def_margin
    FROM rated
)
SELECT
    game_id,
    season,
    rating_margin,
    -- Unknown neutral_site stays empty, so the game gets no prediction (as in the baseline).
    CASE WHEN neutral_site THEN 0.0 WHEN NOT neutral_site THEN 1.0 END AS home,
    off_margin,
    def_margin,
    CASE WHEN month(CAST(rating_date AS DATE)) IN (10, 11) THEN rating_margin ELSE 0.0 END AS rm_nov,
    CASE WHEN month(CAST(rating_date AS DATE)) = 12 THEN rating_margin ELSE 0.0 END AS rm_dec,
    CASE
        WHEN neutral_site THEN 0.0
        WHEN NOT neutral_site THEN CAST(coalesce(conference_game, FALSE) AS DOUBLE)
    END AS home_conf,
    coalesce(home_rest_days, 7.0) - coalesce(away_rest_days, 7.0) AS rest_diff
FROM parts
ORDER BY game_id;
