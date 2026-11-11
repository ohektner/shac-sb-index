-- When is a Torvik Time Machine file known? (task 1.4; DuckDB). Aggregates only.
-- Run with: uv run python -m shac.tables.timemachine_check --data-dir <private repo>
--
-- Tables the runner provides:
--   tm(season, rating_date, team_id, games, as_of)
--       Time Machine rows from tables/ratings_daily.parquet; games = wins + losses.
--       as_of = 06:00 US Eastern on rating_date + 1 (the rule under test).
--   team_games(season, game_id, team_id, tip_at)
--       Every final CBBD game for a D-I team, against ANY opponent (non-D-I included,
--       because Torvik's records count those games from 2019-20 on).
--
-- Torvik's record (W-L) is the clock: if a file counts a game, the game was in it.

-- finding: window_test
-- Between two consecutive files of a team, the record should grow by exactly the
-- number of games the team tipped in the window [previous as_of, this as_of).
-- Compared for two candidate cutoffs: 06:00 Eastern on D+1 (the rule) and midnight
-- Eastern (the old reading of rule 3, "the file is the end of day D, Eastern").
WITH pairs AS (
    SELECT season, team_id, rating_date, games, as_of,
           games - lag(games) OVER w AS delta,
           lag(as_of) OVER w AS prev_as_of,
           rating_date - lag(rating_date) OVER w AS day_gap
    FROM tm
    WINDOW w AS (PARTITION BY season, team_id ORDER BY rating_date)
),
cutoffs AS (
    SELECT '06:00 ET on D+1' AS cutoff, INTERVAL 0 HOUR AS shift
    UNION ALL
    SELECT '00:00 ET on D+1', -INTERVAL 6 HOUR
),
scored AS (
    SELECT c.cutoff, p.season, p.delta,
           (SELECT count(*) FROM team_games g
             WHERE g.season = p.season AND g.team_id = p.team_id
               AND g.tip_at >= p.prev_as_of + c.shift
               AND g.tip_at < p.as_of + c.shift) AS n_tipped
    FROM pairs p, cutoffs c
    WHERE p.day_gap = 1
)
SELECT cutoff, season,
       count(*) AS team_days,
       round(avg(CASE WHEN delta = n_tipped THEN 1.0 ELSE 0 END), 4) AS share_exact,
       count(*) FILTER (WHERE delta > n_tipped) AS file_counts_more,
       count(*) FILTER (WHERE delta < n_tipped) AS file_counts_fewer
FROM scored
GROUP BY ALL
ORDER BY cutoff DESC, season;

-- finding: leak_check
-- The rule in use: a game gets the latest file with as_of < tip_at (same season).
-- Flag a team-game when that file counts MORE games than the team had tipped before
-- this one. A flag means either the file already holds this game (a leak) or Torvik
-- counts a game CBBD doesn't have (a record offset, not a leak; see team_seasons below).
WITH ordered AS (
    SELECT g.*,
           row_number() OVER (PARTITION BY season, team_id ORDER BY tip_at, game_id) - 1
               AS n_before
    FROM team_games g
),
chosen AS (
    SELECT o.season, o.team_id, o.game_id, o.n_before, t.games AS file_games
    FROM ordered o
    ASOF JOIN tm t
      ON t.season = o.season AND t.team_id = o.team_id AND o.tip_at > t.as_of
)
SELECT season,
       count(*) AS team_games_with_file,
       count(*) FILTER (WHERE file_games > n_before) AS flagged,
       count(DISTINCT team_id) FILTER (WHERE file_games > n_before) AS teams_flagged
FROM chosen
GROUP BY ALL
ORDER BY season;

-- finding: leak_check_team_seasons
-- Each flagged team-season: how many of its games are flagged and where the run starts.
-- A run that starts mid-season and lasts to the end (or until a later -1) is a record
-- offset: CBBD is missing one game Torvik counted. A single flagged game is the
-- pattern a real leak would leave.
WITH ordered AS (
    SELECT g.*,
           row_number() OVER (PARTITION BY season, team_id ORDER BY tip_at, game_id) - 1
               AS n_before,
           count(*) OVER (PARTITION BY season, team_id) AS n_games
    FROM team_games g
),
chosen AS (
    SELECT o.season, o.team_id, o.n_before, o.n_games, t.games AS file_games
    FROM ordered o
    ASOF JOIN tm t
      ON t.season = o.season AND t.team_id = o.team_id AND o.tip_at > t.as_of
)
SELECT season,
       count(*) FILTER (WHERE file_games > n_before) AS games_flagged,
       min(n_before) FILTER (WHERE file_games > n_before) + 1 AS first_flagged_game_no,
       max(n_before) FILTER (WHERE file_games > n_before) + 1 AS last_flagged_game_no,
       max(n_games) AS games_in_season
FROM chosen
GROUP BY season, team_id
HAVING count(*) FILTER (WHERE file_games > n_before) > 0
ORDER BY season, games_flagged DESC;
