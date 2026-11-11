-- rotation_players: who counted as a rotation player before each game, and whether they
-- played (DuckDB). The ground truth for the G2 availability checks. PREREGISTRATION.md:
-- a rotation player is one of a team's top 8 by minutes per game over the season to
-- date; "sat" means no minutes in the box score.
-- Run by shac.tables.build (shac.tables.rotation.build_rotation_players).
--
-- Tables the runner provides: games, player_box.
--
-- POINT IN TIME: a player's minutes per game and rank before a game use ONLY the team's
-- earlier games that season (earlier in tip order). The game's own box score fills only
-- the outcome columns (played, minutes, starter).
--
-- Choices (DECISIONS.md, 2026-11-11):
-- - Minutes per game = minutes before this game / games the player played (minutes > 0)
--   before it. An injured starter stays a rotation player while out, which is the case
--   the availability layer exists for.
-- - Only team-games with a box score count, before and after: a missing box is unknown,
--   not "everyone sat". Only D-I vs D-I games (the games table).
-- - Rank ties: more minutes before first, then lower athlete_id (stable, arbitrary).
-- - A team's first game of the season has no rotation yet (no earlier games): no rows.
-- - A player out for the season, or gone from the team, stays a rotation player by this
--   definition; games_sat_in_a_row_before tells those apart from new absences.

WITH team_games AS (
    -- Final games with a box score for this team, numbered in tip order per team-season.
    SELECT g.season, g.game_id, g.tip_at, b.team_id,
           row_number() OVER (PARTITION BY g.season, b.team_id ORDER BY g.tip_at, g.game_id)
               AS game_no
    FROM games g
    JOIN (SELECT DISTINCT game_id, team_id FROM player_box) b USING (game_id)
    WHERE g.status = 'final'
),
appearances AS (
    SELECT tg.season, tg.team_id, tg.game_no, p.athlete_id, p.player_name, p.minutes,
           p.played, p.starter
    FROM team_games tg
    JOIN player_box p ON p.game_id = tg.game_id AND p.team_id = tg.team_id
),
roster AS (
    -- Each player's first listing for the team that season (played or not).
    SELECT season, team_id, athlete_id, min(game_no) AS first_game_no,
           arg_max(player_name, game_no) AS player_name
    FROM appearances
    GROUP BY season, team_id, athlete_id
),
grid AS (
    -- Every team game from the player's first listing on, with this game's outcome.
    SELECT tg.season, tg.team_id, tg.game_id, tg.tip_at, tg.game_no, r.athlete_id,
           r.player_name,
           coalesce(a.minutes, 0) AS minutes,
           coalesce(a.played, false) AS played,
           coalesce(a.starter, false) AS starter
    FROM team_games tg
    JOIN roster r ON r.season = tg.season AND r.team_id = tg.team_id
                 AND tg.game_no >= r.first_game_no
    LEFT JOIN appearances a ON a.season = tg.season AND a.team_id = tg.team_id
                           AND a.game_no = tg.game_no AND a.athlete_id = r.athlete_id
),
to_date AS (
    -- Season-to-date numbers from EARLIER games only (the frame stops one row back).
    SELECT *,
           coalesce(sum(minutes) OVER w, 0) AS minutes_before,
           coalesce(sum(CASE WHEN played THEN 1 ELSE 0 END) OVER w, 0) AS games_played_before,
           max(CASE WHEN played THEN game_no END) OVER w AS last_played_game_no,
           game_no - 1 AS team_games_before
    FROM grid
    WINDOW w AS (PARTITION BY season, team_id, athlete_id ORDER BY game_no
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
ranked AS (
    SELECT *,
           CASE WHEN games_played_before > 0
                THEN minutes_before / games_played_before END AS mpg_before,
           row_number() OVER (
               PARTITION BY game_id, team_id
               ORDER BY CASE WHEN games_played_before > 0
                             THEN minutes_before / games_played_before END DESC NULLS LAST,
                        minutes_before DESC, athlete_id
           ) AS rank_all
    FROM to_date
    WHERE team_games_before > 0
)
SELECT season, game_id, team_id, tip_at, athlete_id, player_name,
       team_games_before, games_played_before, minutes_before, mpg_before,
       -- Team games in a row the player sat just before this one (0 = played last game).
       -- Lets G2 tell a new absence from a long-running one (season-ending injury, left
       -- the team), which stays "rotation" by the season-to-date definition.
       game_no - 1 - last_played_game_no AS games_sat_in_a_row_before,
       CASE WHEN games_played_before > 0 THEN rank_all END AS rotation_rank,
       games_played_before > 0 AND rank_all <= 8 AS is_rotation,
       played, minutes, starter
FROM ranked
ORDER BY tip_at, game_id, team_id, rank_all;
