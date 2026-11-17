-- Task 1.2: is CBBD's `spread` a true close? Tests T1-T3, T5 and L1 from docs/BACKTEST_CLOSE.md.
-- Counts and shares only. Views expected: games, results, cbbd_lines, odds_snapshots.
-- Seasons are the ending year: 2027 = 2026-27. Provider P: 'Draft Kings'/'draftkings', 'Bovada'/'bovada'.

-- check: tested
CREATE OR REPLACE TEMP VIEW tested AS
-- Tip = the feed's latest commence time, or CBBD's scheduled tip if earlier and not a
-- placeholder (the same rule as sql/tables/closing_lines.sql).
WITH tip AS (
  SELECT s.game_id,
         least(arg_max(s.commence_at, s.snapshot_at),
               coalesce(any_value(CASE WHEN NOT g.tip_tbd THEN g.tip_at END),
                        arg_max(s.commence_at, s.snapshot_at))) AS tip_at
  FROM odds_snapshots s JOIN games g USING (game_id)
  WHERE s.game_id IS NOT NULL
  GROUP BY s.game_id
),
snap AS (
  SELECT s.game_id, s.book, s.snapshot_at, s.home_spread,
         epoch(t.tip_at) - epoch(s.snapshot_at) AS secs_before_tip
  FROM odds_snapshots s JOIN tip t USING (game_id)
  WHERE s.book IN ('draftkings', 'bovada') AND s.home_spread IS NOT NULL
),
close_ AS (
  SELECT game_id, book, snapshot_at AS close_at, home_spread AS own_close, secs_before_tip
  FROM snap WHERE secs_before_tip > 0 AND secs_before_tip <= 3600
  QUALIFY row_number() OVER (PARTITION BY game_id, book ORDER BY snapshot_at DESC) = 1
),
open_ AS (
  SELECT s.game_id, s.book, s.home_spread AS own_open
  FROM snap s JOIN close_ c USING (game_id, book)
  WHERE epoch(c.close_at) - epoch(s.snapshot_at) >= 6 * 3600
  QUALIFY row_number() OVER (PARTITION BY s.game_id, s.book ORDER BY s.snapshot_at ASC) = 1
)
SELECT l.provider, g.game_id, l.home_spread AS spread, l.home_spread_open AS spread_open,
       c.own_close, o.own_open, r.home_margin
FROM cbbd_lines l
JOIN games g USING (game_id)
JOIN results r USING (game_id)
JOIN close_ c ON c.game_id = l.game_id
             AND c.book = CASE l.provider WHEN 'Draft Kings' THEN 'draftkings' WHEN 'Bovada' THEN 'bovada' END
LEFT JOIN open_ o ON o.game_id = c.game_id AND o.book = c.book
WHERE g.season = 2027 AND l.provider IN ('Draft Kings', 'Bovada') AND l.home_spread IS NOT NULL;

-- check: tests_by_provider
SELECT provider,
  count(*) AS tested_games,
  count(*) FILTER (WHERE abs(own_close) >= 2) AS t1_n,
  round(100.0 * count(*) FILTER (WHERE abs(own_close) >= 2 AND sign(spread) = sign(own_close))
        / nullif(count(*) FILTER (WHERE abs(own_close) >= 2), 0), 1) AS t1_same_sign_pct,
  count(*) FILTER (WHERE own_open IS NOT NULL AND abs(own_close - own_open) >= 1.0) AS movers,
  round(100.0 * count(*) FILTER (WHERE own_open IS NOT NULL AND abs(own_close - own_open) >= 1.0
        AND abs(spread - own_close) < abs(spread - own_open))
        / nullif(count(*) FILTER (WHERE own_open IS NOT NULL AND abs(own_close - own_open) >= 1.0), 0), 1) AS t2_near_close_pct,
  round(100.0 * count(*) FILTER (WHERE own_open IS NOT NULL AND abs(own_close - own_open) >= 1.0
        AND abs(spread - own_open) < abs(spread - own_close))
        / nullif(count(*) FILTER (WHERE own_open IS NOT NULL AND abs(own_close - own_open) >= 1.0), 0), 1) AS t2_near_open_pct,
  round(100.0 * count(*) FILTER (WHERE abs(spread - own_close) <= 0.5) / count(*), 1) AS t3_exact_pct,
  round(100.0 * count(*) FILTER (WHERE abs(spread - own_close) >= 3.0) / count(*), 1) AS t4a_far_pct,
  count(*) FILTER (WHERE spread_open IS NOT NULL AND own_open IS NOT NULL) AS t5_n,
  round(100.0 * count(*) FILTER (WHERE spread_open IS NOT NULL AND own_open IS NOT NULL
        AND abs(spread_open - own_open) <= 1.0)
        / nullif(count(*) FILTER (WHERE spread_open IS NOT NULL AND own_open IS NOT NULL), 0), 1) AS t5_pct
FROM tested GROUP BY provider ORDER BY provider;

-- check: l1_season_2026 (spread vs spreadOpen against results; season 2026 = 2025-26)
WITH x AS (
  SELECT l.provider, r.home_margin, l.home_spread AS s, l.home_spread_open AS so
  FROM cbbd_lines l JOIN games g USING (game_id) JOIN results r USING (game_id)
  WHERE g.season = 2026 AND l.provider IN ('Draft Kings', 'Bovada')
    AND l.home_spread IS NOT NULL AND l.home_spread_open IS NOT NULL
    AND abs(l.home_spread - l.home_spread_open) >= 1
), d AS (
  SELECT provider, abs(-so - home_margin) - abs(-s - home_margin) AS d FROM x
)
SELECT provider, count(*) AS n, round(avg(d), 3) AS mean_d, round(stddev_samp(d), 3) AS sd,
       round(avg(d) / (stddev_samp(d) / sqrt(count(*))), 2) AS t_stat
FROM d GROUP BY provider ORDER BY provider;
