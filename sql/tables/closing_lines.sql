-- closing_lines (DuckDB). One row per (game_id, source).
-- Run by: uv run python -m shac.tables.build --data-dir <private repo>
--
-- Tables the builder provides:
--   odds_snapshots(snapshot_at, odds_event_id, book, commence_at, home_spread, game_id, ...)
--   snapshot_files(snapshot_at)   every snapshot file's fetch time, even an empty one
--   us_books(book)                the US bookmaker keys (docs/VERIFIED.md)
--   cbbd_lines(game_id, provider, home_spread, home_spread_open, ...)
--
-- Sources:
--   own_snapshots  median home spread across US books in our LAST snapshot taken strictly
--                  before tip (snapshot_at < commence_at). A snapshot exactly at tip is
--                  excluded.
--   pinnacle       Pinnacle's home spread in the last pre-tip snapshot that has Pinnacle
--                  for the game.
--   cbbd           CBBD's line: Draft Kings when it has one, else the median across the
--                  other providers, leaving out `consensus` (an aggregate of the other
--                  books, with a few hundred sign-flipped rows; see docs/TABLES.md). A game
--                  whose only line is `consensus` gets no cbbd row. CBBD lines carry no timestamp, so close_snapshot_at,
--                  open_snapshot_at and minutes_before_tip are empty for this source.
-- Open values come from the FIRST snapshot that had the game (same book rules).
-- `basis` records how each close was formed.
--
-- A game gets an own_snapshots / pinnacle close only once it is "closed": some snapshot
-- file was fetched at or after its tip, so no later pre-tip snapshot can still arrive.
-- Odds events that did not match a CBBD game (game_id empty) are left out.

WITH
-- Tip time per odds event: commence_at from the most recent snapshot of that event.
events AS (
    SELECT odds_event_id,
           arg_max(commence_at, snapshot_at) AS tip_at,
           max(game_id) AS game_id
    FROM odds_snapshots
    GROUP BY odds_event_id
),
closed_events AS (
    SELECT e.*
    FROM events e
    WHERE e.game_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM snapshot_files f WHERE f.snapshot_at >= e.tip_at)
),
-- Every usable pre-tip quote, tagged with which close it can feed.
pre_tip AS (
    SELECT s.odds_event_id, s.snapshot_at, s.book, s.home_spread, e.tip_at, e.game_id,
           CASE WHEN s.book = 'pinnacle' THEN 'pinnacle' ELSE 'own_snapshots' END AS source
    FROM odds_snapshots s
    JOIN closed_events e USING (odds_event_id)
    WHERE s.snapshot_at < e.tip_at
      AND s.home_spread IS NOT NULL
      AND (s.book = 'pinnacle' OR s.book IN (SELECT book FROM us_books))
),
-- One number per (event, source, snapshot): the median across books in that snapshot.
per_snapshot AS (
    SELECT odds_event_id, game_id, tip_at, source, snapshot_at,
           median(home_spread) AS home_spread,
           count(*) AS n_books
    FROM pre_tip
    GROUP BY ALL
),
snapshot_close AS (
    SELECT game_id, source,
           arg_max(home_spread, snapshot_at) AS close_home_spread,
           max(snapshot_at) AS close_snapshot_at,
           arg_max(n_books, snapshot_at) AS n_books,
           arg_min(home_spread, snapshot_at) AS open_home_spread,
           min(snapshot_at) AS open_snapshot_at,
           any_value(tip_at) AS tip_at,
           odds_event_id
    FROM per_snapshot
    GROUP BY game_id, source, odds_event_id
),
-- If two odds events matched the same game (rare), keep the one with more books.
snapshot_rows AS (
    SELECT game_id, source, close_home_spread, close_snapshot_at,
           CAST(floor((epoch(tip_at) - epoch(close_snapshot_at)) / 60) AS INTEGER)
               AS minutes_before_tip,
           n_books, open_home_spread, open_snapshot_at,
           CASE WHEN source = 'pinnacle' THEN 'pinnacle' ELSE 'median_us_books' END AS basis
    FROM snapshot_close
    QUALIFY row_number() OVER (
        PARTITION BY game_id, source ORDER BY n_books DESC, odds_event_id
    ) = 1
),
cbbd_dk AS (
    SELECT game_id, home_spread AS close_home_spread, home_spread_open AS open_home_spread
    FROM cbbd_lines
    WHERE provider = 'Draft Kings' AND home_spread IS NOT NULL
),
cbbd_median AS (
    SELECT game_id,
           median(home_spread) AS close_home_spread,
           count(home_spread) AS n_books,
           median(home_spread_open) AS open_home_spread
    FROM cbbd_lines
    WHERE home_spread IS NOT NULL
      AND provider <> 'consensus'  -- an aggregate of the others; has sign-flipped rows
    GROUP BY game_id
),
cbbd_rows AS (
    SELECT m.game_id,
           'cbbd' AS source,
           coalesce(d.close_home_spread, m.close_home_spread) AS close_home_spread,
           CAST(NULL AS TIMESTAMPTZ) AS close_snapshot_at,
           CAST(NULL AS INTEGER) AS minutes_before_tip,
           CASE WHEN d.game_id IS NOT NULL THEN 1 ELSE m.n_books END AS n_books,
           CASE WHEN d.game_id IS NOT NULL THEN d.open_home_spread ELSE m.open_home_spread END
               AS open_home_spread,
           CAST(NULL AS TIMESTAMPTZ) AS open_snapshot_at,
           CASE WHEN d.game_id IS NOT NULL THEN 'draftkings' ELSE 'median_cbbd_providers' END
               AS basis
    FROM cbbd_median m
    LEFT JOIN cbbd_dk d USING (game_id)
)
SELECT game_id, source,
       CAST(close_home_spread AS DOUBLE) AS close_home_spread,
       close_snapshot_at, minutes_before_tip,
       CAST(n_books AS INTEGER) AS n_books,
       CAST(open_home_spread AS DOUBLE) AS open_home_spread,
       open_snapshot_at, basis
FROM snapshot_rows
UNION ALL
SELECT game_id, source,
       CAST(close_home_spread AS DOUBLE),
       close_snapshot_at, minutes_before_tip,
       CAST(n_books AS INTEGER),
       CAST(open_home_spread AS DOUBLE),
       open_snapshot_at, basis
FROM cbbd_rows
ORDER BY game_id, source
