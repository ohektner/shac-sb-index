# Calibration and tiers: build spec (task 1.9)

Written 2026-11-12 by the lead, before any number against a line was computed. It fills the gaps PREREGISTRATION.md leaves for task 1.9. It changes nothing already committed there. The DECISIONS.md entry of 2026-11-12 makes this page part of the record.

**For the builder:** every choice is made here. If something is not covered, stop and ask Owen. Do not pick.

**Do not run any of this on real data until** task 1.2 is closed, the backtest close is recorded in DECISIONS.md ([BACKTEST_CLOSE.md](BACKTEST_CLOSE.md)) and Owen has said go. Build and test on made-up games first, as tasks 1.7 and 1.8 did.

## 1. Definitions

All per predictor (`torvik_baseline` and `margin_model`) and per game.

| Name | Formula | Notes |
| --- | --- | --- |
| `close_margin` | `−close_home_spread` | From the scorecard's `lines` view, so it is whatever close task 1.2 fixes |
| `edge` | `pred_margin − close_margin` = `pred_margin + close_home_spread` | Points. Positive = likes the home side |
| `side` | `+1` if `edge > 0`, `−1` if `edge < 0`, none if `edge = 0` | Same as `side_close` in `scored_games.sql` |
| `x` | `abs(edge)` | Points, not divided by σ, no cap |
| `ats_margin` | `home_margin + close_home_spread` | Positive = home covered; 0 = push |
| `covered` | `1` if `side × ats_margin > 0`, `0` if `< 0` | Not defined on a push |
| `cover_prob` | `1 / (1 + exp(−b × x))` | One number `b` per predictor per scored season |

**The curve has one parameter and no intercept.** At `x = 0` it gives exactly 50%. Why: with no edge the side is arbitrary, so the chance it covers is 50% by symmetry; an intercept could only add a level shift that the data at zero edge can't support. `edge / σ` would give the same probabilities (σ is one constant per fit), so points are used, as PREREGISTRATION.md says.

**`b` can come out zero or negative.** Then every `cover_prob` is 50% or lower, no game reaches Lean, and the scorecard says so. Never flip the side, never clamp `cover_prob`, never refit with a different form.

**`cover_prob` is the chance of covering given the game is not a push.**

## 2. Which games go into a fit

For scored season S the fit seasons are `training_seasons(S)` from `shac.model.margin`:

| Scored season | Fit seasons |
| --- | --- |
| 2021 (own row) | 2019, 2020 |
| 2023 | 2019, 2020, 2022 |
| 2024 | 2019, 2020, 2022, 2023 |
| 2025 | 2019, 2020, 2022, 2023, 2024 |
| 2026 | 2019, 2020, 2022, 2023, 2024, 2025 |
| 2027 (live) | 2019, 2020, 2022, 2023, 2024, 2025, 2026 |

2020-21 is never a fit season.

**Out-of-sample margins for the fit seasons** (leave one season out, within the fit seasons only):

- `margin_model`: `leave_one_season_out(features, fit_seasons, FEATURE_SET)` from `shac.model.margin`. Use its `pred_margin`.
- `torvik_baseline`: new function `baseline_leave_one_season_out(features, fit_seasons)`. For each fit season T: `H_T` = mean of `home_margin − rating_margin` over games in the other fit seasons with `home = 1`, `home_margin` and `rating_margin` not empty. Then `pred_margin = rating_margin + H_T × home` for T's games (empty if `rating_margin` or `home` is empty). `rating_margin` and `home` come from `build_features`.

**A game is in the fit only if all of these hold:** it is in a fit season; `home_margin` is not empty; `pred_margin` is not empty; `close_home_spread` is not empty; `edge ≠ 0`; `ats_margin ≠ 0`. Count what each rule drops (section 6). Neutral-site games, conference tournaments and postseason games are in, with no extra term.

**Minimum to fit:** at least 2 fit seasons with games in the fit and at least 5,000 games in the fit. If not met, that predictor and scored season get no `b`: `cover_prob` stays empty, tiers stay empty, and the fit file says `not_fit` with the counts.

## 3. The fit

Maximum likelihood with no penalty. Use this exact loop (plain numpy; no scikit-learn, whose default adds a penalty):

```
b = 0.0
repeat up to 100 times:
    p    = 1 / (1 + exp(−b × x))            # one value per game
    step = sum(x × (covered − p)) / sum(x × x × p × (1 − p))
    b    = b + step
    stop when abs(step) < 1e-10
if it did not stop: raise an error (do not return a value)
```

