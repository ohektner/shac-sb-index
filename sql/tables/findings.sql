-- Data-quality findings for docs/TABLES.md (DuckDB). Aggregates only: counts and shares.
-- Run with: uv run python -m shac.tables.findings --data-dir <private repo>
-- Same views as sql/checks/tables.sql (one per parquet table).

-- finding: spread_sign
-- Is CBBD's `spread` the HOME team's spread? If so, a negative spread (home favored)
-- should go with home wins well over half the time, a positive one with home losses,
-- and the home moneyline should be the shorter price whenever the spread is negative.
SELECT g.season,
       l.provider,
       count(*) FILTER (WHERE l.home_spread < 0) AS n_home_fav,
       round(avg(CASE WHEN r.home_margin > 0 THEN 1.0 ELSE 0 END)
             FILTER (WHERE l.home_spread < 0), 3) AS home_win_rate_when_home_fav,
       count(*) FILTER (WHERE l.home_spread > 0) AS n_home_dog,
       round(avg(CASE WHEN r.home_margin > 0 THEN 1.0 ELSE 0 END)
             FILTER (WHERE l.home_spread > 0), 3) AS home_win_rate_when_home_dog,
       count(*) FILTER (WHERE l.home_spread <> 0 AND l.home_moneyline IS NOT NULL
                          AND l.away_moneyline IS NOT NULL
                          AND l.home_moneyline <> l.away_moneyline) AS n_with_moneylines,
       round(avg(CASE WHEN (l.home_spread < 0) = (l.home_moneyline < l.away_moneyline)
                      THEN 1.0 ELSE 0 END)
             FILTER (WHERE l.home_spread <> 0 AND l.home_moneyline IS NOT NULL
                       AND l.away_moneyline IS NOT NULL
                       AND l.home_moneyline <> l.away_moneyline), 3) AS moneyline_agrees,
       round(corr(-l.home_spread, r.home_margin), 3) AS corr_implied_vs_actual_margin
FROM cbbd_lines l
JOIN games g USING (game_id)
JOIN results r USING (game_id)
WHERE l.home_spread IS NOT NULL
GROUP BY ALL
ORDER BY g.season, l.provider;

-- finding: open_vs_current
-- How often `spreadOpen` is filled, and how often it equals `spread`.
SELECT g.season,
       l.provider,
       count(*) FILTER (WHERE l.home_spread IS NOT NULL) AS n_spread,
       count(*) FILTER (WHERE l.home_spread_open IS NOT NULL) AS n_open,
       count(*) FILTER (WHERE l.home_spread_open = l.home_spread) AS n_open_equals_spread,
       round(count(*) FILTER (WHERE l.home_spread_open = l.home_spread)
             / nullif(count(*) FILTER (WHERE l.home_spread_open IS NOT NULL
                                         AND l.home_spread IS NOT NULL), 0), 3)
           AS share_equal_when_both
FROM cbbd_lines l
JOIN games g USING (game_id)
GROUP BY ALL
ORDER BY g.season, l.provider;

-- finding: provider_coverage
-- Share of D-I games (status final) with a spread from each provider, per season.
WITH finals AS (
    SELECT game_id, season FROM games WHERE status = 'final'
),
per_provider AS (
    SELECT f.season, l.provider, count(DISTINCT f.game_id) AS n_games
    FROM finals f
    JOIN cbbd_lines l USING (game_id)
    WHERE l.home_spread IS NOT NULL
    GROUP BY ALL
),
any_line AS (
    SELECT f.season, count(DISTINCT l.game_id) AS n_any
    FROM finals f
    LEFT JOIN cbbd_lines l ON l.game_id = f.game_id AND l.home_spread IS NOT NULL
    GROUP BY ALL
),
totals AS (SELECT season, count(*) AS n_final FROM finals GROUP BY season)
SELECT t.season, t.n_final, a.n_any AS n_with_any_spread,
       round(a.n_any / t.n_final, 3) AS share_any,
       p.provider, p.n_games, round(p.n_games / t.n_final, 3) AS share_provider
FROM totals t
JOIN any_line a USING (season)
LEFT JOIN per_provider p USING (season)
ORDER BY t.season, p.provider;

-- finding: games_by_status
SELECT season, status, count(*) AS n FROM games GROUP BY ALL ORDER BY season, status;

-- finding: overtimes
SELECT g.season,
       count(*) AS n_results,
       count(*) FILTER (WHERE r.overtimes IS NULL) AS n_overtimes_unknown,
       count(*) FILTER (WHERE r.overtimes >= 1) AS n_overtime_games,
       max(r.overtimes) AS max_overtimes,
       count(*) FILTER (WHERE r.home_margin = 0) AS n_tied_margin,
       count(*) FILTER (WHERE r.home_points = 0 OR r.away_points = 0) AS n_zero_score
FROM results r
JOIN games g USING (game_id)
GROUP BY ALL
ORDER BY g.season;

-- finding: player_minutes
SELECT g.season,
       count(*) AS n_rows,
       count(*) FILTER (WHERE p.minutes IS NULL) AS n_minutes_null,
       count(*) FILTER (WHERE p.minutes = 0) AS n_minutes_zero,
       count(*) FILTER (WHERE NOT p.played) AS n_did_not_play,
       count(DISTINCT p.game_id) AS n_games_with_box
FROM player_box p
JOIN games g USING (game_id)
GROUP BY ALL
ORDER BY g.season;

-- finding: cbbd_close_basis
SELECT g.season, c.basis, count(*) AS n, round(avg(c.n_books), 2) AS avg_n_books
FROM closing_lines c
JOIN games g USING (game_id)
WHERE c.source = 'cbbd'
GROUP BY ALL
ORDER BY g.season, c.basis;

-- finding: odds_books
SELECT snapshot_at, count(DISTINCT odds_event_id) AS n_events, count(DISTINCT book) AS n_books,
       count(*) FILTER (WHERE book = 'pinnacle') AS n_pinnacle_rows
FROM odds_snapshots
GROUP BY snapshot_at
ORDER BY snapshot_at;
