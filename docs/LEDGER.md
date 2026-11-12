# The ledger and the pricing jobs: build spec (task 1.10)

Written 2026-11-12 by the lead. It builds on what PR #27 shipped (`src/shac/ledger/writer.py`, `tests/test_ledger_append_only.py`) and says what is still missing. No ledger file exists yet, so the columns can still change; after the first row they can't. The DECISIONS.md entry of 2026-11-12 makes this page part of the record. The `predictions_ledger` table in ARCHITECTURE.md is replaced by section 1 when task 1.10 merges.

**For the builder:** every choice is made here. If something is not covered, stop and ask Owen.

## 1. Columns (schema v2)

`ledger/season=YYYY/ledger.csv`, one row per game per pricing. Order as listed. Key: (`game_id`, `run`, `priced_at`). New columns are marked **new**.

| # | Column | Type | Meaning |
| ---: | --- | --- | --- |
| 1 | `game_id` | int | CBBD game id |
| 2 | `run` | text | `morning` or `late` |
| 3 | `priced_at` | timestamp | Clock time when the job computed the row. Must be before `tip_at` |
| 4 | `tip_at` | timestamp | The odds feed's `commence_at` for the event in the snapshot used |
| 5 | `home_team_id` | int | |
| 6 | `away_team_id` | int | |
| 7 | `neutral_site` | `true` / `false` | **new.** The flag the model used (it decides whether home court is added) |
| 8 | `games_fetched_at` | timestamp | **new.** `fetched_at` of the CBBD game record used: the schedule as known then |
| 9 | `model_version` | text | `<version>-<first 12 hex of the SHA-256 of the parameters file>`, for example `v1-3fa9c2d1e0b4` (section 2) |
| 10 | `ratings_source` | text | **new.** `torvik_daily` or `torvik_time_machine` |
| 11 | `ratings_date` | date `YYYY-MM-DD` | **new.** `rating_date` of the file used |
| 12 | `ratings_as_of` | timestamp | **new.** `as_of` of that file. Must be before `priced_at` |
| 13 | `model_margin` | float | Predicted home margin |
| 14 | `line` | float | Home spread priced against: the median across US books in the snapshot |
| 15 | `book` | text | `consensus` |
| 16 | `n_books` | int | **new.** US books in that median |
| 17 | `line_snapshot_at` | timestamp | `fetched_at` of the odds snapshot file. Must be before `priced_at`. Its SHA-256 is in the public `odds_snapshot_log.csv` |
| 18 | `kalshi_implied_margin` | float, may be empty | Empty while Kalshi is on hold |
| 19 | `cover_prob` | float | `1 / (1 + exp(−b × abs(edge)))`, `edge = model_margin + line` ([CALIBRATION.md](CALIBRATION.md)) |
| 20 | `side` | text | `home` if `edge > 0`, `away` if `edge < 0` |
| 21 | `tier` | text | `pick`, `lean` or `none`, from `tier_for(cover_prob)`, then caps |
| 22 | `flags` | text, may be empty | Words from the list below, sorted A to Z, joined by `;` |
| 23 | `absence_adj` | float | **new (added 2026-11-12 for G2, before any row exists).** Points added to the base margin for flagged new absences ([PLAYER_IMPACT.md](PLAYER_IMPACT.md)). `model_margin` already includes it, so the base margin is `model_margin − absence_adj`. Always `0.0` until the availability adjustment is live |

Formats are unchanged from the 2026-11-11 entry: UTC `YYYY-MM-DDTHH:MM:SSZ`, floats by Python `repr`, empty cell for empty.