## 4. Applying it

- **Backtest, scored season S:** `pred_margin` is the walk-forward one already in `predictions`. `cover_prob = 1 / (1 + exp(−b_S × x))` for games with a close and `edge ≠ 0`; empty otherwise. Fill the `cover_prob` column of `predictions` before `score()` runs. `scored_games.sql` then assigns tiers; it is not changed.
- **Live 2026-27:** `b_2027` and the margin weights fit on the 2027 fit seasons are frozen in the model parameters file ([LEDGER.md](LEDGER.md), "Model parameters file") and used all season. They are not refit during the season. Live, `edge = model_margin + line` with the line in the ledger row.
- **Tier cutoffs** are compared on the unrounded number: `cover_prob >= 0.56` is Pick; `>= 0.53` and `< 0.56` is Lean; below is no call. Exactly 0.56 is a Pick and exactly 0.53 is a Lean. One Python function `tier_for(cover_prob)` is used live; a test checks it against the SQL `CASE` at 0.5299999, 0.53, 0.5599999 and 0.56.
- **Reliability groups** include their lower edge (already so in `metric_5b_reliability.sql`).

## 5. Rules for special cases

| Case | Rule |
| --- | --- |
| Push | Out of the fit, the Brier score, the reliability table and the win %. Counted in the tier's `pushes`. It still has a `cover_prob` and a tier |
| `edge = 0` | No side. Out of the fit; `cover_prob` empty; no tier; counted (`n_left_out_no_side`) |
| No qualifying close | No edge. Out of the fit; `cover_prob` empty; no tier; counted (`n_left_out_no_close`) |
| Neutral site | Treated like any game. No separate curve, row or term |
| 2020-21 | Never in a fit. Scored in its own row with `b` fit on 2019 and 2020. Never pooled |
| 2019, 2020, 2022 | Fit seasons only. They get no `cover_prob` and no row |
| A game one predictor can't price | Already left out for both by `scored` (`shared`). The fit uses each predictor's own usable games; both need the same inputs, so the two fit counts must be equal. If they differ, stop and report |

## 6. What is reported

New files in `scorecard/backtest/`, written by `shac.scorecard.run`:

**`calibration_fit.csv`**, one row per predictor per scored season (and 2027): `predictor`, `scored_season`, `fit_seasons`, `status` (`fit` or `not_fit`), `n_fit_games`, `n_left_out_no_close`, `n_left_out_no_side`, `n_left_out_push`, `fit_cover_rate` (share of `covered = 1`), `slope_b`, `edge_for_lean` (`ln(0.53/0.47) / b`), `edge_for_pick` (`ln(0.56/0.44) / b`; both empty if `b <= 0`), `sigma`.

**σ:** `sigma = sqrt(mean((home_margin − close_margin)²))` over final games with a close in the fit seasons (no mean subtracted; pushes and zero-edge games included). Reported here and stored in the parameters file. Not used for tiers.

**`calibration_check.csv`** (the isotonic check), one row per predictor per scope: `n_games`, `brier_logistic`, `brier_isotonic`, `brier_coin_flip` (0.25), `n_tier_differs`. Isotonic: `sklearn.isotonic.IsotonicRegression(increasing=True, y_min=0.0, y_max=1.0, out_of_bounds="clip")`, fit on the same `x` and `covered` as the logistic fit for that scored season, applied to the same scored games. `n_tier_differs` is how many scored games would land in a different tier under it.

**The logistic curve is the one used for 2026-27 whatever the isotonic check shows.** The check is printed, nothing more. A change of curve needs a new dated DECISIONS.md entry and applies only to games priced after it.

**Reliability table (`metric_5b_reliability.sql`), three added columns:** `n_wins`, `wilson95_low`, `wilson95_high` (z = 1.96, same formula as `metric_6_tiers.sql`). **Thin groups:** a group with fewer than 100 decided games keeps its row, `n_games`, `n_wins`, the interval and `predicted_cover_rate`, and adds `thin = true`; its `actual_cover_rate` is left empty, and the site shows "fewer than 100 games" in its place. 100 because the interval at 100 games is already about ±10 points, five times a group's width.

**Per-tier record (`metric_6_tiers.sql`), unchanged.** One call per predictor per game: the predictor's `side` against the scored line, graded against that same line. Backtest: the close. Live: the line in the scored ledger row (the last row priced before tip). Win % = wins / (wins + losses). Wilson interval on that, z = 1.96; with no decided calls the interval is empty. A tier with few calls is always shown with its interval. Rows: each scored season, `pooled` (2023 to 2026), and 2020-21 alone. Read the `pooled` row.

