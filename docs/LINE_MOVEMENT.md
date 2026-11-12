# The line-movement model and its held-out test (G3)

Written 2026-11-12 by the lead. Nothing here has been run, and no number has been compared with a line. This page fixes the model, the split and the test before any of the data exists, so nothing is chosen after seeing a result. The DECISIONS.md entry of 2026-11-12 makes it part of the record.

**For the builder:** every choice is made here. If something is not covered, stop and ask Owen. Do not pick.

G3 is research. **It changes no call, no tier and no model input.** A line is still never an input to the margin model (DECISIONS.md, 2026-11-11). Kalshi stays on hold. Polymarket is not in this version (see "Left out").

## The questions

PLAN.md asks two things of our own snapshots:

1. **Does our number predict the move?** After we price a game, does the line move toward our margin by the close?
2. **Does the open-to-close move predict anything?** Is the close a better forecast of the result than the open, and once the line has moved, is there anything left in the move?

A third is added because the data is already collected: **does the sharp book lead?** When Pinnacle and the US books disagree, do the US books move toward Pinnacle? It gives question 1 a fair comparison: our number against the best public signal.

## Part 1: the model (questions 1 and 3)

### One row per game

From the live ledger (`ledger/season=2027/ledger.csv`), `closing_lines`, the flattened `odds_snapshots` and the graded-calls file (LEDGER.md, section 5). For each game, the **anchor row** is its ledger row with the earliest `priced_at`.

| Name | Formula | Meaning |
| --- | --- | --- |
| `t0` | anchor `line_snapshot_at` | The snapshot we priced from |
| `line0` | anchor `line` | US-book median home spread at `t0` |
| `edge0` | anchor `model_margin + line0` | Our edge at `t0`. Positive = we like the home side more than the market |
| `pin0` | Pinnacle's `home_spread` for the event in the snapshot fetched at `t0` | |
| `pin_gap0` | `line0 − pin0` | Positive = Pinnacle likes the home side more than the US median |
| `close` | `close_home_spread`, source `own_snapshots` | |
| `move` | `line0 − close` | How far the market's home margin rose from `t0` to the close. `line0 = −6.5`, `close = −8.0` gives `+1.5` |
| `game_date` | US Eastern date of `tip_at` | For clustering |

Everything on the right-hand side of a model is known at `t0`, which is before `priced_at`, which is before tip and is public in the ledger's commit. The close comes later.

**A game is usable only if all hold** (count what each rule drops):

1. It has a ledger row, and the anchor's `flags` contain none of `stale_line`, `thin_line`, `tip_mismatch`.
2. It was played: its graded outcome is not `no_action`.
3. It has an `own_snapshots` close with `close_snapshot_at > t0`, `minutes_before_tip <= 60` and `n_books >= 3`. (A stale close is not a close; review finding 5.)
4. For models B and C only: Pinnacle has a spread for the event in the `t0` snapshot.

Set `U_A` = games passing 1 to 3. Set `U_BC` = games passing 1 to 4.

### Three models, no intercept

```
A:  move = bA × edge0                          fit on U_A
B:  move = bB × pin_gap0                       fit on U_BC
C:  move = c1 × edge0 + c2 × pin_gap0          fit on U_BC
```

Plain least squares. A and B: `b = sum(x × move) / sum(x × x)`. C: `numpy.linalg.lstsq` on the two columns. No intercept, no other inputs, no weights, no trimming of outliers.

Why no intercept: swap which team is called home and `move`, `edge0` and `pin_gap0` all flip sign, so the only honest constant is zero. The mean of `move` is reported on its own line as a check on that.

The benchmark is **model 0: `move = 0`**, the open is the best guess of the close. A model has to beat that.

### The split

- **Freeze.** The coefficients are fit once and written to `models/line_move_v1.json`: `version`, `created_at`, `code_commit`, `fit_from`, `fit_through` (the earliest and latest tip in the fit), `n_fit_A`, `n_fit_BC`, `bA`, `bB`, `c1`, `c2`, and each one's standard error (below). The file is committed the day it is made and never changed.
- **When.** The first run on or after **2027-02-01** at which `U_BC` holds at least 800 games that have tipped. If there are fewer, wait and try again weekly.
- **Fit games:** usable games with `tip_at < created_at`.
- **Held-out games:** usable games with `tip_at > created_at`, through the last game of the 2026-27 season. No coefficient ever sees them.
- The evaluation code refuses to run if the file is missing, or if any game it is about to score tipped at or before `created_at`.

The split is by time, not random, because games on one night share news and line moves, and because a forward test is what a reader would trust. Its cost: the fit is mostly non-conference play and the test mostly conference play and tournaments. Say so with the result.

### What is reported on the held-out games

