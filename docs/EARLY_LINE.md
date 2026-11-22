# Calls against the early line

Written 2026-11-22. Owen decided it, and the DECISIONS.md entry of the same date makes this page part of the record. It changes the line that a Pick or Lean is made against, graded against and calibrated on, and makes CLV the headline. **It was written before any model result against an early line existed.** The only early-line numbers seen were the thin carry-over check in the 2026-11-22 calibration run (`calibration_carryover.csv`: 8 games in the model's 52–54% group).

Where this page and PREREGISTRATION.md, LEDGER.md or CALIBRATION.md disagree, this page wins. Each of those has a dated note pointing here.

## Why

- **The old rule graded each game on its last row before tip,** which is the market at its sharpest. Against that line, v1 shows no edge: the curve is nearly flat, with Lean starting at about 20 points (SCORECARD.md, 2026-11-22). CLV from that row is near zero by construction.
- **Model bettors take the early number** and are judged by whether the line moves toward them (CLV). In our own snapshots (394 games, Nov 14–21), 88% of lines first appear the day before the game, most 18 to 36 hours before tip. They move 1.2 points on average from first sight to the close: at least 1 point in 57% of games, at least 2 in 24%.
- **G2 is a news strategy:** call when the news lands, before the line moves. A call made when the game first reaches a tier fits both v1 (early numbers) and G2 (news).

## 1. Live calls (task 1.10)

1. **Runs.** Add an `evening` run at 03:47 UTC, using the 03:21 UTC snapshot. With it, the runs are `morning` (14:37), `late` (21:47) and `evening` (03:47). None makes an odds request of its own.
2. **Slate rule 2** becomes: `tip_at` more than 10 minutes after `priced_at` and before the **second** 06:00 US Eastern after `priced_at`. Each run prices today's games and tomorrow's games, as long as a US book has a line (slate rule 3).
3. **The call.** A game's call is its **first** ledger row whose `cover_prob` is in a tier (Lean or Pick). It is graded at that row's line, in that row's tier. Later rows never change it, upgrade it or cancel it. A game with no row in a tier has no call.
4. **The first priced row** is the game's earliest ledger row. Every per-game metric that isn't the tier record uses it: CLV (metric 1), distance to the close (2), error vs. results (3), ATS (4) and calibration (5).
5. **CLV** for a row uses LEDGER.md section 5, step 5, unchanged: from that row's line to our close, plus Pinnacle's close as a second row. It is left out (and counted) when the row was priced off the closing snapshot.
6. **Headline:** the model's mean CLV at the first priced row, over all games, with its 95% interval. Then comes the tier record (calls, win %, Wilson interval, gap to 52.4%, mean CLV of the calls).

## 2. Calibration (changes CALIBRATION.md's line only)

- **Edges are measured against the early line.** In backtests that's CBBD's open (`cbbd_open_home_spread`, after the line checks). Live, it's the row's own line. `covered` means the side against that line covered that line.
- **Ratings for backtest edges are those known at 18:00 US Eastern the evening before the game's Eastern date.** That's when openers usually post in our snapshots. Concretely: the latest ratings file with `as_of` before that time, both teams from that one file (the `game_history.sql` rule, with this time in place of tip). The margin model's leave-one-season-out fits and the baseline's home court use the same features.
- **Everything else in CALIBRATION.md stays:**
  - the logistic form with no intercept, and the cutoffs (Pick 56%, Lean 53%);
  - fit seasons before S, never 2020-21, with leave-one-season-out margins;
  - the minimum of 2 fit seasons and 5,000 games, and the stop checks.
- **Only 2024-25 (1,810 games) and 2025-26 have CBBD opens, about 7,400 games in all.** That's enough for the 2026-27 curve (fit on 2025 and 2026). No earlier season can be fit, so there is **no backtest tier record against the early line**: it has to be earned live, or from the historical odds archive if Owen buys it.
- **No refits during the season.** Adding 2026-27 games to the fit would need its own dated entry and would apply to later games only.
- **The close-based backtest scorecard stays published** as the second view, unchanged.

## 3. The first read: captured 2026-27 games

This is a shadow pricing of games we already have snapshots for. **It is not a live record:** no call was committed before tip. It is defined here before it is run, and its results go to Owen before anything is published.

**Games:**
- 2026-27, `final`, tipping on or after 2026-11-14 00:00 UTC (before then, "first seen" is just our first snapshot);
- with our own first-seen line and close (`closing_lines` source `own_snapshots`);
- `neutral_site` known;
- both teams in the ratings file used, and both predictors price the game.

The data is as of the run date.

**Lines and call times:**
- **Primary:** the first-seen line (`open_home_spread`). The call time is its `open_snapshot_at`.
- **Secondary:** the morning-run line, from the latest snapshot before 14:37 UTC on the tip's Eastern date (median of US books, as in `closing_lines.sql`). The call time is 14:37 UTC that day. Games with no line by then are left out of this row and counted.

**Ratings:** the latest file with `as_of` before the call time, both teams from that one file.

**Predictions:**
- **Model v1:** the `core` weights fit on `training_seasons(2027)`, as `walk_forward` does.
- **Torvik baseline:** the rating part, plus the home court fit on those seasons (`baseline_predictions.sql`).

**Measures, per predictor, for each line:**
- **CLV against our close:**
  - Report the mean, its 95% interval (mean ± 1.96 × sd / √n) and the share of games with positive CLV.
  - Games priced off the closing snapshot are left out and counted.
  - Report the same again against Pinnacle's close, where it exists.
- **ATS at that line:** wins, losses, pushes and win %.
- **Calibration and tiers:**
  - `cover_prob` comes from the section 2 curve (the 2026-27 slope).
  - Report the reliability groups, and the tier calls with their record and mean CLV.

**Reading it (fixed now):**
- **Promising:** the 95% interval for the model's mean CLV against our close (primary line) is entirely above 0.
- **No evidence either way at this size:** the interval includes 0.
- **The line moves away from v1:** the interval is entirely below 0.

Whether to buy the historical archive is Owen's call after this read.

## What does not change

- The tier cutoffs, and the logistic form.
- The close-based backtest scorecard and its metrics.
- The CBBD line checks (PR #75; DECISIONS.md 2026-11-22).
- Which close is primary (PREREGISTRATION.md: our own last pre-tip snapshot; Pinnacle's as the second row).
- The ledger's columns and append-only rules.
