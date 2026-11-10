-- Private-table checks (DuckDB). Each check returns ZERO rows when the tables are good.
-- Run with: uv run python -m shac.tables.check --data-dir <private repo>
--
-- The runner makes one view per parquet file in <private repo>/tables/:
--   games, results, player_box, cbbd_lines, odds_snapshots, odds_event_matches,
--   closing_lines

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