For each model `m`, with `pred` its predicted move for a game:

| Number | Formula |
| --- | --- |
| `n_games`, `n_dates` | |
| `mse_zero`, `mse_model` | `mean(move²)`, `mean((move − pred)²)` |
| `skill` | `1 − mse_model / mse_zero` |
| `gain` | `mean(d)`, with `d = move² − (move − pred)²` per game |
| `gain_se` | Clustered by `game_date`: with `n` games, `G` dates, `D_g` = the sum of `d` on date `g` and `n_g` its game count, `gain_se = sqrt( G / (G − 1) × sum over dates of (D_g − n_g × gain)² ) / n` |
| `slope_check` | `sum(pred × move) / sum(pred × pred)`. 1 means the predicted moves are the right size; below 1, too big |
| `direction` | Among games with `abs(pred) >= 0.25` and `move ≠ 0`: the share with the same sign, its count and a 95% Wilson interval (z = 1.96) |

Rows: A on `U_A`; A, B and C on `U_BC` (so the three are compared on the same games); and C against B, with `d = (move − pred_B)² − (move − pred_C)²`.

Also printed, no verdict: the mean and standard deviation of `move`; the share of games with `move = 0`; the same table split by hours from `t0` to tip (under 3, 3 to 8, over 8); and by `model_version` if more than one appears in the anchors.

**Verdicts, fixed now.** Each is read once, after the season's last game. Runs before then are marked `interim` and decide nothing.

| Statement | True when, on the held-out games |
| --- | --- |
| "Our number predicts the move" | Model A on `U_A`: `gain > 0` and `gain / gain_se >= 2.0` |
| "US books follow Pinnacle" | Model B: the same |
| "Our number adds something beyond Pinnacle" | C against B: the same |

If the held-out set has fewer than 600 usable games (300 for a `U_BC` row), the row is printed with `too_few_games` and no verdict. A failed verdict is reported as plainly as a passed one.

**Standard errors of the coefficients** (in the freeze file, for the write-up only): for A and B, with residual `u = move − b × x` on the fit games, `se_b = sqrt( G / (G − 1) × sum over dates of (sum on the date of x × u)² ) / sum(x × x)`. For C, the same idea in matrix form: `(X'X)⁻¹ × M × (X'X)⁻¹ × G / (G − 1)`, where `M` is the sum over dates of `s_g s_g'` and `s_g` is the date's sum of `x_i × u_i` (a 2-vector); the errors are the square roots of the diagonal.

**Stop and report to Owen, changing nothing, if** any coefficient is outside −1.5 to 1.5, or fewer than 40% of games with a ledger row are in `U_A`.

## Part 2: does the move predict anything? (question 2)

No coefficients are frozen here, because nothing is fit before the test: these are two fixed measurements. They use no ledger row and no number of ours.

**Open.** The current `open_home_spread` is the first snapshot's median even when one book had posted (review finding 7). G3 adds three columns to `closing_lines` for `own_snapshots`: `open_n_books`, and `open3_home_spread` / `open3_snapshot_at`, the US median in the **first snapshot in which at least 3 US books have a spread for the game.**

**Games:** 2026-27 games with a final score, an `own_snapshots` close meeting usable rule 3 (with `open3_snapshot_at` in place of `t0`), and `open3_snapshot_at` at least 3 hours before `close_snapshot_at`.

| Name | Formula |
| --- | --- |
| `move_oc` | `open3_home_spread − close` |
| `e_open` | `home_margin + open3_home_spread` (result against the open, home side) |
| `e_close` | `home_margin + close` |

**Measurement 1, is the close better than the open?** `d = abs(e_open) − abs(e_close)` per game; report `mean(abs(e_open))`, `mean(abs(e_close))`, `gain = mean(d)` and `gain_se` clustered by date as above. "The close beats the open" when `gain > 0` and `gain / gain_se >= 2.0`. Also reported on movers only (`abs(move_oc) >= 1.0`).

**Measurement 2, is anything left in the move?** `g = sum(move_oc × e_close) / sum(move_oc × move_oc)`, with a clustered standard error (the A/B formula, with `x = move_oc` and `u = e_close − g × move_oc`). Read it as:

- `g` within 2 standard errors of 0: the close has taken the move in fully. This is the expected answer.
- `g` above 0 by 2 standard errors: moves don't go far enough (the result lands further in the move's direction).
- `g` below 0 by 2 standard errors: moves overshoot.

All 2026-27 games are used, read once after the season's last game. Final scores are noisy (a spread of about 10 points a game against moves of about 1), so say in the write-up how wide the interval on `g` is before saying what it shows.