**Flags (the whole list; the writer refuses any other word):** `absence_adjusted` (G2; `absence_adj` is not 0), `absence_skipped` (G2; a team's latest box score was not in hand, so its absences could not be checked), `edge_over_10` (`abs(edge) >= 10`), `manual_run` (job started by hand), `no_side` (`edge` exactly 0: write `side = home`, `cover_prob = 0.5`, `tier = none`; left out of the record), `questionable_player` (G2; caps at Lean), `stale_line`, `stale_ratings`, `thin_line` (`n_books < 3`), `tip_mismatch` (feed tip and CBBD tip differ by more than 60 minutes).

**No result, no score and no closing line ever goes in the ledger.** No raw third-party number either: the line is our own median, the ratings are named by file, not copied.

## 2. Model parameters file

`models/<version>.json` in the public repo, written once by task 1.9's real run after Owen approves it, then never changed. The pricing job reads it and never refits.

```
{ "version": "v1", "created_at": "...Z", "code_commit": "<short sha>",
  "feature_set": "core", "fit_seasons": [2019, 2020, 2022, 2023, 2024, 2025, 2026],
  "n_fit_games": 0, "weights": {"rating_margin": 0.0, "home": 0.0},
  "calibration": {"slope_b": 0.0, "n_fit_games": 0, "close_source": "cbbd"},
  "sigma": 0.0 }
```

- `model_margin = weights.rating_margin × rating_margin + weights.home × home`, with `rating_margin` and `home` exactly as `sql/model/features.sql` defines them.
- G2 may add an `absence` block to a later file (PLAYER_IMPACT.md, section 4). A file without it means `absence_adj = 0.0` on every row.
- A new model or curve is a new file (`v2.json`) with a dated DECISIONS.md entry, used only for games priced after it. A test fails if a parameters file named in any ledger row no longer hashes to that row's `model_version`.
- The live pricing job cannot run for real until this file exists. Build and test with a made-up file.

## 3. The two runs

| Run | Starts (UTC) | Odds snapshot it uses |
| --- | --- | --- |
| `morning` | 14:37 | The latest one fetched before the run (normally 13:17) |
| `late` | 21:47 | The latest one fetched before the run (normally 21:17) |

Both go in the PC backup trigger. The job makes no odds request of its own (the free credits are spent). Re-check the times when US clocks change in March.

**The slate.** A run prices every game that passes all of these, and nothing else:

1. The event is in the snapshot used, and `odds_event_matches` logic gives it `match_type = matched`.
2. `tip_at` (the feed's) is more than 10 minutes after `priced_at` and before the next 06:00 US Eastern.
3. At least one US book (`US_BOOKS`) has a spread for it in that snapshot.
4. The latest CBBD record for the game (section 4) has a `neutral_site` value and a status that is not cancelled or postponed.
5. Both teams are in the ratings file used.

The `late` run writes a row for every such game, whether or not anything changed since morning. A game that tipped before the late run keeps its morning row.

**Ratings file used:** the latest file in `ratings_daily` for the season with `as_of < priced_at`, both teams from that one file (same rule as `game_history.sql`). If its `as_of` is before 06:00 US Eastern of the run's Eastern date, add `stale_ratings`. If it is more than 72 hours before `priced_at`, the run writes nothing.

**Snapshot age** at `priced_at`: up to 180 minutes, fine; over 180 and up to 360, add `stale_line`; over 360, the run writes nothing.

**Skipped games get no row.** Each run appends one row to `data/status/pricing_log.csv`: `run`, `started_at`, `status` (`ok`, `ok_no_games`, `skipped_stale_snapshot`, `skipped_stale_ratings`, `push_failed`, `error`), `line_snapshot_at`, `ratings_as_of`, `n_events_in_snapshot`, `n_priced`, then one count per skip reason (`n_unmatched_event`, `n_outside_slate`, `n_no_us_line`, `n_status_off`, `n_neutral_unknown`, `n_no_ratings`), `ledger_rows_after`, `ledger_sha256_after`.

**Order inside a run:** check out with full history; run the history check (section 6); build the rows; `append_rows`; re-read the file with `read_rows` and check it ends in a newline; commit and push to `main`. If the push is rejected, `git pull --rebase` and push again, up to 3 times. If it still fails, or any check fails, discard the change, write a `push_failed` or `error` log row and exit non-zero. Rows that were not pushed do not exist. The workflow uses `concurrency: ledger` so two pricing runs never overlap.

## 4. What changes after a row is written

Nothing in the ledger is ever edited or removed. Each case below is handled by a new row or at grading time.

| What happened | What is done |
| --- | --- |
| The line moved | Nothing special. The late run's row has the new line; the last row priced before tip is the one scored |
| A row was wrong and the game hasn't tipped | Start the run again by hand. It writes new rows (later `priced_at`, flag `manual_run`), which replace the old ones for scoring |
| A row was wrong and the game has tipped | The row stays and counts. Add a line to `ledger/season=YYYY/annotations.csv` (append-only; `game_id`, `run`, `priced_at`, `noted_at`, `kind`, `note`). The record is published two ways: all calls (the headline), and all calls minus annotated ones |
| Postponed or cancelled after pricing | No ledger change. Graded `no_action` (section 5) |
| Postponed, then played later | The new date is priced as usual when it appears in the feed. Old rows fall out by the 24-hour rule in section 5 |
| Tip time moved earlier, and a row was priced after the real tip | The row is ignored in scoring and counted (`priced_after_actual_tip`) |

There is no "void" row and no way to withdraw a call after tip.

**Game status, point in time** (the problem flagged in MODEL.md). `games` is rebuilt from raw and keeps the latest copy of each game, so it shows status, tip time and neutral site as known *now*, not as known when a game was priced. Rules:

- The pricing job reads the schedule only through a new reader, `games_as_of(T)`: for each game, the latest raw CBBD record with `fetched_at < T`. It is called with `T = priced_at`, and the record's `fetched_at` is stored in `games_fetched_at`.
- From a game record the model may use `home_team_id`, `away_team_id` and `neutral_site`, and the job may use `status` only for slate rule 4. CBBD is fetched once a day, so the odds feed is what says a game is still on (slate rule 1) and when it tips (`tip_at`).
- Any input built from other games' status or results (rest days, for one) must be computed from `games_as_of(T)` too, and can't ship until a test shows the live value equals the backtest value on 2026-27 games. None is in model v1.
- Backtests of past seasons can't see old statuses (only one copy was fetched). That is fine for v1, which uses none.

## 5. Results: a separate, rebuilt file

Results never touch the ledger. A nightly job (after the CBBD ingest) rebuilds `scorecard/live/graded_calls.csv` from scratch from the ledger, `results`, `games` and `closing_lines`. It can be deleted and rebuilt at any time and must come out the same.

For each game in the ledger:

1. `actual_tip` = `games.tip_at` as of grading.
2. The **scored row** is the last ledger row with `priced_at < actual_tip` and `line_snapshot_at < actual_tip`. Later rows are counted as `priced_after_actual_tip`.
3. If the scored row's `tip_at` is more than 24 hours from `actual_tip`, or the game is cancelled or postponed, or it has no result 7 days after `tip_at`: `outcome = no_action` with a `reason`.
4. Otherwise `ats_margin = home_margin + line` (the row's own line) and `outcome` is `win`, `loss` or `push` for the row's `side`. Rows flagged `no_side` get `no_side`.
5. `clv_points = side × ((−close) − (−line))`, with the close from `closing_lines` source `own_snapshots`, **only if `close_snapshot_at` is later than the row's `line_snapshot_at`.** When the row was priced off the closing snapshot itself, CLV would be 0 by construction; it is left out and counted (`n_clv_left_out_same_snapshot`). `pinnacle_clv_points` the same way with the `pinnacle` close.

Columns: `game_id`, `run`, `priced_at`, `is_scored_row`, `outcome`, `reason`, `close_line`, `close_snapshot_at`, `minutes_before_tip`, `clv_points`, `pinnacle_clv_points`, `annotated`. `close_line` is our own median (a derived number). No scores are published in this file.

Live metrics are the pre-registered ones, computed from scored rows, and never mixed with backtest rows.

## 6. Is the append-only test enough? No

What `test_ledger_matches_main_plus_appends` does today: it compares the checkout's ledger files with `origin/main`. On a pull request that edits, reorders or deletes a ledger row, it fails. That part works. The gaps:

| # | Gap | Fix (in task 1.10) |
| --- | --- | --- |
| 1 | **The ledger is written by direct commits to `main`, and nothing checks those.** `ci.yml` ignores pushes that touch only `ledger/**`. Even if it ran, on `main` the test compares `main` with itself. The "start of every pricing job" run is the same empty comparison. A buggy job could rewrite rows and every check would stay green | New `shac.ledger.history.verify_history()`: walk every commit that touched `ledger/` (`git log --reverse --format=%H -- ledger`), and for each file check the commit's version starts with its parent's version, byte for byte. Run it (a) at the start of every pricing run, (b) in the nightly grading job, (c) in CI as a test. All three check out full history (`fetch-depth: 0`). The pricing job stops, writing nothing, if it fails |
| 2 | Rewritten history (a force-push) can't be seen by any test | Owen turns on a branch rule for `main`: block force pushes and deletions. (No "require pull request" rule: the jobs push straight to `main`.) |
| 3 | The comparison is by `splitlines()`, so a change of line endings, or a dropped final newline, passes. Checked 2026-11-12: both pass today | Compare bytes: the new file must start with the old file's exact bytes, and the old file must end with a newline. Add both cases to the parametrized test |
| 4 | A pull request can append rows. Only the jobs should | New test, active when `GITHUB_EVENT_NAME` is `pull_request`: `git diff --name-only origin/main HEAD -- ledger` must be empty |
| 5 | `test_every_ledger_file_has_the_contract_header` reads every CSV under `ledger/` as a ledger | Apply it to files named `ledger.csv` only; give `annotations.csv` its own header check |
| 6 | The writer doesn't check that a row agrees with itself | In `format_row`, refuse a row unless: ids and `n_books` are whole numbers (write `str(int(v))`, so `401.0` can't appear); every float is finite; `side` matches the sign of `model_margin + line` (or `no_side` is flagged); `tier` equals `tier_for(cover_prob)`, or is `lean` with `cover_prob >= 0.56` and `questionable_player` flagged; `ratings_as_of < priced_at` and `line_snapshot_at < priced_at`; every flag is on the list and they are sorted; `absence_adj` is finite, and `absence_adjusted` is flagged exactly when `absence_adj` is not 0. A `cover_prob` of `None` must raise `LedgerError` (today it raises `TypeError`) |
| 7 | A crash mid-write could leave half a line | Build the whole text first and write it with one `write` call, then flush. The job verifies the file before committing (section 3), so a broken file never reaches `main` |

## 7. Build order and exit check

1. Writer: schema v2 and the checks in gap 6. Update `make_row` and the tests.
2. `verify_history`, the byte comparison, the pull-request test, `fetch-depth: 0` in `ci.yml`.
3. Parameters-file loader and its hash check.
4. `games_as_of(T)`.
5. `shac.pricing.run --run morning|late --data-dir <private repo>`: sections 2 and 3. Inputs are passed in as data frames so tests need no network.
6. Workflows `pricing.yml` (two crons, `workflow_dispatch`, `concurrency: ledger`) and the backup-trigger entries.
7. The grading job (section 5).

**Exit check.** `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, with tests that show, on made-up data:

- a commit that edits, reorders, removes or re-ends a ledger line makes `verify_history` fail (build a throwaway git repo in `tmp_path`);
- each slate rule drops the game it should and counts it under the right reason;
- a game tipping 9 minutes after `priced_at` gets no row, and one at 11 minutes does;
- the late run adds rows and leaves morning rows byte-identical;
- each writer check in gap 6 refuses its bad row and writes nothing;
- grading: the scored row is the last one before the real tip; a postponed game is `no_action`; a call priced off the closing snapshot has no CLV; rebuilding the graded file twice gives identical bytes;
- `model_version` matches the parameters file's hash.

Then, on a branch, Owen checks that a pull request editing one ledger row turns CI red (the PLAN.md exit check). The G1 gate after that: morning and late rows on 7 straight days with no manual commits.

Before the first live run: task 1.9's parameters file exists; the 2026-27 Time Machine files are topped up (PLAN.md, task 1.10); Owen has set the branch rule in gap 2. The `auditor` reviews the PR. It is the ledger, so Owen signs off before merge.
