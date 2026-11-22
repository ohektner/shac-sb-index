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
--
-- LINE CHECK: CBBD line on the wrong team (DECISIONS.md 2026-11-22). Some CBBD lines,
-- mostly at neutral sites, give the right number but the wrong team (Duke -8.5 stored
-- as Texas Tech -8.5). Every book agrees, so closing_lines.sql's books-disagree check
-- can't see it. Here a CBBD close is checked against a plain Torvik number for the
-- game, from the point-in-time ratings above:
--   torvik_check = rating_margin + 3.4 at a true home game (0 at a neutral site)
--   rating_margin as in sql/model/features.sql; 3.4 = the baseline's home court,
--   rounded (3.36 to 3.47 by season in scorecard/backtest/baseline_fit.csv)
-- The close is dropped when it is 10+ points from torvik_check AND its mirror image
-- (the same number on the other team) is at least 3 times closer. Dropped means the
-- cbbd close and open are emptied and cbbd_close_basis = 'dropped_wrong_team'; the line
-- is never turned around. The check reads features and the line, never the result, and
-- only removes a benchmark: nothing here flows into a feature.

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
),
joined AS (
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
    cl.own_close_home_spread, cl.pinnacle_close_home_spread,
    -- for the line check only (dropped from the output)
    CASE WHEN g.neutral_site IS NOT NULL THEN
        ((h.adj_o - a.adj_o) + (a.adj_d - h.adj_d))
            * (h.adj_tempo * a.adj_tempo / c.file_mean_tempo) / 100
        + CASE WHEN g.neutral_site THEN 0.0 ELSE 3.4 END
    END AS torvik_check
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
),
checked AS (
    SELECT *,
           coalesce(
               abs(torvik_check + cbbd_close_home_spread) >= 10
               AND 3 * abs(torvik_check - cbbd_close_home_spread)
                   <= abs(torvik_check + cbbd_close_home_spread),
               FALSE
           ) AS wrong_team
    FROM joined
)
SELECT * EXCLUDE (torvik_check, wrong_team) REPLACE (
    CASE WHEN wrong_team THEN NULL ELSE cbbd_close_home_spread END AS cbbd_close_home_spread,
    CASE WHEN wrong_team THEN NULL ELSE cbbd_open_home_spread END AS cbbd_open_home_spread,
    CASE WHEN wrong_team THEN 'dropped_wrong_team' ELSE cbbd_close_basis END
        AS cbbd_close_basis
)
FROM checked
ORDER BY tip_at, game_id;
