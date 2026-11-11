-- scored: every prediction lined up with its result and its lines (DuckDB). Task 1.7.
-- Every metric query in this folder reads this view and nothing else.
--
-- Views the runner provides:
--   predictions   predictor, game_id, pred_margin, cover_prob (empty until calibrated)
--   game_history  for the season and the final margin
--   lines         game_id, close_home_spread, open_home_spread (the runner picks the source)
--   season_roles  sql/scorecard/seasons.sql
--
-- Two views are made:
--   graded_all  one row per predictor x final game x scope, with `shared` saying whether
--               EVERY predictor has a prediction for the game.
--   scored      the shared rows only. PREREGISTRATION.md: a game one predictor can't
--               price "is left out of both the baseline's and the model's numbers", so
--               the baseline and the model are always compared on the same games.
--               coverage.sql counts what this drops.
--
-- A held-out game appears twice: once under its own season and once under 'pooled'.
-- 2020-21 appears only under its own season.
--
-- Sign conventions (ARCHITECTURE.md): margins are home margins; a line's implied home
-- margin is -home_spread; side is +1 for home, -1 for away, 0 when the prediction sits
-- exactly on the line (no side, so the game is left out of ATS and CLV and counted).
--
-- Two sides, on purpose (DECISIONS.md, 2026-11-11):
--   side_close  the side against the CLOSING line. ATS, calibration and tiers use it in
--               backtests, because the close exists for every season. So in a backtest
--               `cover_prob` must be the probability that side_close covers the close.
--   side_open   the side against the OPENING line. CLV uses it (open-to-close), and only
--               where an open exists.

CREATE OR REPLACE VIEW graded_all AS
WITH shared_games AS (
    -- Games every predictor has a prediction for.
    SELECT game_id
    FROM predictions
    WHERE pred_margin IS NOT NULL
    GROUP BY game_id
    HAVING count(DISTINCT predictor) = (SELECT count(DISTINCT predictor) FROM predictions)
),
joined AS (
    SELECT
        p.predictor,
        p.game_id,
        g.season,
        r.role,
        p.pred_margin,
        p.cover_prob,
        g.home_margin,
        l.close_home_spread,
        -l.close_home_spread AS close_margin,
        -l.open_home_spread AS open_margin,
        s.game_id IS NOT NULL AS shared
    FROM predictions AS p
    JOIN game_history AS g USING (game_id)
    JOIN season_roles AS r ON r.season = g.season AND r.role IN ('held_out', 'excluded')
    LEFT JOIN lines AS l USING (game_id)
    LEFT JOIN shared_games AS s USING (game_id)
    -- Final games with a prediction only.
    WHERE p.pred_margin IS NOT NULL AND g.home_margin IS NOT NULL
),
sided AS (
    SELECT
        *,
        CAST(sign(pred_margin - close_margin) AS INTEGER) AS side_close,
        CAST(sign(pred_margin - open_margin) AS INTEGER) AS side_open
    FROM joined
),
graded AS (
    SELECT
        *,
        -- ATS: did side_close cover the closing line? 'win', 'loss' or 'push';
        -- empty when there is no close or no side.
        CASE
            WHEN side_close IS NULL OR side_close = 0 THEN NULL
            WHEN side_close * (home_margin + close_home_spread) > 0 THEN 'win'
            WHEN side_close * (home_margin + close_home_spread) < 0 THEN 'loss'
            ELSE 'push'
        END AS ats_result,
        -- CLV in points: how far the line moved toward side_open between open and close.
        CASE
            WHEN side_open IS NULL OR side_open = 0 OR close_margin IS NULL THEN NULL
            ELSE side_open * (close_margin - open_margin)
        END AS clv,
        -- Tier from the calibrated cover probability (PREREGISTRATION.md, "Tiers").
        -- Backtests don't apply the Kalshi check or the questionable-player cap.
        CASE
            WHEN cover_prob IS NULL THEN NULL
            WHEN cover_prob >= 0.56 THEN 'pick'
            WHEN cover_prob >= 0.53 THEN 'lean'
            ELSE 'none'
        END AS tier
    FROM sided
)
SELECT *, CAST(season AS VARCHAR) AS scope FROM graded
UNION ALL
SELECT *, 'pooled' AS scope FROM graded WHERE role = 'held_out';

CREATE OR REPLACE VIEW scored AS
SELECT * FROM graded_all WHERE shared;
