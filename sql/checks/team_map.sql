-- Team-map checks (DuckDB). Each check returns ZERO rows when the map is good.
-- Run with: uv run python -m shac.teams.check --data-dir <private repo>
--
-- Tables the runner provides:
--   teams(team_id, name, conference, first_season, last_season)        data/teams.csv
--   aliases(source, alias, valid_from_season, valid_to_season, team_id) data/team_aliases.csv
--   review(source, alias, seasons, ...)                                 data/team_map_review.csv
--   cbbd_team_seasons(season, team_id)       CBBD /teams lists: who is D-I each season
--   cbbd_games(game_id, season, home_team_id, home_team, away_team_id, away_team)
--   torvik_names(season, file_kind, name)    distinct names in Torvik files
--   odds_names(season, name)                 distinct names in Odds API snapshots
--
-- An alias "applies" in a season when valid_from_season <= season <= valid_to_season
-- (an empty valid_to_season means still in use).

-- check: a_cbbd_games_unresolved
-- CBBD games 2019-2026 between two D-I teams where a team name does not resolve to
-- exactly one team_id, or resolves to a different id than the game says.
WITH di_games AS (
    SELECT g.*
    FROM cbbd_games g
    JOIN cbbd_team_seasons h ON h.season = g.season AND h.team_id = g.home_team_id
    JOIN cbbd_team_seasons a ON a.season = g.season AND a.team_id = g.away_team_id
    WHERE g.season BETWEEN 2019 AND 2026
),
sides AS (
    SELECT game_id, season, home_team_id AS team_id, home_team AS name FROM di_games
    UNION ALL
    SELECT game_id, season, away_team_id, away_team FROM di_games
),
resolved AS (
    SELECT s.game_id, s.team_id, count(al.team_id) AS n_matches,
           min(al.team_id) AS mapped_id
    FROM sides s
    LEFT JOIN aliases al
      ON al.source = 'cbbd' AND al.alias = s.name
     AND s.season >= al.valid_from_season
     AND s.season <= coalesce(al.valid_to_season, 9999)
    GROUP BY s.game_id, s.team_id
)
SELECT DISTINCT game_id
FROM resolved
WHERE n_matches <> 1 OR mapped_id <> team_id;

-- check: b_alias_two_teams_same_season
-- One source's name pointing at two different teams in overlapping seasons.
SELECT a1.source, a1.alias, a1.team_id AS team_id_1, a2.team_id AS team_id_2
FROM aliases a1
JOIN aliases a2
  ON a1.source = a2.source AND a1.alias = a2.alias AND a1.team_id < a2.team_id
WHERE a1.valid_from_season <= coalesce(a2.valid_to_season, 9999)
  AND a2.valid_from_season <= coalesce(a1.valid_to_season, 9999);

-- check: c_alias_team_missing_from_teams
SELECT al.source, al.alias, al.team_id
FROM aliases al
ANTI JOIN teams t ON t.team_id = al.team_id;

-- check: d_torvik_names_unmatched
-- Every name in a Torvik team_results file (Torvik's D-I list) must resolve that season.
-- in_review = true means it is deliberately waiting in data/team_map_review.csv.
SELECT n.season, n.name,
       EXISTS (SELECT 1 FROM review r WHERE r.source = 'torvik' AND r.alias = n.name) AS in_review
FROM torvik_names n
WHERE n.file_kind = 'team_results'
  AND NOT EXISTS (
      SELECT 1 FROM aliases al
      WHERE al.source = 'torvik' AND al.alias = n.name
        AND n.season >= al.valid_from_season
        AND n.season <= coalesce(al.valid_to_season, 9999)
  );

-- check: e_odds_names_unmatched
SELECT n.season, n.name,
       EXISTS (SELECT 1 FROM review r WHERE r.source = 'odds_api' AND r.alias = n.name) AS in_review
FROM odds_names n
WHERE NOT EXISTS (
    SELECT 1 FROM aliases al
    WHERE al.source = 'odds_api' AND al.alias = n.name
      AND n.season >= al.valid_from_season
      AND n.season <= coalesce(al.valid_to_season, 9999)
);

-- check: f_torvik_two_names_one_team
-- Two different Torvik team_results names landing on the same team in one season
-- would mean one of them is mapped wrong.
WITH m AS (
    SELECT n.season, n.name, al.team_id
    FROM torvik_names n
    JOIN aliases al
      ON al.source = 'torvik' AND al.alias = n.name
     AND n.season >= al.valid_from_season
     AND n.season <= coalesce(al.valid_to_season, 9999)
    WHERE n.file_kind = 'team_results'
)
SELECT season, team_id, count(DISTINCT name) AS n_names
FROM m
GROUP BY season, team_id
HAVING count(DISTINCT name) > 1;

-- check: g_torvik_name_off_di_list
-- A Torvik team_results name mapped to a team that CBBD says is not D-I that season
-- (only for seasons that have a CBBD /teams list).
SELECT n.season, n.name, al.team_id
FROM torvik_names n
JOIN aliases al
  ON al.source = 'torvik' AND al.alias = n.name
 AND n.season >= al.valid_from_season
 AND n.season <= coalesce(al.valid_to_season, 9999)
WHERE n.file_kind = 'team_results'
  AND n.season IN (SELECT DISTINCT season FROM cbbd_team_seasons)
  AND NOT EXISTS (
      SELECT 1 FROM cbbd_team_seasons c WHERE c.season = n.season AND c.team_id = al.team_id
  );
