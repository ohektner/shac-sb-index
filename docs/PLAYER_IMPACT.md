# Player impact: adjusting the margin for new absences (task 2.5)

Written 2026-11-12 by the lead. Nothing here has been run, and no number has been compared with a line. This page fixes the method, so nothing is chosen after seeing a result. The DECISIONS.md entry of 2026-11-12 makes it part of the record. It depends on [EXTRACTION.md](EXTRACTION.md) (flags, `status_for`, box scores every day).

**For the builder:** every choice is made here. If something is not covered, stop and ask Owen. Do not pick.

## The idea

Torvik's ratings know a player has been out once the team has played without him. They can't know about a player who played the last game and won't play the next. That is the one place the news can add something, so the adjustment covers **new absences only**: rotation players who played their team's previous game.

```
model_margin = base_margin + absence_adj
absence_adj  = −v × (S_home − S_away)
```

- `base_margin` is the margin model's number (`docs/MODEL.md`), unchanged.
- `S_team` is the team's expected missing playing time among new absences, in units of one full-time player (40 minutes a game).
- `v` is how many points of margin one full-time player's absence costs. One number, learned from past box scores and final scores.

It has three parts, fixed in turn below: the weights (`w`), the value (`v`) and the status probabilities (`p`).

## 1. Weights: how much of a player is missing

For a team's game `g`, from `rotation_players` (earlier games only):

- A **candidate** is a row with `is_rotation = true` and `games_sat_in_a_row_before = 0`.
- `w = min(mpg_before / 40, 1.0)`.

Players outside the top 8, and players already sitting, have weight 0 by definition. A team's first game of the season has no rotation rows, so no adjustment.

## 2. The value `v`, from history

This step uses box scores and final scores only. No flag and no line is involved, so it may be run on the training seasons before task 1.2 closes (DECISIONS.md, 2026-11-11, "error against final scores").

**Truth load.** For team `t` in a past game `g`: `S_true = sum of w over candidates with played = false`.

**Games in the fit.** Fit seasons are `training_seasons(2027)` (2019, 2020, 2022 to 2026). A game is in if: it is final with a `home_margin`; it has a leave-one-season-out `pred_margin` (`leave_one_season_out`, feature set `core`, the same margins calibration uses); and both teams have a box score for it. A team in its first game of the season has `S_true = 0`. Count what each rule drops.

**The fit.** With `r = home_margin − pred_margin` and `D = S_true_home − S_true_away`, over the games in the fit:

```
v    = − sum(D × r) / sum(D × D)
e    = r + v × D                      (per game, only games with D ≠ 0)
s2   = sum(e × e) / (n − 1)           (n = games with D ≠ 0)
se_v = sqrt(s2 / sum(D × D))
```

No intercept: with no absence difference there is nothing to adjust. Games with `D = 0` add nothing to either sum.

**Keep rule, written before the first run** (the same shape as the input rule in MODEL.md): the adjustment is kept only if `v / se_v >= 2.0` on all fit seasons together **and** `v > 0` when fit on each of 2019, 2020 and 2022 alone. If it fails, `v` is recorded in MODEL.md's table, the adjustment is not built further, and task 2.5 reports that plainly. One run. The 40-minute scale, top 8 and "played the previous game" are fixed here and not tuned; a variant needs a new dated entry before it is run.

**Stop and report to Owen, changing nothing, if** `v` is outside 0 to 12, or fewer than 2,000 games have `D ≠ 0`.

**Caveats to print with the result.** The fit uses every new absence, known in advance or not; live, only flagged ones are adjusted, and news favors well-known players. A player who sits is sometimes hurt in the game before, when his team may already have played worse; the ratings then hold a little of the effect, so `v` runs slightly low. Both push toward a smaller adjustment, the safe direction.

## 3. Status probabilities `p`

How likely a candidate with a given status is to sit, **beyond** a candidate with no news at all (who still sits now and then; the fit already carries that on both teams).

Source: `scorecard/availability/status_sit_rates.csv` (EXTRACTION.md, section 7), restricted to games that tipped before the freeze time `F` (below). With `rate(s) = n_sat / n` for status `s`:

```
p(s) = max(0, rate(s) − rate(none))     for s in out, doubtful, questionable, and only if n(s) >= 50
p(s) = 0                                 for probable, available, no status, or n(s) < 50
```

