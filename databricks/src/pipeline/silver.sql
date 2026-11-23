-- Silver: the raw files cleaned into the same tables `shac.tables.build` makes locally
-- (games, results, cbbd_lines, ratings_daily). Each step mirrors the Python it replaces;
-- the expectations below show, on every update, how many rows each rule dropped.
-- Parity with the local build is checked by src/parity.py (docs/DATABRICKS.md, D1).

-- CBBD games: one record per game id, the copy from the most recently fetched file
-- (ties broken by file name), as shac.tables.raw.dedupe_files does.
CREATE OR REFRESH MATERIALIZED VIEW silver_cbbd_game_records
COMMENT "CBBD game records, de-duplicated across overlapping fetch windows."
AS SELECT fetched_at, file_name, g.*
FROM (SELECT fetched_at, file_name, explode(data) AS g FROM bronze_cbbd_games)
QUALIFY row_number() OVER (PARTITION BY g.id ORDER BY fetched_at DESC, file_name DESC) = 1;

-- Only games between two D-I teams (teams.csv first_season..last_season).
CREATE OR REFRESH MATERIALIZED VIEW silver_games (
  CONSTRAINT both_teams_division_one EXPECT (home_d1 AND away_d1) ON VIOLATION DROP ROW
)
COMMENT "One row per D-I vs D-I game (game_id = CBBD game id)."
AS SELECT
  r.id AS game_id,
  r.season,
  r.seasonType AS season_type,
  CAST(r.startDate AS TIMESTAMP) AS tip_at,
  r.startTimeTbd AS tip_tbd,
  r.homeTeamId AS home_team_id,
  r.awayTeamId AS away_team_id,
  r.neutralSite AS neutral_site,
  r.conferenceGame AS conference_game,
  r.status,
  CAST(r.fetched_at AS TIMESTAMP) AS fetched_at,
  coalesce(r.season BETWEEN h.first_season AND h.last_season, FALSE) AS home_d1,
  coalesce(r.season BETWEEN a.first_season AND a.last_season, FALSE) AS away_d1
FROM silver_cbbd_game_records r
LEFT JOIN ref_teams h ON h.team_id = r.homeTeamId
LEFT JOIN ref_teams a ON a.team_id = r.awayTeamId;

-- Final games with both scores. Overtimes from the period lists: 2 periods = none.
CREATE OR REFRESH MATERIALIZED VIEW silver_results (
  CONSTRAINT has_both_scores EXPECT (home_points IS NOT NULL AND away_points IS NOT NULL)
    ON VIOLATION DROP ROW
)
COMMENT "One row per final D-I game with both scores."
AS SELECT
  r.id AS game_id,
  r.homePoints AS home_points,
  r.awayPoints AS away_points,
  r.homePoints - r.awayPoints AS home_margin,
  CASE WHEN size(coalesce(r.homePeriodPoints, array())) = size(coalesce(r.awayPeriodPoints, array()))
        AND size(coalesce(r.homePeriodPoints, array())) >= 2
       THEN size(r.homePeriodPoints) - 2 END AS overtimes,
  CAST(r.fetched_at AS TIMESTAMP) AS fetched_at
FROM silver_cbbd_game_records r
JOIN silver_games g ON g.game_id = r.id AND g.status = 'final';

-- CBBD lines: latest record per game; a record whose home/away teams differ from the
-- game is dropped, never flipped. One row per game and provider (last listed wins).
CREATE OR REFRESH MATERIALIZED VIEW silver_cbbd_line_records (
  CONSTRAINT teams_match_game EXPECT (teams_match) ON VIOLATION DROP ROW
)
COMMENT "CBBD lines records for D-I games, de-duplicated, with matching teams."
AS WITH latest AS (
  SELECT fetched_at, file_name, l.*
  FROM (SELECT fetched_at, file_name, explode(data) AS l FROM bronze_cbbd_lines)
  QUALIFY row_number() OVER (PARTITION BY l.gameId ORDER BY fetched_at DESC, file_name DESC) = 1
)
SELECT r.*, (r.homeTeamId <=> g.home_team_id AND r.awayTeamId <=> g.away_team_id) AS teams_match
FROM latest r
JOIN silver_games g ON g.game_id = r.gameId;

