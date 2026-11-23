-- Gold: the CBBD closing lines and game_history, mirroring sql/tables/closing_lines.sql
-- (cbbd source only; Odds API data stays out of Databricks) and sql/tables/game_history.sql.

-- CBBD's line per game: Draft Kings when it has one, else the median of the other
-- providers, leaving out `consensus` and `Bovada` (DECISIONS.md 2026-11-21).
-- LINE CHECK 1, books disagree (DECISIONS.md 2026-11-22): two of those providers favor
-- different teams by 3+ points each. The game keeps its row with no close or open.
CREATE OR REFRESH MATERIALIZED VIEW gold_closing_lines (
  CONSTRAINT cbbd_books_agree EXPECT (basis <> 'dropped_books_disagree')
)
COMMENT "CBBD close and open per game (home spread; negative = home favored)."
AS WITH cbbd_dk AS (
  SELECT game_id, home_spread AS close_home_spread, home_spread_open AS open_home_spread
  FROM silver_cbbd_lines
  WHERE provider = 'Draft Kings' AND home_spread IS NOT NULL
),
cbbd_median AS (
  SELECT game_id,
         median(home_spread) AS close_home_spread,
         count(home_spread) AS n_books,
         median(home_spread_open) AS open_home_spread
  FROM silver_cbbd_lines
  WHERE home_spread IS NOT NULL
    AND provider <> 'consensus'
    AND provider <> 'Bovada'
  GROUP BY game_id
),
cbbd_books_disagree AS (
  SELECT game_id
  FROM silver_cbbd_lines
  WHERE home_spread IS NOT NULL
    AND provider NOT IN ('consensus', 'Bovada')
  GROUP BY game_id
  HAVING min(home_spread) <= -3 AND max(home_spread) >= 3
)
SELECT
  m.game_id,
  'cbbd' AS source,
  CAST(CASE WHEN x.game_id IS NULL THEN coalesce(d.close_home_spread, m.close_home_spread)
       END AS DOUBLE) AS close_home_spread,
  CAST(CASE WHEN d.game_id IS NOT NULL THEN 1 ELSE m.n_books END AS INT) AS n_books,
  CAST(CASE WHEN x.game_id IS NOT NULL THEN NULL
            WHEN d.game_id IS NOT NULL THEN d.open_home_spread
            ELSE m.open_home_spread END AS DOUBLE) AS open_home_spread,
  CASE WHEN x.game_id IS NOT NULL THEN 'dropped_books_disagree'
       WHEN d.game_id IS NOT NULL THEN 'draftkings'
       ELSE 'median_cbbd_providers' END AS basis
FROM cbbd_median m
LEFT JOIN cbbd_dk d USING (game_id)
LEFT JOIN cbbd_books_disagree x USING (game_id);

-- game_history: every D-I game with point-in-time ratings, result and CBBD close.
-- THE NO-LEAKAGE RULE: a game gets the latest ratings file whose as_of is strictly before
-- its tip, both teams from that same file. The FAIL expectation stops the update if any
-- row breaks it.
-- LINE CHECK 2, wrong team: the CBBD close is 10+ points from a plain Torvik number and
-- its mirror image is at least 3 times closer. The close and open are emptied, never
-- turned around.
CREATE OR REFRESH MATERIALIZED VIEW gold_game_history (
  CONSTRAINT no_leakage EXPECT (ratings_as_of IS NULL OR ratings_as_of < tip_at)
    ON VIOLATION FAIL UPDATE,
  CONSTRAINT cbbd_line_on_right_team EXPECT (cbbd_close_basis IS DISTINCT FROM 'dropped_wrong_team')
)
COMMENT "One row per D-I game: keys, point-in-time features, labels, CBBD benchmark."
AS WITH files AS (
  SELECT source, season, rating_date, as_of,
         count(*) AS file_n_teams,
         avg(adj_tempo) AS file_mean_tempo
  FROM silver_ratings_daily
  GROUP BY source, season, rating_date, as_of
),
chosen AS (
  SELECT g.game_id, f.source, f.rating_date, f.as_of, f.file_n_teams, f.file_mean_tempo
  FROM silver_games g
  JOIN files f ON f.season = g.season AND f.as_of < g.tip_at
  QUALIFY row_number() OVER (
    PARTITION BY g.game_id ORDER BY f.as_of DESC, f.source, f.rating_date
  ) = 1
),
joined AS (
  SELECT
    g.game_id, g.season, g.season_type, g.tip_at, g.tip_tbd, g.neutral_site,
    g.conference_game, g.home_team_id, g.away_team_id,
    c.source AS ratings_source,
    c.rating_date,
    c.as_of AS ratings_as_of,
    c.file_n_teams,
    c.file_mean_tempo,
    h.adj_o AS home_adj_o, h.adj_d AS home_adj_d, h.adj_tempo AS home_adj_tempo,
    h.barthag AS home_barthag, h.rank AS home_rank,
    a.adj_o AS away_adj_o, a.adj_d AS away_adj_d, a.adj_tempo AS away_adj_tempo,
    a.barthag AS away_barthag, a.rank AS away_rank,
    g.status, r.home_points, r.away_points, r.home_margin, r.overtimes,
    cl.close_home_spread AS cbbd_close_home_spread,
    cl.open_home_spread AS cbbd_open_home_spread,
    cl.basis AS cbbd_close_basis,
    CASE WHEN g.neutral_site IS NOT NULL THEN
      ((h.adj_o - a.adj_o) + (a.adj_d - h.adj_d))
        * (h.adj_tempo * a.adj_tempo / c.file_mean_tempo) / 100
      + CASE WHEN g.neutral_site THEN 0.0 ELSE 3.4 END
    END AS torvik_check
  FROM silver_games g
  LEFT JOIN chosen c ON c.game_id = g.game_id
  LEFT JOIN silver_ratings_daily h
    ON h.source = c.source AND h.rating_date = c.rating_date AND h.as_of = c.as_of
   AND h.team_id = g.home_team_id
  LEFT JOIN silver_ratings_daily a
    ON a.source = c.source AND a.rating_date = c.rating_date AND a.as_of = c.as_of
   AND a.team_id = g.away_team_id
  LEFT JOIN silver_results r ON r.game_id = g.game_id
  LEFT JOIN gold_closing_lines cl ON cl.game_id = g.game_id
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
SELECT
  game_id, season, season_type, tip_at, tip_tbd, neutral_site, conference_game,
  home_team_id, away_team_id,
  ratings_source, rating_date, ratings_as_of, file_n_teams, file_mean_tempo,
  home_adj_o, home_adj_d, home_adj_tempo, home_barthag, home_rank,
  away_adj_o, away_adj_d, away_adj_tempo, away_barthag, away_rank,
  status, home_points, away_points, home_margin, overtimes,
  CASE WHEN wrong_team THEN NULL ELSE cbbd_close_home_spread END AS cbbd_close_home_spread,
  CASE WHEN wrong_team THEN NULL ELSE cbbd_open_home_spread END AS cbbd_open_home_spread,
  CASE WHEN wrong_team THEN 'dropped_wrong_team' ELSE cbbd_close_basis END AS cbbd_close_basis
FROM checked;