- The table is computed once, at `F`, stored in the parameters file and not updated during the season.
- If `n(out) < 50` at `F`, task 2.5 waits. (The precision bar needs 100 `out` pairs anyway.)
- Nothing is assumed about `questionable`: until 50 such cases exist it adjusts nothing, and the Lean cap (section 5) is what handles it.

## 4. The live number

At pricing time `T = priced_at`, for each team in a game on the slate:

1. **Is the rotation current?** Find the team's latest game that tipped before `T` (through `games_as_of(T)`, not cancelled or postponed). If that game's box is not in hand at `T` (EXTRACTION.md, section 1), the team's candidates are unknown: `S_team = 0`, and the row gets the flag `absence_skipped`.
2. Otherwise build the rotation rows for this game with the `rotation_players` SQL over the boxes in hand at `T`, and take the candidates and their `w`.
3. `S_team = sum over candidates of p(status_for(candidate, game, T)) × w`.

Then `absence_adj = −v × (S_home − S_away)`. No cap and no rounding. If `absence_adj ≠ 0` the row gets the flag `absence_adjusted`. The edge, side, cover probability and tier are then computed from `model_margin` exactly as before; the calibration slope is not refit.

| Case | Rule |
| --- | --- |
| Flagged player is not in the top 8 | Weight 0. No adjustment |
| Flagged player already sat the previous game | Not a candidate. No adjustment (the ratings have started to see it) |
| A player returning from a long absence | No adjustment in this version. Counted in the report |
| Several players out on one team | Their terms add. No cap; `edge_over_10` already marks extreme edges |
| Status `probable` or `available` | `p = 0` |
| Two flags disagree | `status_for` decides (latest wins; a tie is `questionable`) |
| A flag arrives after the row is priced | The next run's row has it (the late run re-prices every game). The last row before tip is the one scored |
| Team's first game, or no box in hand | `S_team = 0`; `absence_skipped` only in the no-box case |
| Neutral site, postseason | No special treatment |

**Ledger.** `docs/LEDGER.md` gains one column, `absence_adj` (column 23), and two flag words, in the same PR as this page, because the ledger's columns can't change after its first row. Until G2 is live the column is `0.0`. The parameters file gains an `absence` block: `{"v": ..., "se_v": ..., "n_fit_games": ..., "p": {"out": ..., "doubtful": ..., "questionable": ...}, "p_frozen_at": "...Z", "n_by_status": {...}}`. A model with this block is a new parameters file (`v2.json`) with a dated DECISIONS.md entry, used only for games priced after it.

## 5. The questionable cap

PREREGISTRATION.md: "a game with a rotation player (top 8 by minutes) listed as questionable is capped at Lean. This starts when the availability layer goes live in G2." Made exact:

- At `T = priced_at`, for either team, if any row with `is_rotation = true` (candidate or not) has `status_for(...)` equal to `questionable` **or `doubtful`**, the row gets the flag `questionable_player`, and a `pick` becomes `lean`. Nothing else changes.
- If a team's rotation is unknown (`absence_skipped`), the cap can't be checked for that team and is not applied.
- `doubtful` is included because it is the stronger form of the same uncertainty; capping one and not the other would make no sense. This widens the pre-registered wording slightly (fewer Picks, never more), so it needs Owen's sign-off, and it is recorded before the layer is live.

**"Live" means:** the first pricing run after a dated DECISIONS.md entry records that reading accuracy and precision met their bars (EXTRACTION.md, section 7) and Owen approved. Before that, flags are collected and evaluated but touch no ledger row. The cap goes live with that entry. The margin adjustment goes live only with its own later entry (section 7).

## 6. The exit check: does it help on flagged games?

This compares with closing lines, so it runs only after the first real scorecard run and after live pricing has started. PLAN.md's check is "error vs. the close on flagged games, with and without the adjustment".

**Freeze.** `F` is the moment `v` and the `p` table are written to a file and committed (`models/absence_v1.json`). Games that tipped before `F` are *retrospective* (their sit rates fed `p`); games after are *prospective*. `v` never saw 2026-27 at all.

**Flagged games.** 2026-27 games with a scored ledger row (LEDGER.md, section 5) for which the replayed `absence_adj`, computed as in section 4 with `T` = that row's `priced_at`, is not 0. For each:

