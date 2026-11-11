-- game_history: one row per D-I game, 2018-19 on, with point-in-time Torvik ratings for
-- both teams, the result and the closing lines (DuckDB). Task 1.6.
-- Run by shac.tables.build (shac.tables.history.build_game_history).
--
-- Tables the runner provides: games, results, ratings_daily, closing_lines.
--
-- THE NO-LEAKAGE RULE LIVES HERE. A game gets the latest ratings file whose `as_of` is
-- strictly before its tip (ARCHITECTURE.md rules 2 and 3). Both teams come from that same
-- file (same source, date AND as_of, so two pulls on one date are never mixed). If a
-- team isn't in it, its ratings stay empty: the game is not given an older file for one
-- side. Results and closing lines are LABELS and BENCHMARKS, never features;
-- the column lists in shac.tables.history say which is which.

WITH files AS (
    -- One row per ratings file (a source's snapshot of every team on one date).
    SELECT source, season, rating_date, as_of,
           count(*) AS file_n_teams,
           avg(adj_tempo) AS file_mean_tempo
    FROM ratings_daily
    GROUP BY source, season, rating_date, as_of
),
chosen AS (
    -- The latest file of the game's season known strictly before tip.
    SELECT g.game_id, f.source, f.rating_date, f.as_of, f.file_n_teams, f.file_mean_tempo
    FROM games g
    ASOF JOIN files f
      ON f.season = g.season AND g.tip_at > f.as_of
),
closes AS (
    SELECT game_id,
           max(close_home_spread) FILTER (WHERE source = 'cbbd') AS cbbd_close_home_spread,
           max(open_home_spread) FILTER (WHERE source = 'cbbd') AS cbbd_open_home_spread,
           max(basis) FILTER (WHERE source = 'cbbd') AS cbbd_close_basis,
           max(close_home_spread) FILTER (WHERE source = 'own_snapshots')
               AS own_close_home_spread,
           max(close_home_spread) FILTER (WHERE source = 'pinnacle') AS pinnacle_close_home_spread
    FROM closing_lines
    GROUP BY game_id
)
SELECT
    g.game_id, g.season, g.season_type, g.tip_at, g.tip_tbd, g.neutral_site,
    g.conference_game, g.home_team_id, g.away_team_id,
    -- features (point-in-time; as_of = ratings_as_of)
    c.source AS ratings_source,
    c.rating_date,
    c.as_of AS ratings_as_of,
    c.file_n_teams,
    c.file_mean_tempo,
    h.adj_o AS home_adj_o, h.adj_d AS home_adj_d, h.adj_tempo AS home_adj_tempo,
    h.barthag AS home_barthag, h.rank AS home_rank,
    a.adj_o AS away_adj_o, a.adj_d AS away_adj_d, a.adj_tempo AS away_adj_tempo,
    a.barthag AS away_barthag, a.rank AS away_rank,
    -- labels (the result; status is only final after the game)
    g.status, r.home_points, r.away_points, r.home_margin, r.overtimes,
    -- benchmarks (closing lines; never features)
    cl.cbbd_close_home_spread, cl.cbbd_open_home_spread, cl.cbbd_close_basis,
    cl.own_close_home_spread, cl.pinnacle_close_home_spread
FROM games g
LEFT JOIN chosen c USING (game_id)
LEFT JOIN ratings_daily h
  ON h.source = c.source AND h.rating_date = c.rating_date AND h.as_of = c.as_of
 AND h.team_id = g.home_team_id
LEFT JOIN ratings_daily a
  ON a.source = c.source AND a.rating_date = c.rating_date AND a.as_of = c.as_of
 AND a.team_id = g.away_team_id
LEFT JOIN results r USING (game_id)
LEFT JOIN closes cl USING (game_id)
ORDER BY g.tip_at, g.game_id;
