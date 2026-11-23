# Backtest scorecard

How the Torvik baseline and the model are measured on past seasons. The rules are fixed in [PREREGISTRATION.md](PREREGISTRATION.md); this page says where each one lives in the code. Built in task 1.7.

**Status: run of 2026-11-22, with calibration (task 1.9) and the CBBD line checks** (results in `scorecard/backtest/`). Pooled distance to the close on the held-out seasons: margin model 1.989, Torvik baseline 2.006, so the model passes metric 2, which is the G1 test (task 1.8's exit check). The first run (2026-11-21, before the line checks) had 2.014 and 2.030.

Task 1.2 is decided (DECISIONS.md, 2026-11-21): branch C, no paid month. The backtest is scored against CBBD's line, which is not a verified close, so:

- Every run writes `lines_note.md` next to the CSVs: the note on the lines, with the current Draft Kings check numbers (`lines_note` in `shac.tables.backtest_close`). It goes on the scorecard page as written.
- Backtest CLV is not written (`BACKTEST_CLV_REPORTED = False`): no `metric_1_clv.csv`, and no CLV columns in `metric_6_tiers.csv`. CLV comes from live games only.
- Metric 2 still decides G1: both predictors face the same line.
- CBBD lines that are on the wrong team, or that the books disagree about, are dropped before scoring (DECISIONS.md 2026-11-22; TABLES.md, "Line checks"). They count as "no close": 63 of 26,116 games in the scored seasons.

## Run it

```
uv run python -m shac.scorecard.run --data-dir <private repo>
```

- Reads `tables/game_history.parquet` from the private repo. No network.
- Writes one row per game to `tables/baseline_predictions.parquet` and `tables/model_predictions.parquet` in the private repo.
- Writes summary numbers only to `scorecard/backtest/*.csv` here, one file per metric.

## The queries (`sql/scorecard/`)

| File | What it does |
| --- | --- |
| `seasons.sql` | Which seasons are training only, held out (2023 to 2026) or set aside (2020-21) |
| `baseline_predictions.sql` | The baseline formula: one predicted home margin per game |
| `baseline_fit.sql` | Per season: the home-court constant, what it was fit on, and games left out (no ratings file, a team not in the file, neutral site unknown) |
| `scored_games.sql` | Lines each prediction up with its result, close and open; works out sides, ATS result, CLV and tier; keeps only games every predictor predicts |
| `coverage.sql` | Per predictor: games predicted, games scored, games dropped because another predictor has none, and how many have a close, an open and a cover probability |
| `metric_1_clv.sql` | CLV in points, 95% interval, share positive |
| `metric_2_distance_to_close.sql` | Mean distance to the close. The G1 pass/fail measure is the `pooled` row |
| `metric_3_error_vs_results.sql` | Error against final margins, next to the close's own error |
| `metric_4_ats.sql` | Record and win % against the line; pushes separate |
| `metric_5a_brier.sql`, `metric_5b_reliability.sql` | Calibration: Brier score and the reliability table |
| `metric_6_tiers.sql` | Pick and Lean records with Wilson intervals, gap to 52.4%, mean CLV |

Every metric has a row per season (`scope` = the season) and a `pooled` row for the four held-out seasons together. 2020-21 has its own row and is never pooled. Every row carries its game count. Metrics 1 to 4 and 6 also say how many games were left out and why; for the rest, `coverage.csv` has the counts.

**Same games for everyone.** A game is scored only if every predictor has a prediction for it, so the baseline and the model are always compared on the same games.

**Pooled CLV covers fewer seasons.** CBBD has no opening lines before 2024-25, so the pooled CLV row is in practice the seasons that have them. The left-out count shows it.

## How to read the columns

- **Margins** are home margins. A line's implied home margin is minus the home spread.
- **Side** is +1 for home and −1 for away. A prediction exactly on the line has no side; the game is left out of ATS and CLV and counted.
- **Two sides.** ATS, calibration and tiers use the side against the closing line. CLV uses the side against the opening line and measures how far the line moved toward it by the close. Games with no opening line are left out of CLV only.
- **Cover probability** is the chance that the predictor's side covers the line it is scored against. In a backtest that is the side against the close, covering the close.
- **A tier's mean CLV** counts only calls whose side is the same against the open and the close. If the line crossed the prediction, that CLV belongs to the other side; those calls are counted in `n_clv_left_out_side_differs`.

## No leakage

- The home-court constant for season S is fit on seasons before S only, and never on 2020-21.
- The baseline query never reads a closing line or a score from the game it predicts.
- `tests/test_scorecard.py` checks both by rewriting later results and every line and confirming no prediction moves. It also checks each metric against answers worked out by hand on made-up games.

## The model's rows (task 1.8)

The run fits the margin model walk-forward ([MODEL.md](MODEL.md)) and hands `score` its rows next to the baseline's (`predictor`, `game_id`, `pred_margin`, `cover_prob`). The same SQL scores both, on the games both predict. One row per predictor per game; a repeat is refused.

## Calibration and tiers (task 1.9)

Built from [CALIBRATION.md](CALIBRATION.md); the code is `src/shac/model/calibration.py`. Before scoring, each predictor's `cover_prob` is filled as `1 / (1 + exp(-b × |edge|))`, with `edge` = prediction minus the close's implied margin. There is one slope `b` per predictor per scored season, fit only on earlier seasons (never 2020-21), using leave-one-season-out margins. That fills metrics 5a, 5b and 6. Three more files:

- `calibration_fit.csv`: one row per predictor per season (2021, 2023 to 2026, and the live 2027 slope). It gives the slope, the edges where Lean (53%) and Pick (56%) start, the counts left out (no close, no side, push) and `sigma`. `not_fit` means fewer than 2 fit seasons or 5,000 games.
- `calibration_check.csv`: Brier score of the logistic curve against an isotonic curve and a coin flip, plus how many games would change tier. Shown only; the logistic curve is the one used.
- `calibration_carryover.csv`: the reliability table on edges against CBBD's **open**, for held-out games that have one. It's on because test T5 passed (`CBBD_OPENS_USABLE`). It changes no tier.

The reliability table (`metric_5b_reliability`) now has `n_wins`, a 95% Wilson interval and `thin`. A group with fewer than 100 decided games keeps its counts and interval, but its `actual_cover_rate` is left empty.

**Branch C note:** the slopes are fit on edges against CBBD's line, which is an earlier line, not a close, in every season (BACKTEST_CLOSE.md). The calibration carry-over caveat in CALIBRATION.md applies to the fit too. The scorecard page says so.

**First real-data result (2026-11-22).** Every stop check in CALIBRATION.md section 8 passes. The slopes are 0.001 to 0.008, so the curve is nearly flat. Against CBBD's line, a predictor's disagreement with the line says almost nothing about who covers:
- the pooled Brier score is 0.2500 for both predictors (a coin flip is 0.25);
- 22,100 of 22,109 games land in the 50–52% group, which covered 50.3% against a predicted 50.3%;
- there was 1 Lean call per predictor in four seasons, and no Picks.

The live (2026-27) slope puts Lean at about 20 points of edge for the model (17 for Torvik) and Pick at about 41 (34), so expect almost no calls this season. That is the honest reading of a v1 model at Torvik's level against market lines. A Pick or Lean can only become common if G2 adds information the line doesn't have. Per CALIBRATION.md, the curve isn't changed because of this result.