- `base` = the row's `model_margin − absence_adj` as stored (so it is the base margin whether or not the adjustment was live).
- `adjusted` = `base` + the replayed `absence_adj`.
- `close_margin` = minus the `own_snapshots` close. Games with no close are left out and counted.

**Reported,** for prospective, retrospective and both together, and again for closes taken 60 minutes or less before tip:

| Number | Formula |
| --- | --- |
| `n_games`, `n_dates` | |
| `dist_base`, `dist_adjusted` | mean of `abs(base − close_margin)`, mean of `abs(adjusted − close_margin)` |
| `gain` | mean of `d`, where `d = abs(base − close_margin) − abs(adjusted − close_margin)`. Positive = the adjustment moved us toward the close |
| `gain_se` | Clustered by US Eastern game date: with `n` games, `G` dates, `D_g` = the sum of `d` on date `g` and `n_g` its game count, `gain_se = sqrt( G / (G − 1) × sum over dates of (D_g − n_g × gain)² ) / n` |
| `err_base`, `err_adjusted` | mean of `abs(base − home_margin)` and `abs(adjusted − home_margin)`, final games. Noisy; shown, not judged |
| `share_flagged` | flagged games / all games with a scored row |
| `n_returns_ignored`, `n_skipped_no_box` | From section 4 |

Files: `scorecard/availability/impact.csv`. The G2 gate needs this measured and reported, whatever it shows.

## 7. Going live

**Recommend the adjustment to Owen for live use only if all hold:** the keep rule on `v` passed; the precision bar is met; at least 100 prospective flagged games; `gain > 0` on the prospective games; and `gain / gain_se >= 2.0` on both periods together. Otherwise it stays measured and reported, the README says so, and pricing is unchanged. Either way it is Owen's decision, recorded in a dated DECISIONS.md entry, and applies only to games priced after it.

## 8. Code layout and build check

| File | What goes in it |
| --- | --- |
| `src/shac/model/absence.py` | `truth_load`, `fit_value(features, rotation, fit_seasons) -> dict`, `status_probs(sit_rates) -> dict`, `live_load(...)`, `absence_adj(...)` |
| `src/shac/model/run.py` | Prints `v`, `se_v`, the per-season values and the keep-rule result |
| `src/shac/pricing/run.py` | Section 4 and 5, behind one switch that is off until the DECISIONS.md entry |
| `src/shac/scorecard/impact.py`, `sql/scorecard/impact.sql` | Section 6 |
| `tests/test_absence.py` | The tests below |

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, with these tests on made-up data:

1. **Hand-worked `v`.** Four games with `D` = 1, 1, −1, 0.5 and `r` = −4, −2, 3, −1.5: `v = 9.75 / 3.25 = 3.0`.
2. **Weights.** `mpg_before` 30 gives 0.75; 44 gives 1.0; rank 9 gives no weight; a player who sat the previous game is not a candidate.
3. **No leak in `v`.** Rewriting any 2026-27 result or box leaves `v` unchanged. Rewriting a fit season's results changes it.
4. **Leave one season out.** A fit season's `r` doesn't change when that season's own results are rewritten.
5. **Probabilities.** `rate(out) = 0.90`, `rate(none) = 0.03`, `n = 60` gives 0.87; `n = 49` gives 0; `probable` gives 0; a rate below `rate(none)` gives 0.
6. **Sign.** Home team missing a full-time player with `p = 1` and `v = 3`: `absence_adj = −3.0`. The away team the same: `+3.0`. Both: `0.0` and no `absence_adjusted` flag.
7. **Stale box.** The team's last game has no box in hand at `T`: `S_team = 0`, flag `absence_skipped`, and the cap is not applied for that team.
8. **Cap.** A `questionable` or `doubtful` rotation player turns `pick` into `lean` and sets the flag; `out` does not; a non-rotation player does not.
9. **Switch off.** With the switch off, every row has `absence_adj = 0.0` and no G2 flag, whatever the flags file holds.
10. **Clustered error.** A hand-worked example with 3 dates.
11. **Replay equals live.** For a made-up game, section 6's replayed `absence_adj` equals the value the pricing code wrote.

The `auditor` reviews the PR. It is model logic, so Owen signs off before merge.