**Carry-over check (`calibration_carryover.csv`).** The curve is fit on edges against the close but used live on edges against an earlier line (open question in DECISIONS.md, 2026-11-11). Decision: live uses the same curve, and the carry-over is measured, not assumed.
- Backtest half, only if BACKTEST_CLOSE.md test T5 says CBBD opens are usable: for held-out games with an open, `x_open = abs(pred_margin + open_home_spread)`, `cover_prob_open = 1 / (1 + exp(−b_S × x_open))`, side and result graded against the open. Output: the six reliability groups with `n_games`, `n_wins`, predicted and actual rate, the interval and `thin`, per predictor, per season and pooled. If T5 fails, write the file with no rows and say why in SCORECARD.md.
- Live half: the live reliability table from the ledger (built in task 1.12).
- Neither half changes a tier this season. A change needs a dated DECISIONS.md entry and applies to later games only.

## 7. Code layout

| File | What goes in it |
| --- | --- |
| `src/shac/model/calibration.py` | `fit_slope(x, covered) -> float`, `cover_prob(x, b)`, `tier_for(cover_prob)`, `baseline_leave_one_season_out`, `calibrate(features, lines, predictor, scored_season) -> dict` (the `calibration_fit.csv` row) |
| `src/shac/scorecard/run.py` | Calls `calibrate` for both predictors and each scored season, fills `cover_prob`, writes the new files. `BACKTEST_CLOSE_DECIDED` is not touched by this task |
| `sql/scorecard/metric_5b_reliability.sql` | The three columns and `thin` |
| `tests/test_calibration.py` | The tests below |
| `docs/SCORECARD.md`, `docs/MODEL.md` | One short section each |

## 8. Exit check

Step 1, on made-up data (this is what the PR must pass): `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, with these tests in `tests/test_calibration.py`:

1. **Hand-worked slope.** 100 games all with `x = 2`, 60 covered: `b = ln(0.6/0.4) / 2 = 0.2027326` to 1e-6.
2. **Score equation.** On a varied made-up set, `abs(sum(x × (covered − p))) < 1e-6`.
3. **Zero information.** 50 covered of 100 at any `x`: `b = 0`, every `cover_prob = 0.5`, no tiers.
4. **Negative slope is kept.** 40 covered of 100 at `x = 2`: `b < 0`, `cover_prob < 0.5`, tier `none`, side unchanged.
5. **Left-out games.** Adding pushes, zero-edge games and games with no close to the fit seasons leaves `b` unchanged and moves the three counts.
6. **Walk-forward.** Rewriting results and lines in season S or later leaves `b_S` unchanged. Rewriting them in an earlier fit season changes it.
7. **2020-21.** Rewriting every 2020-21 result and line changes no `b` at all (2021 is in no fit). Its own row's `b` uses 2019 and 2020 only.
8. **Leave one season out.** Fit season T's margins don't change when T's own results are rewritten (both predictors).
9. **Baseline leave-one-out matches by hand** on a small set, and the two predictors' fit counts are equal.
10. **Cutoffs.** `tier_for` and the SQL agree at the four values in section 4.
11. **Thin group.** 99 games: `thin = true`, `actual_cover_rate` empty, `n_wins` present. 100 games: rate shown.
12. **Minimum to fit.** One fit season, or 4,999 games: `not_fit`, empty `cover_prob`.
13. **Refusal still stands.** `shac.scorecard.run` still prints "Not run" while `BACKTEST_CLOSE_DECIDED` is false.

Step 2, on real data, only after 1.2 is closed and Owen says go: `uv run python -m shac.scorecard.run --data-dir ../shac-sb-index-data`. Done when `calibration_fit.csv` has 12 rows (2 predictors × 6 seasons), `metric_5a`, `5b`, `6` and `calibration_check` have rows for both predictors, and in `coverage.csv` `n_with_cover_prob` equals `n_with_close` minus the zero-edge games.

**Stop and report to Owen, changing nothing, if** any `slope_b` is outside −0.05 to 0.20 (the raw normal formula is about 0.165, so more than that means a bug), any `not_fit` row appears, the two predictors' fit counts differ, or `sigma` is outside 9.5 to 12.

The `auditor` reviews the PR. It is model logic, so Owen signs off before merge.