CREATE OR REFRESH MATERIALIZED VIEW silver_cbbd_lines
COMMENT "One row per D-I game per CBBD line provider. home_spread < 0 = home favored."
AS WITH lines AS (
  SELECT r.gameId AS game_id, CAST(r.fetched_at AS TIMESTAMP) AS fetched_at, p.pos, p.line
  FROM silver_cbbd_line_records r
  LATERAL VIEW posexplode(r.lines) p AS pos, line
)
SELECT
  game_id,
  line.provider,
  line.spread AS home_spread,
  line.spreadOpen AS home_spread_open,
  line.overUnder AS total,
  line.overUnderOpen AS total_open,
  line.homeMoneyline AS home_moneyline,
  line.awayMoneyline AS away_moneyline,
  CASE WHEN line.spread IS NULL OR line.homeMoneyline IS NULL OR line.awayMoneyline IS NULL
         THEN FALSE
       WHEN line.spread = 0 OR line.homeMoneyline = line.awayMoneyline THEN FALSE
       ELSE (line.spread < 0) <> (line.homeMoneyline < line.awayMoneyline) END AS suspect_sign,
  fetched_at
FROM lines
QUALIFY row_number() OVER (PARTITION BY game_id, line.provider ORDER BY pos DESC) = 1;

-- Torvik ratings by day, point-in-time (shac.tables.torvik).
-- Time Machine file dated D: known at 06:00 US Eastern on D+1. Our daily pulls: known at
-- fetch time, and only the first pull of each Eastern date is kept, whole.
CREATE OR REFRESH MATERIALIZED VIEW silver_torvik_rows (
  CONSTRAINT has_45_fields EXPECT (n_fields = 45) ON VIOLATION FAIL UPDATE,
  CONSTRAINT record_is_w_l EXPECT (record RLIKE '^[0-9]+-[0-9]+$') ON VIOLATION FAIL UPDATE
)
COMMENT "Every Torvik team row from both sources, parsed, before team mapping."
AS WITH tm_files AS (
  SELECT
    file_name,
    CAST(regexp_extract(source_file, '/timemachine/([0-9]{4})/', 1) AS INT) AS season,
    to_date(regexp_extract(file_name, '^([0-9]{8})_', 1), 'yyyyMMdd') AS rating_date,
    from_json(value, 'ARRAY<ARRAY<STRING>>') AS rows
  FROM bronze_torvik_time_machine
),
tm_rows AS (
  SELECT f.*, x.pos, x.r
  FROM tm_files f
  LATERAL VIEW posexplode(f.rows) x AS pos, r
),
tm AS (
  SELECT
    'torvik_time_machine' AS source, t.season, t.rating_date,
    CAST(t.rating_date AS STRING) AS rating_key,
    to_utc_timestamp(CAST(date_add(t.rating_date, 1) AS TIMESTAMP) + INTERVAL 6 HOURS,
                     'America/New_York') AS as_of,
    CAST(m.fetched_at_utc AS TIMESTAMP) AS fetched_at,
    t.pos, t.r, size(t.r) AS n_fields
  FROM tm_rows t
  LEFT JOIN bronze_torvik_manifest m
    ON m.status = 'ok'
   AND m.file = concat('torvik/timemachine/', t.season, '/', t.file_name)
),
daily_lines AS (
  SELECT
    *,
    CAST(regexp_extract(file_name, '^([0-9]{4})_team_results_', 1) AS INT) AS season,
    to_timestamp(regexp_extract(file_name, '_([0-9]{8}T[0-9]{6}Z)[.]csv[.]gz$', 1),
                 "yyyyMMdd'T'HHmmss'Z'") AS fetched,
    row_number() OVER (PARTITION BY source_file ORDER BY monotonically_increasing_id()) AS pos
  FROM bronze_torvik_daily
  WHERE source_file NOT LIKE '%/seasons/%'
    AND _c1 IS NOT NULL AND _c1 <> '' AND _c1 <> 'team'
),
daily AS (
  SELECT
    'torvik_daily' AS source, season,
    to_date(from_utc_timestamp(fetched, 'America/New_York')) AS rating_date,
    CAST(fetched AS STRING) AS rating_key,
    fetched AS as_of,
    fetched AS fetched_at,
    pos,
    array(_c0, _c1, _c2, _c3, _c4, _c5, _c6, _c7, _c8, _c9, _c10, _c11, _c12, _c13, _c14,
          _c15, _c16, _c17, _c18, _c19, _c20, _c21, _c22, _c23, _c24, _c25, _c26, _c27,
          _c28, _c29, _c30, _c31, _c32, _c33, _c34, _c35, _c36, _c37, _c38, _c39, _c40,
          _c41, _c42, _c43, _c44) AS r,
    CASE WHEN _c44 IS NULL THEN 44 ELSE 45 END AS n_fields
  FROM daily_lines
)
SELECT
  source, season, rating_date, rating_key, as_of, fetched_at, pos, n_fields,
  r[1] AS team,
  r[3] AS record,
  CAST(r[0] AS INT) AS rank,
  CAST(r[4] AS DOUBLE) AS adj_o,
  CAST(r[6] AS DOUBLE) AS adj_d,
  CAST(r[8] AS DOUBLE) AS barthag,
  CAST(r[44] AS DOUBLE) AS adj_tempo
