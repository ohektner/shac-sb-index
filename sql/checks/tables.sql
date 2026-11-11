-- Private-table checks (DuckDB). Each check returns ZERO rows when the tables are good.
-- Run with: uv run python -m shac.tables.check --data-dir <private repo>
--
-- The runner makes one view per parquet file in <private repo>/tables/:
--   games, results, player_box, cbbd_lines, odds_snapshots, odds_event_matches,
--   closing_lines, ratings_daily, game_history, rotation_players

-- check: a_games_duplicate_pk
SELECT game_id, count(*) AS n FROM games GROUP BY game_id HAVING count(*) > 1;

-- check: b_results_duplicate_pk
SELECT game_id, count(*) AS n FROM results GROUP BY game_id HAVING count(*) > 1;

-- check: c_player_box_duplicate_pk
SELECT game_id, team_id, athlete_id, count(*) AS n
FROM player_box
GROUP BY game_id, team_id, athlete_id
HAVING count(*) > 1;

-- check: d_cbbd_lines_duplicate_pk
SELECT game_id, provider, count(*) AS n
FROM cbbd_lines
GROUP BY game_id, provider
HAVING count(*) > 1;

-- check: e_odds_snapshots_duplicate_pk
SELECT snapshot_at, odds_event_id, book, count(*) AS n
FROM odds_snapshots
GROUP BY snapshot_at, odds_event_id, book
HAVING count(*) > 1;

-- check: f_odds_event_matches_duplicate_pk
SELECT odds_event_id, count(*) AS n
FROM odds_event_matches
GROUP BY odds_event_id
HAVING count(*) > 1;

-- check: g_closing_lines_duplicate_pk
SELECT game_id, source, count(*) AS n
FROM closing_lines
GROUP BY game_id, source
HAVING count(*) > 1;

-- check: h_final_game_without_result
SELECT g.game_id
FROM games g
LEFT JOIN results r USING (game_id)
WHERE g.status = 'final' AND r.game_id IS NULL;

-- check: i_result_margin_wrong
SELECT game_id
FROM results
WHERE home_margin IS DISTINCT FROM home_points - away_points;

-- check: j_player_box_game_not_in_games
SELECT DISTINCT p.game_id
FROM player_box p
LEFT JOIN games g USING (game_id)
WHERE g.game_id IS NULL;

-- check: k_closing_snapshot_at_or_after_tip
-- A close must come from a snapshot taken strictly before tip: before the feed's
-- commence time, and before CBBD's tip time when CBBD's time is not a placeholder.
SELECT c.game_id, c.source
FROM closing_lines c
LEFT JOIN games g USING (game_id)
WHERE c.close_snapshot_at IS NOT NULL
  AND (
      c.minutes_before_tip IS NULL
      OR c.minutes_before_tip < 0
      OR EXISTS (
          SELECT 1
          FROM odds_snapshots s
          WHERE s.game_id = c.game_id
            AND s.snapshot_at = c.close_snapshot_at
            AND s.snapshot_at >= s.commence_at
      )
      OR EXISTS (
          SELECT 1
          FROM odds_event_matches m
          WHERE m.game_id = c.game_id AND c.close_snapshot_at >= m.commence_at
      )
      OR (g.tip_tbd = false AND c.close_snapshot_at >= g.tip_at)
  );

-- check: l_timestamp_not_utc
-- Every timestamp column (and every column named *_at) must be TIMESTAMP WITH TIME ZONE,
-- which is how parquet marks a UTC instant.
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'main'
  AND (data_type LIKE 'TIMESTAMP%' OR column_name LIKE '%\_at' ESCAPE '\')
  AND data_type <> 'TIMESTAMP WITH TIME ZONE';

-- check: m_ratings_daily_duplicate_pk
SELECT rating_date, team_id, source, count(*) AS n
FROM ratings_daily
GROUP BY rating_date, team_id, source
HAVING count(*) > 1;

-- check: n_ratings_time_machine_as_of_wrong
-- A Time Machine file dated D is known at 06:00 US Eastern on D+1 (DECISIONS.md,
-- 2026-11-11). Anything else would let a file reach games it already counts.
SELECT rating_date, team_id
FROM ratings_daily
WHERE source = 'torvik_time_machine'
  AND as_of <> timezone('America/New_York', (rating_date + 1)::timestamp + INTERVAL 6 HOUR);

-- check: o_ratings_daily_missing_values
SELECT rating_date, team_id, source
FROM ratings_daily
WHERE adj_o IS NULL OR adj_d IS NULL OR adj_tempo IS NULL OR barthag IS NULL
   OR wins IS NULL OR losses IS NULL OR as_of IS NULL OR season IS NULL;

-- check: p_game_history_duplicate_pk
SELECT game_id, count(*) AS n FROM game_history GROUP BY game_id HAVING count(*) > 1;

-- check: q_game_history_ratings_not_before_tip
-- No-leakage rule 2: every game's ratings were known strictly before tip.
SELECT game_id FROM game_history WHERE ratings_as_of >= tip_at;

-- check: r_game_history_missing_games
-- One row per game in `games`, no more and no fewer.
SELECT game_id FROM games ANTI JOIN game_history USING (game_id)
UNION ALL
SELECT game_id FROM game_history ANTI JOIN games USING (game_id);

-- check: s_rotation_players_duplicate_pk
SELECT game_id, team_id, athlete_id, count(*) AS n
FROM rotation_players
GROUP BY game_id, team_id, athlete_id
HAVING count(*) > 1;

-- check: t_rotation_more_than_eight
SELECT game_id, team_id, count(*) AS n
FROM rotation_players
WHERE is_rotation
GROUP BY game_id, team_id
HAVING count(*) > 8;

-- check: u_rotation_without_earlier_minutes
-- A rotation player must have played before this game (point in time).
SELECT game_id, team_id, athlete_id
FROM rotation_players
WHERE is_rotation AND NOT (games_played_before > 0 AND minutes_before > 0);
