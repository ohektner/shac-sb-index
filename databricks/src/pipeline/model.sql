-- Model inputs and the Torvik baseline, mirroring sql/model/features.sql,
-- sql/scorecard/seasons.sql and sql/scorecard/baseline_predictions.sql (DuckDB).
-- Training and the margin model's predictions are a job task (src/train.py), not here.

-- Season roles, fixed in docs/PREREGISTRATION.md before any backtest result was seen.
CREATE OR REFRESH MATERIALIZED VIEW ref_season_roles
COMMENT "Season roles: train_only, excluded (2020-21), held_out."
AS SELECT * FROM VALUES
  (2019, 'train_only'),
  (2020, 'train_only'),
  (2021, 'excluded'),
  (2022, 'train_only'),
  (2023, 'held_out'),
  (2024, 'held_out'),
  (2025, 'held_out'),
  (2026, 'held_out')
AS t(season, role);

-- The margin model's candidate inputs, one row per game. Reads key and feature columns
-- only (plus earlier games' status, for rest days); never a score or a line.
--   rating_margin  (EM_h - EM_a) * T / 100
--   home           1 at the home floor, 0 at a neutral site
--   off_margin / def_margin   the two halves of rating_margin
--   rm_nov / rm_dec           rating_margin when the ratings file is from Oct-Nov / Dec
--   home_conf      1 for a conference game at the home floor
--   rest_diff      home rest days minus away, each capped at 7
CREATE OR REFRESH MATERIALIZED VIEW gold_model_features
COMMENT "Margin model inputs per game (point-in-time; no scores or lines)."
AS WITH team_games AS (
  SELECT game_id, season, tip_at, home_team_id AS team_id,
         coalesce(status IN ('cancelled', 'postponed'), FALSE) AS not_played
  FROM gold_game_history
  UNION ALL
  SELECT game_id, season, tip_at, away_team_id AS team_id,
         coalesce(status IN ('cancelled', 'postponed'), FALSE) AS not_played
  FROM gold_game_history
),
last_game AS (
  SELECT game_id, team_id, tip_at,
         max(CASE WHEN NOT not_played THEN tip_at END) OVER (
           PARTITION BY season, team_id
           ORDER BY tip_at
           RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL '1' MINUTE PRECEDING
         ) AS prev_tip
  FROM team_games
),
rest AS (
  -- Whole minutes between the two tips, counted as minute boundaries crossed (DuckDB's
  -- date_diff), then days, capped at 7; a season opener counts as 7.
  SELECT game_id, team_id,
         least(
           coalesce(
             (unix_seconds(date_trunc('MINUTE', tip_at))
               - unix_seconds(date_trunc('MINUTE', prev_tip))) / 60 / 1440.0,
             7.0),
           7.0) AS rest_days
  FROM last_game
),
rated AS (
  SELECT
    g.game_id, g.season, g.neutral_site, g.conference_game, g.rating_date,
    g.home_adj_tempo * g.away_adj_tempo / g.file_mean_tempo AS possessions,
    g.home_adj_o - g.away_adj_o AS off_gap,
    g.away_adj_d - g.home_adj_d AS def_gap,
    hr.rest_days AS home_rest_days,
    ar.rest_days AS away_rest_days
  FROM gold_game_history g
  LEFT JOIN rest hr ON hr.game_id = g.game_id AND hr.team_id = g.home_team_id
  LEFT JOIN rest ar ON ar.game_id = g.game_id AND ar.team_id = g.away_team_id
),
parts AS (
  SELECT *,
         (off_gap + def_gap) * possessions / 100 AS rating_margin,
         off_gap * possessions / 100 AS off_margin,
         def_gap * possessions / 100 AS def_margin
  FROM rated
)
SELECT
  game_id,
  season,
  rating_margin,
  CASE WHEN neutral_site THEN 0.0 WHEN NOT neutral_site THEN 1.0 END AS home,
  off_margin,
  def_margin,
  CASE WHEN month(rating_date) IN (10, 11) THEN rating_margin ELSE 0.0 END AS rm_nov,
  CASE WHEN month(rating_date) = 12 THEN rating_margin ELSE 0.0 END AS rm_dec,
  CASE WHEN neutral_site THEN 0.0
       WHEN NOT neutral_site THEN CAST(coalesce(conference_game, FALSE) AS DOUBLE) END
    AS home_conf,
  coalesce(home_rest_days, 7.0) - coalesce(away_rest_days, 7.0) AS rest_diff
FROM parts;

-- The Torvik baseline: rating_margin plus a home-court constant fit on earlier eligible
-- seasons only (never 2020-21), for every scored season.
CREATE OR REFRESH MATERIALIZED VIEW gold_baseline_predictions
COMMENT "Torvik baseline predicted home margin per game in scored seasons."
AS WITH rated AS (
  SELECT game_id, season, neutral_site, home_margin,
         ((home_adj_o - home_adj_d) - (away_adj_o - away_adj_d))
           * (home_adj_tempo * away_adj_tempo / file_mean_tempo) / 100 AS rating_margin
  FROM gold_game_history
),
splits AS (
  SELECT test.season AS test_season, train.season AS train_season
  FROM ref_season_roles test
  JOIN ref_season_roles train ON train.season < test.season AND train.role <> 'excluded'
  WHERE test.role IN ('held_out', 'excluded')
),
home_court AS (
  SELECT
    s.test_season,
    avg(r.home_margin - r.rating_margin) AS home_court,
    count(*) AS n_train_games,
    array_join(array_sort(collect_set(CAST(s.train_season AS STRING))), ' ') AS train_seasons
  FROM splits s
  JOIN rated r ON r.season = s.train_season
  WHERE NOT r.neutral_site
    AND r.home_margin IS NOT NULL
    AND r.rating_margin IS NOT NULL
  GROUP BY s.test_season
)
SELECT
  'torvik_baseline' AS predictor,
  r.game_id,
  CASE WHEN r.neutral_site THEN r.rating_margin
       WHEN NOT r.neutral_site THEN r.rating_margin + h.home_court END AS pred_margin,
  r.rating_margin,
  h.home_court,
  h.n_train_games,
  h.train_seasons
FROM rated r
JOIN home_court h ON h.test_season = r.season;
