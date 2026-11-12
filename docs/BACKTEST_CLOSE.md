# The backtest close: test and decision tree (task 1.2)

Written 2026-11-12 by the lead, **before task 1.2 has any result.** It fixes the test task 1.2 runs, the thresholds, and what follows from each outcome, so the yardstick can't be chosen after seeing how anything scores against it. The DECISIONS.md entry of 2026-11-12 makes this page part of the record.

**For whoever runs 1.2:** run the tests below exactly, fill in the results table, and read the branch off the rules. Nothing here uses a model or Torvik prediction, and 1.2 must not compute one. If a result fits no rule, stop and ask Owen.

## What we know and what we don't

- CBBD's `spread` is the home spread (sign confirmed on all eight seasons, `docs/TABLES.md`). It has no timestamp.
- Our own snapshots (7 a day, per book, with fetch times) started 2026-11-10. They cover 2026-27 only.
- So a direct test is possible only for the providers CBBD has **this season** that are also in our snapshots: Draft Kings (`draftkings`) and Bovada (`bovada`). ESPN BET is not in our feed.
- Earlier seasons use other providers: ESPN BET (2023 to 2025), and the aggregators teamrankings, numberfire and consensus (2019 to 2023). They can only be reached by links between providers in seasons where two overlap.
- The Odds API's paid history starts 2020-11-16, so no purchase can ever cover 2018-19 or 2019-20.

**Two kinds of season, two standards:**

| Seasons | Role | What their line must be |
| --- | --- | --- |
| 2023, 2024, 2025, 2026 | Held out: the yardstick | A verified close, or labeled as not verified |
| 2019, 2020, 2022 | Fit only (the calibration slope learns from them; they get no scorecard row) | A pre-game line with the right sign. Their timing can't be tested and is caveated once |
| 2021 | Own row | Same as fit-only, with the caveat on its row |

## The tests

Inputs: `cbbd_lines`, `odds_snapshots`, `games`, `results` (rebuild the tables first), and the raw daily `lines` fetches in `cbbd/raw/lines/`. Write the queries in `sql/checks/backtest_close.sql` and the outputs (counts and shares only) in `docs/VERIFIED.md`.

**Tested games (T1 to T4), per provider P in {Draft Kings, Bovada}:** 2026-27 games that are final, have a CBBD `spread` from P, and have the same book in our snapshots with:
- `own_close` = that book's `home_spread` in our last snapshot before tip, taken 60 minutes or less before tip;
- `own_open` = that book's `home_spread` in our first snapshot with the game, taken at least 6 hours before `own_close`.

**Movers** are tested games with `abs(own_close − own_open) >= 1.0`.

| Test | What is measured | Pass |
| --- | --- | --- |
| **T1 sign** | Among tested games with `abs(own_close) >= 2`: share where CBBD `spread` and `own_close` have the same sign | ≥ 99% |
| **T2 close or open** | Among movers: `near_close` = share with `abs(spread − own_close) < abs(spread − own_open)`; `near_open` = share with the reverse; the rest are ties | **CLOSE** if `near_close ≥ 80%` and `near_open ≤ 10%`. **OPEN** if `near_open ≥ 80%`. Otherwise **MIXED** |
| **T3 exactness** | Among all tested games: share with `abs(spread − own_close) <= 0.5` | ≥ 80% (needed for CLOSE) |
| **T4 timing** | (a) share of tested games with `abs(spread − own_close) >= 3.0`; (b) among games with two or more CBBD fetches made after the game was final, share whose `spread` differs between them | (a) ≤ 1% and (b) ≤ 1% |
| **T5 opens** | Among tested games with a CBBD `spreadOpen`: share with `abs(spreadOpen − own_open) <= 1.0` | ≥ 70% → CBBD opens are usable for backtest CLV |

**Sample size.** T2 needs at least 40 movers for the provider; T1, T3, T4 need at least 150 tested games. Below that the result is **WAIT**: collect more days and re-run. Do not decide early and do not lower a threshold.

**Links to earlier seasons** (no snapshots exist, so each link leans on the one before):

| Link | Seasons it covers | Test | Pass |
| --- | --- | --- | --- |
| **L1** | 2026 (Draft Kings, Bovada) | Season 2026 lines from P with both `spread` and `spreadOpen` and `abs(spread − spreadOpen) >= 1`, final games. `d = abs(−spreadOpen − home_margin) − abs(−spread − home_margin)` per game | P is CLOSE on T2, and mean `d` > 0 with `mean / (sd / sqrt(n)) >= 2.0` (the later number predicts results better) |
| **L2** | ESPN BET | Season 2026 games with ESPN BET and Draft Kings, where Draft Kings moved (`abs(spread − spreadOpen) >= 1`). Share where ESPN BET's `spread` is nearer Draft Kings' `spread` than its `spreadOpen`, and the reverse | L1 passed for Draft Kings, at least 40 such games, nearer `spread` ≥ 80%, nearer `spreadOpen` ≤ 10% |
| **L3** | 2025 and 2024 (ESPN BET only) | The L1 test on ESPN BET's own 2025 lines that have `spreadOpen` | L2 passed, and `mean / (sd / sqrt(n)) >= 2.0` |
| **L4** | 2023 (ESPN BET and teamrankings; consensus is already left out) | Season 2023 games with both: share with `abs(ESPN BET spread − teamrankings spread) <= 0.5` | L3 passed, and ≥ 85% |

