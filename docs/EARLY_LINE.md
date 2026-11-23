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
- **If the historical archive passes its checks (section 4), the 2026-27 curve is fit on its night-before lines instead.**
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

## 4. The historical archive (bought 2026-11-23)

Owen is buying one month of The Odds API's $30 tier (DECISIONS.md 2026-11-23). Everything in this section is fixed **before any archive data is pulled.**

### Pull plan

- **Seasons:** 2025-26, 2024-25, 2023-24 and 2022-23, newest first.
- **Game days:** US Eastern dates with at least one final D-I game in `games`.
- **Three requests per game day D,** at US Eastern clock times (converted to UTC for that date, so the times hold through the clock change):

  | Kind | Time (US Eastern) | Used as |
  | --- | --- | --- |
  | `evening` | 22:20 on D−1 | The call line (about when the live `evening` run prices) |
  | `afternoon` | 13:55 on D | Close for afternoon tips |
  | `late` | 18:55 on D | Close for evening tips (just before the 19:00 cluster) |

- **The request:** `GET /v4/historical/sports/basketball_ncaab/odds`, regions `us`, markets `spreads`, American odds, ISO dates.
  - It returns the archive's snapshot at or before the requested time and costs 10 credits.
  - Pinnacle (`eu`) is not pulled, because it doubles the cost.
- **Budget:** about 1,760 calls (17,600 credits).
  - The puller stops when fewer than 1,000 credits would remain, which keeps the live snapshot job running.
  - Newest seasons go first, so a shortfall drops the oldest.
- **Storage:** raw files go in the private repo under `odds/history/`, wrapped like live snapshot files, with `fetched_at` set to the archive's snapshot time, plus a manifest of every request.

### The backtest

- **Lines and matching:** each snapshot's line is the median of the `US_BOOKS` spreads, as for our live close. Events are matched to games exactly as live snapshots are: team names via `team_aliases`, and rows with home and away reversed get no game.
- **The call line** is the game's `evening` snapshot line. A game with no line there gets no call, and those games are counted.
  - This is simpler than live, which also has the `late` and `morning` runs (the `late` run often prices earlier).
  - So each backtest game gets one call time, and its CLV, if anything, understates live.
- **Tip** follows the `closing_lines.sql` rule: the feed's latest commence time, capped at CBBD's scheduled tip when that is real.
- **Close:** the latest pulled snapshot strictly before tip, with `minutes_before_tip` recorded. If the call snapshot is the only one before tip, CLV is left out and counted.
- **Ratings:** the latest file with `as_of` before the call snapshot's time.
- **Predictions** are walk-forward, as in the scorecard: season S uses the model and the baseline home court fit on `training_seasons(S)`, applied to the call-time ratings.
- **The curve** follows CALIBRATION.md, with edges against the call line and leave-one-season-out margins on call-time ratings.
  - Only archive seasons have call lines, so the scored seasons are 2024-25 (fit on 2022-23 and 2023-24) and 2025-26 (fit on 2022-23 to 2024-25).
  - The 2026-27 curve is fit on all four seasons.

**Measures, per predictor, per season and pooled:**
- **CLV, all games, pooled 2022-23 to 2025-26** (CLV needs no curve): the mean, its 95% interval, the share positive, and the spread of `minutes_before_tip`.
- **ATS** at the call line.
- **Controls:**
  - "always the underdog", "always home", and a coin flip (seed 0);
  - the paired difference, model CLV minus always-underdog CLV, with its 95% interval.
- **Splits:**
  - November–December vs. January–April;
  - books in the call line: 1–2, 3–5 or 6+.
- **Calibration and tiers** (2024-25, 2025-26, and the two pooled):
  - the reliability groups;
  - the tier record: calls, W-L-P, win %, Wilson 95% interval, gap to 52.4%, and mean CLV.

### Checks before any result is computed

Stop and report if either check fails.

1. **Coverage:** in each season, at least 90% of final D-I games have a matched call line. If not, fix team-name aliases (a mechanical fix, listed in the PR) and check again. If it is still below 90%, report it.
   - *Outcome, 2026-11-23:* it failed in 2022-23 (82.0%) and 2024-25 (89.7%) because of lines posted only on game day, not name matching.
   - Owen's calls (DECISIONS.md 2026-11-23): proceed as written, and use the archive curve live as if the check had passed.
2. **Agreement with CBBD:** for games with both lines, compare the archive close with CBBD's line (after the line checks). The median absolute difference must be 0.5 point or less, and opposite favorites (both lines 3+ points) must be 1% of games or fewer.

### Reading it (fixed now)

- **"v1 beats early lines":** the pooled model CLV interval is above 0, **and** the paired model-minus-underdog interval is above 0.
- **"Only the drift":** the model CLV interval is above 0, but the paired interval includes 0 or is below it.
- **"No":** the model CLV interval includes 0 or is below it.
- **Tiers** are reported with their intervals. If no tier beats 52.4% in the pooled 2024-25 and 2025-26 seasons, the scorecard says so plainly. Live calls go on under section 1 either way.

### The live curve

If checks 1 and 2 pass, the 2026-27 curve is the archive fit (2022-23 to 2025-26). It replaces section 2's CBBD-open fit **whatever the backtest shows.** If the checks fail, section 2 stands.

**Not changed:** task 1.2's decision. The close-based backtest scorecard keeps CBBD's line as its yardstick (DECISIONS.md 2026-11-21). The archive is a separate early-line backtest.

## What does not change

- The tier cutoffs, and the logistic form.
- The close-based backtest scorecard and its metrics.
- The CBBD line checks (PR #75; DECISIONS.md 2026-11-22).
- Which close is primary (PREREGISTRATION.md: our own last pre-tip snapshot; Pinnacle's as the second row).
- The ledger's columns and append-only rules.
