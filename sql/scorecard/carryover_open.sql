-- Carry-over check, backtest half (docs/CALIBRATION.md, section 6). Task 1.9.
--
-- The calibration curve is fit on edges against the close but used live on edges against
-- an earlier line. This measures how the same curve does on edges against CBBD's OPEN,
-- for held-out games that have one. It changes no tier.
--
-- Replaces the `scored` view with open-graded rows, so metric_5b_reliability.sql can be
-- run on it unchanged:
--   x_open          = abs(pred_margin + open_home_spread) = abs(pred_margin - open_margin)
--   cover_prob      = 1 / (1 + exp(-b_S * x_open))   (b_S: that predictor and season's slope)
--   ats_result      side and result graded against the open; no side at zero edge
--
-- Views expected: graded_all (sql/scorecard/scored_games.sql) and
--   slopes(predictor, season, slope_b)   one row per fit predictor and scored season.
-- Run only when BACKTEST_CLOSE.md test T5 says CBBD opens are usable.

CREATE OR REPLACE VIEW scored AS
WITH open_graded AS (
    SELECT
        g.predictor,
        g.scope,
        g.game_id,
        g.pred_margin - g.open_margin AS edge_open,
        g.home_margin - g.open_margin AS ats_margin_open,
        s.slope_b
    FROM graded_all AS g
    JOIN slopes AS s ON s.predictor = g.predictor AND s.season = g.season
    WHERE g.shared AND g.role = 'held_out' AND g.open_margin IS NOT NULL
      AND s.slope_b IS NOT NULL
)
SELECT
    predictor,
    scope,
    game_id,
    CASE WHEN edge_open <> 0 THEN 1 / (1 + exp(-slope_b * abs(edge_open))) END AS cover_prob,
    CASE
        WHEN edge_open = 0 THEN NULL
        WHEN sign(edge_open) * ats_margin_open > 0 THEN 'win'
        WHEN sign(edge_open) * ats_margin_open < 0 THEN 'loss'
        ELSE 'push'
    END AS ats_result
FROM open_graded;