These line-vs-result numbers involve no prediction of ours, so they are allowed before the scorecard runs.

**A held-out season is "verified" when:** 2026: T1, T3, T4 pass and T2 is CLOSE for Draft Kings, and L1 passes for Draft Kings. 2025 and 2024: 2026 is verified and L2 and L3 pass. 2023: 2025 is verified and L4 passes.

**Bovada alone failing** (Draft Kings CLOSE, Bovada OPEN or MIXED, or Bovada fails T4): drop Bovada from the `cbbd` close in `sql/tables/closing_lines.sql`; 2026 games with no other provider get no close and are counted. Then continue with the branch Draft Kings gives.

## The branches

| Branch | When | The backtest close | The scorecard says | The $30 month |
| --- | --- | --- | --- | --- |
| **A. True close** | All four held-out seasons verified | CBBD's line as built today (`closing_lines`, source `cbbd`) | "Closing lines are CBBD's. Checked directly against our own snapshots for 2026-27 and by provider links for earlier seasons. 2018-19 to 2021-22 lines come from aggregators and their timing could not be checked; they are used only to fit the calibration slope (and for 2020-21's separate row)." | **Recommend: don't buy now.** G1 doesn't need it. Missing opens are already handled (left out of CLV and counted). Revisit at G3, when the line-movement study wants more history; the archive isn't going away |
| **B. Close for some seasons** | Draft Kings passes T1 to T4 with CLOSE this season, but one or more held-out seasons are not verified (a link failed) | CBBD's line for every held-out season. Unverified seasons are labeled | Each unverified season's rows carry "line timing not verified". The `pooled` row stays all four held-out seasons (PREREGISTRATION.md) and still decides G1. A second row, `pooled_verified`, pools the verified seasons only. If the two give different answers on "matches or beats Torvik", the scorecard page says so in one sentence | **Recommend: buy one month ($30), to check the unverified seasons** (plan below). If CBBD then passes in a season, it becomes verified and CBBD stays its close. If it fails, the bought closes become that season's yardstick, and games without one are left out and counted |
| **C. Not a close** | Draft Kings is OPEN or MIXED on T2, or fails T3 | Not CBBD's line. Held-out seasons need bought closes. The scorecard does not run until Owen decides | If Owen buys: "Closing lines for [seasons] are The Odds API's archived snapshots; [seasons] are scored against CBBD's line, which is an earlier line, not a close." If Owen doesn't: every table is headed "against CBBD's line, an earlier line, not a close", metric 2 still decides G1 (both predictors face the same line), and backtest CLV is not reported | **Recommend: buy.** Run the call counter first (below). If the four held-out seasons need 1,900 calls or fewer, the $30 month covers them. If more, give Owen two options: $30 for 2026 and 2025 only, or one $59 month for all four |
| **D. Sign or timing fault** | T1 or T4 fails | Fix first, then re-run everything. T1: if one provider's sign is flipped consistently (≥ 99% opposite), flip it in the `cbbd_lines` build and record it; if it is inconsistent, drop that provider. T4: drop that provider (its numbers include in-game or edited lines). If the provider is Draft Kings, go to branch C | Says which provider was fixed or dropped, and how many games lost their close | Follows the branch reached after the fix |
| **WAIT** | Sample size not reached | Nothing is decided | n/a | n/a |

**Backtest CLV** (all branches): reported from CBBD opens only if T5 passes. If T5 fails or can't be run, backtest CLV is left empty with the reason, and CLV comes from live games only. Bought snapshots, if any, supply opens for the seasons they cover (first snapshot with the game).

**Calibration fit seasons** (all branches): 2019, 2020 and 2022 use CBBD's line as it is. In branch C that line is known to be earlier than a close, and `docs/CALIBRATION.md`'s carry-over note applies to the fit too; say so on the scorecard page.

## If a month is bought: the pull plan

Check The Odds API's historical docs again before paying (endpoint, cost per call, snapshot spacing); the numbers here are from 2026-11-09.

- **Endpoint:** historical odds, `basketball_ncaab`, `regions=us`, `markets=spreads`, one `date` per call. One call returns every listed game at that moment and costs 10 credits.
- **Which moments:** for each game day in the seasons to pull, group that day's games by tip time (from `games.tip_at`). Take a snapshot 5 minutes before each of the busiest tip times until 80% of the day's games are covered, at most 8 a day. Add one snapshot at 15:00 UTC that day (an early line, for CLV).
- **Call counter (free, run before buying):** a script that applies the rule above to `games` and prints calls per season. It makes no request.
- **Order:** newest unverified season first. Stop at 1,900 calls (19,000 credits), leaving a reserve.
- **Storage:** raw responses in the private repo under `odds/historical/`, with `fetched_at` and the requested `date`, never overwritten. Same flattening as `odds_snapshots`. A new `closing_lines` source, `odds_api_history`: median of US books in the last bought snapshot before tip, only if it is 60 minutes or less before tip.
- **First use is as a check:** run T2 and T3 with the bought per-book lines in place of our own snapshots, for each provider and season they overlap. Then apply branch B's rule.

## After the branch is known

1. Record the results table and the branch in `docs/VERIFIED.md`, and add a dated DECISIONS.md entry naming the branch. That entry needs Owen's sign-off (scorecard).
2. Give Owen the recommendation on the paid month. He decides.
3. Only then set `BACKTEST_CLOSE_DECIDED = True`, in the same PR as that entry, and run the scorecard.