FROM (SELECT * FROM tm UNION ALL SELECT * FROM daily);

-- Team names to team_id with the `torvik` aliases valid that season. A name with no
-- alias is dropped (and counted here); the map check keeps it at zero.
CREATE OR REFRESH MATERIALIZED VIEW silver_torvik_mapped (
  CONSTRAINT torvik_name_maps_to_team EXPECT (team_id IS NOT NULL) ON VIOLATION DROP ROW
)
COMMENT "Torvik rows with team_id; one row per (source, date key, team name)."
AS SELECT t.*, a.team_id
FROM silver_torvik_rows t
LEFT JOIN ref_team_aliases a
  ON a.source = 'torvik' AND a.alias = t.team
 AND t.season >= a.valid_from_season AND t.season <= coalesce(a.valid_to_season, 9999)
QUALIFY row_number() OVER (
  PARTITION BY t.source, t.season, t.rating_key, t.team
  ORDER BY a.team_id IS NULL, t.pos
) = 1;

CREATE OR REFRESH MATERIALIZED VIEW silver_ratings_daily (
  CONSTRAINT preseason_file_shows_no_games EXPECT (NOT preseason_played) ON VIOLATION DROP ROW
)
COMMENT "Torvik ratings by day, point-in-time: use a row only when as_of < tip."
AS WITH parsed AS (
  SELECT *,
    CAST(regexp_extract(record, '^([0-9]+)-', 1) AS INT) AS wins,
    CAST(regexp_extract(record, '-([0-9]+)$', 1) AS INT) AS losses
  FROM silver_torvik_mapped
),
tm AS (
  SELECT *,
    rating_date = min(rating_date) OVER (PARTITION BY season) AND wins + losses > 0
      AS preseason_played
  FROM parsed WHERE source = 'torvik_time_machine'
),
first_pull AS (
  SELECT * FROM parsed WHERE source = 'torvik_daily'
  QUALIFY as_of = min(as_of) OVER (PARTITION BY rating_date)
),
daily AS (
  SELECT *, FALSE AS preseason_played
  FROM first_pull
  QUALIFY row_number() OVER (PARTITION BY rating_date, team_id ORDER BY pos) = 1
)
SELECT rating_date, season, team_id, source, adj_o, adj_d, adj_tempo, barthag, rank,
       wins, losses, as_of, fetched_at, preseason_played
FROM (SELECT * FROM tm UNION ALL SELECT * FROM daily);
