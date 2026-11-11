# Backtest scorecard

How the Torvik baseline and the model are measured on past seasons. The rules are fixed in [PREREGISTRATION.md](PREREGISTRATION.md); this page says where each one lives in the code. Built in task 1.7.

**Status: not yet run on real data.** The first run waits for task 1.2, which decides what the backtest closing line is (see DECISIONS.md, 2026-11-11). Until then the command below prints "Not run" and stops. To switch it on, record the decision in DECISIONS.md and set `BACKTEST_CLOSE_DECIDED = True` in `src/shac/scorecard/run.py` in the same PR.

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
- **Calibration and tier files are empty for the baseline** until task 1.9 gives it a cover probability.

## No leakage

- The home-court constant for season S is fit on seasons before S only, and never on 2020-21.
- The baseline query never reads a closing line or a score from the game it predicts.
- `tests/test_scorecard.py` checks both by rewriting later results and every line and confirming no prediction moves. It also checks each metric against answers worked out by hand on made-up games.

## The model's rows (task 1.8)

The run fits the margin model walk-forward ([MODEL.md](MODEL.md)) and hands `score` its rows next to the baseline's (`predictor`, `game_id`, `pred_margin`, `cover_prob`). The same SQL scores both, on the games both predict. One row per predictor per game; a repeat is refused.