**A second, clearly labeled row** from CBBD's 2025-26 lines (Draft Kings `spreadOpen` and `spread`), using the same two formulas, only if `docs/BACKTEST_CLOSE.md` found Draft Kings to be CLOSE on T2 and T5 passed. It is one book with no timestamps; it is context, not the result.

**One more line, no verdict:** on the held-out games of part 1, `sum(pred_C × e0) / sum(pred_C × pred_C)` with `e0 = home_margin + line0` and its clustered error. It asks whether a predicted move says anything about the result against the line we priced. Expect a wide interval.

## Left out of this version

| What | Why |
| --- | --- |
| Polymarket's price as an input | Its spread markets need the price-to-margin conversion (ARCHITECTURE.md, written for Kalshi) and a thin-market filter, neither tested yet. It would be model D, with its own dated entry before it is fit, judged by the same "adds beyond" test against C |
| Kalshi | On hold (DECISIONS.md) |
| Time to tip, book disagreement, day of week as inputs | One change at a time. Version 1 answers the two plain questions first |
| Earlier seasons | No snapshots exist before 2026-11-10. If a month of historical odds is bought (BACKTEST_CLOSE.md), a replay on those seasons with walk-forward margins is possible, as a new dated entry after the first real scorecard run |
| Using any of this for calls | Out of scope for the season. It would be a change to PREREGISTRATION.md |

## No leakage

- Inputs come from the `t0` snapshot and the anchor row only. A test rewrites every later snapshot and every result and checks that no input and no fitted coefficient moves.
- The fit reads only games that tipped before `created_at`. A test rewrites every later game and checks that the freeze file's numbers don't change.
- The held-out evaluation reads the coefficients from the file. It has no code path that fits.
- `move` is never an input to anything outside this page.

## Code layout

| File | What goes in it |
| --- | --- |
| `sql/tables/closing_lines.sql` | `open_n_books`, `open3_home_spread`, `open3_snapshot_at` |
| `sql/market/line_move_games.sql` | The one-row-per-game table of part 1, with a `usable_A`, `usable_BC` and drop-reason column (DuckDB) |
| `sql/market/move_vs_result.sql` | Part 2's games |
| `src/shac/market/line_move.py` | `fit_models(games, created_at) -> dict`, `freeze(...)`, `evaluate(games, frozen) -> rows`, `clustered_se(...)` |
| `src/shac/market/run.py` | `uv run python -m shac.market.run --data-dir <private repo> --step fit` or `--step evaluate` |
| `scorecard/market/` | `line_move_fit.csv`, `line_move_heldout.csv`, `move_vs_result.csv`, `line_move_coverage.csv` (the drop counts). Derived numbers only |
| `tests/test_line_move.py` | The tests below |

## Exit check

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, with these tests on made-up data:

1. **Signs.** `line0 = −6.5`, `close = −8.0`: `move = 1.5`. `pin0 = −7.5`: `pin_gap0 = 1.0`. `model_margin = 9.0`: `edge0 = 2.5`.
2. **Hand-worked coefficient.** `edge0` = 1, 2, −1 and `move` = 0.5, 1, −0.5: `bA = 0.5`, `skill = 1`, `gain = mean(move²) = 0.5`.
3. **Model 0.** With `pred = 0` everywhere, `gain = 0` and `skill = 0`.
4. **Clustered error.** A hand-worked case with 3 dates; and the check that one date holding all games gives no error value (`G − 1 = 0`) and the row says `too_few_dates`.
5. **Usable rules.** Each of the four drops the game it should and counts it under the right reason; a close taken 61 minutes before tip is dropped and one at 60 is kept; a close from the `t0` snapshot itself is dropped.
6. **Anchor.** With a morning and a late row, the morning row is the anchor.
7. **The split.** A game tipping one second after `created_at` is held out; one second before is in the fit; evaluation refuses a missing file and refuses a fit game.
8. **No leak** (the two rewrites in "No leakage").
9. **C against B.** When `edge0` is 0 in every game, C's predictions equal B's and the C-against-B `gain` is exactly 0; when `move = 0.3 × edge0 + 0.5 × pin_gap0` exactly, C's `skill` is 1.
10. **Part 2.** `move_oc`, `e_open`, `e_close` by hand on 4 games; `g` by hand; the 3-book open picks the second snapshot when the first has 2 books.
11. **Too few.** 599 held-out games prints `too_few_games` and no verdict.

On real data, in order: `--step fit` once the freeze rule is met (writes and commits the file), then `--step evaluate` after the season's last game. Done when `line_move_heldout.csv` has its five rows and three verdicts, `move_vs_result.csv` has both measurements, and the write-up for the G4 case study quotes them with their intervals.

The `auditor` reviews the PR. It reads the ledger and the closes, so Owen signs off before merge. Build it after task 1.10 (it needs ledger rows and the graded file); nothing in it can run for real before then.
