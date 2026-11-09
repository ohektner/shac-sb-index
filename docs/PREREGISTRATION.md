# Pre-registration

Committed on 2026-11-09, before any backtest was run and before any historical data was pulled. It fixes how the model will be judged, so results can't shape the yardstick.

Changes are allowed only through a dated entry in DECISIONS.md, made before the affected results are looked at, and they apply only going forward. The git history of this file is the proof.

## Seasons

- History: 2018-19 through 2025-26 (seasons 2019 to 2026).
- **Walk-forward only.** To test season S, the model and the calibration are fit on seasons before S. Games are never shuffled across time.
- **Held-out test seasons:** 2023, 2024, 2025 and 2026, each predicted by a model trained on the seasons before it.
- **2020-21 (season 2021) is excluded** from training and from the headline numbers, because home-court advantage collapsed with no fans. Its results are reported in their own row.
- Live results from 2026-27 are reported separately from backtest results and never mixed with them.

## Scorecard metrics

Every metric is reported for the model and for the Torvik baseline, per season and pooled.

1. **CLV in points.** For each priced game, take the side the model implied at pricing. CLV is how far the line moved toward that side between pricing and the close: `side × (close_implied_margin − priced_implied_margin)`, where side is +1 for home and −1 for away, and an implied margin is `−home_spread`. Positive is good. Reported as the mean with a 95% confidence interval and the share of games with positive CLV. Backtest seasons use open-to-close where an opening line exists.
2. **Distance to the close.** Mean absolute difference between the predicted margin and the closing line's implied margin. This is the primary accuracy measure, and the one used for "matches or beats Torvik" at G1: the model passes if its pooled distance on the held-out seasons is no larger than the baseline's.
3. **Error vs. results.** Mean absolute error of the predicted margin against the final margin, shown next to the closing line's own error. Reported, but game results are noisy enough that small differences mean little.
4. **ATS at −110.** Record and win % of the model's side against the line at pricing. Pushes are counted separately and left out of the win %. Reported, not trusted.
5. **Calibration.** Brier score of `cover_prob`, and a reliability table: games grouped by predicted cover probability (50-52%, 52-54%, 54-56%, 56-58%, 58-60%, 60%+), with the predicted and actual cover rate and the count in each group.
6. **Per-tier record.** For Pick and for Lean: number of calls, win %, a 95% Wilson confidence interval, the gap to the 52.4% break-even and mean CLV. The interval is always shown, so a small sample reads as small.

If no tier beats 52.4% in the backtest, the scorecard says so plainly. Picks are not forced.

## Confidence method

- **Cover probability is calibrated,** not the raw Φ(edge / σ), which overstates confidence.
- **Method:** logistic regression of "the model's side covered" on the size of the edge in points, with no intercept shift by season. It is fit on out-of-sample predictions from the training seasons only: each training season's edges come from a model that did not see that season.
- Isotonic regression is reported next to it as a check. The logistic curve is the one used for tiers unless a DECISIONS.md entry changes that before the per-tier results are seen.
- Pushes are left out of the fit.
- **σ:** starts at 10.3 points (the spread of final margins around the closing line in a published 83,000-game study). It is re-fit on training seasons only. σ is used for the Kalshi conversion and for reporting, not for tiers.

## Tiers

| Tier | Calibrated cover probability |
| --- | --- |
| Pick | 56% or higher |
| Lean | 53% up to, not including, 56% |
| No call | Below 53% |

- **Kalshi check:** a Pick must also beat the Kalshi price plus fees when a matching Kalshi line exists. Kalshi data is on hold pending written permission, so this check is inactive until a DECISIONS.md entry turns it on. Backtests don't apply it.
- **Questionable-player cap:** a game with a rotation player (top 8 by minutes) listed as questionable is capped at Lean. This starts when the availability layer goes live in G2. Backtests don't apply it.
- **Which run is scored:** the last ledger row for the game with `priced_at` before tip-off. Earlier rows stay on record.
- The line a call is scored against is the line recorded in that ledger row.

## AI availability layer bars

| Measure | Bar | How it's checked |
| --- | --- | --- |
| Reading accuracy: did the model correctly read what the source said? | 95% or higher | About 200 hand-labeled items |
| Precision: when it says "out," did the player sit? | 95% for rotation players | Box score minutes after the game |
| Recall: of rotation players who sat, how many were flagged before tip? | Reported, no bar | Box score minutes |

A rotation player is one of a team's top 8 by minutes per game over the season to date. "Sat" means no minutes in the box score.

## What this file does not fix

The model's features and form (linear, ridge, boosted trees) are free to change during G1, one change at a time. Whatever the model is, it is judged by the rules above.
