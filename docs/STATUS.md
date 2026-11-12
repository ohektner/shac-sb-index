# Status

Where the project stands right now. Update this at the end of every working session. For what was decided and why, see [DECISIONS.md](DECISIONS.md); for the plan, see [PLAN.md](PLAN.md).

**Last updated:** 2026-11-12

## Current gate

**G0, urgent.** Exit: the odds snapshot job runs on schedule several times a day, 3 days in a row. Scheduled runs started 2026-11-10, so the earliest close is about 2026-11-13. At the gate, run the check in PLAN.md and have the `auditor` review.

## Running unattended

| Job | Where | Schedule | Check it |
| --- | --- | --- | --- |
| Odds snapshot (US books + Pinnacle) | GitHub Actions | 7 a day | `data/status/odds_snapshot_log.csv` |
| Polymarket snapshot | GitHub Actions | 6 a day | `data/status/polymarket_snapshot_log.csv` |
| Polymarket history (recent games) | GitHub Actions | Daily, 10:29 UTC | Actions tab, `polymarket-history` |
| News collection (Google News) | GitHub Actions | Twice a day, 14:53 and 21:53 UTC | `data/status/news_log.csv` |
| News collection (team sites) | GitHub Actions | Daily, 20:13 UTC | `data/status/news_sidearm_log.csv` |
| Snapshot watch (alarm) | GitHub Actions | Twice a day | Fails and emails Owen if odds snapshots stop |
| Schedule backup (starts skipped jobs) | Owen's PC, Windows task | Every 15 minutes | `%LOCALAPPDATA%\shac-sb-index\schedule-backup.log` |
| Torvik daily snapshot | Owen's PC, Windows task | 5:00 AM Phoenix | `data/status/torvik_snapshot_log.csv`, and `%LOCALAPPDATA%\shac-sb-index\torvik-task.log` |

## Done

- **G0 build:** odds and Torvik snapshot jobs, status logs, watch alarm, CI (PRs #1–#2, #7).
- **Task 1.1:** CBBD raw backfill, 8 seasons, 496 calls (PRs #3, #5).
- **Task 1.5:** team-name map, 369 teams, 0 unmatched (PR #6). Extended in PR #18 to read the Time Machine files: 11 old Torvik names (for example "North Carolina St.", "IUPUI") mapped with season ranges, plus 9 new Odds API names from this season's snapshots.
- **Clean tables** in the private repo: games, results, player_box, cbbd_lines, odds_snapshots, closing_lines (PR #10), ratings_daily (PR #18), game_history (PR #19). Rebuild with `uv run python -m shac.tables.build --data-dir <private repo>`.
- **Polymarket collection** (PR #8), the G3 second market, with written permission.
- **Task 1.3, Torvik Time Machine backfill:** finished 2026-11-10 with Bart Torvik's OK. 1,185 daily ratings files (the day before each season's first game through its last, 2018-19 to 2025-26) plus 8 `super_sked` files, 0 blocks, 0 missing days. Re-running makes 0 requests. Manifest at `torvik/timemachine/manifest.csv` in the private repo (PRs #14, #15).
- **Task 1.4, Time Machine columns and timing** (PR #18, merged 2026-11-11). All 45 columns named; for two seasons they match the season-end CSV exactly (`docs/TORVIK.md`). The file dated D already counts D's games, late West Coast and Hawaii games included, so it is known at 06:00 Eastern on D+1. A game uses the latest file known before its tip. Rule 3 was tightened: the old Eastern-date reading would have leaked 52 late-night games. One early count remains in 90,423 team-games (an 11 a.m. game against a non-D-I team).
- **Task 1.6, `game_history` and the no-leakage tests** (PR #19, merged 2026-11-11 with Owen's OK). The `auditor` found no blockers; its three fixes are in (daily pulls never mixed, a real-data record-clock test, `status` moved to labels).
- **2026-27 CBBD games, daily** (PR #21, merged 2026-11-11). New `current` mode: settled windows once, plus a live `games`/`lines` window re-fetched daily at 10:07 UTC (also in the PC backup trigger). First run: 3 calls, 1,044 games from Nov 1 to Nov 20. Odds events now match 96% (100 of 104; 3 `swapped`, 1 `no_game`), and `closing_lines` has its first 55 `own_snapshots` and 55 `pinnacle` rows.
- **2026-27 Time Machine files** (PR #23, 2026-11-11): Oct 31 to Nov 10 fetched, so every 2026-27 game so far has ratings. Our daily 5 a.m. pulls match the Time Machine file for the day before to rounding error (`docs/TORVIK.md`). Re-run `uv run python -m shac.ingest.torvik_timemachine --data-dir ../shac-sb-index-data --seasons 2027` before going live to add the days since.
- **Task 1.7, baseline scorecard: built, not yet run on real data** (2026-11-11). The Torvik baseline and all six pre-registered metrics are DuckDB SQL in `sql/scorecard/`, with a runner (`shac.scorecard.run`) and tests on made-up games with hand-worked answers. Seven DECISIONS.md entries settle the gaps in the pre-registration; Owen approved the three that were choices. How to read it: `docs/SCORECARD.md`.
- **Task 1.8, margin model: built and fit; the comparison with the baseline waits for 1.2** (PR #30, merged 2026-11-11 with Owen's OK). Model v1 is the baseline's two parts with learned weights (slope about 1.01, home court about 3.4 points). Four extra inputs were tried on the train-only seasons against final scores; none passed the keep rule. So v1 sits very close to the Torvik baseline. Details: `docs/MODEL.md`.
- **Polymarket 2025-26 history backfill:** finished 2026-11-10. 21,983 calls over 10 runs, 0 errors, 21,906 price-history files. New games are picked up by the daily `polymarket-history` job.

## Design session, 2026-11-12 (the lead's last before Fable access ends 2026-11-16)

Specs written so Opus and Sonnet can build without making choices. Docs only; no real-data scorecard run, no number against a line. Owen approved all of them on 2026-11-12. #38 merged; #39 to #41 were stacked, and deleting their base branches closed #39 and #41, so items 2, 3 and 5 merged together in [#43](https://github.com/ohektner/shac-sb-index/pull/43).

| Work item | PR | What it is | Status |
| --- | --- | --- | --- |
| 1. Task 1.9 calibration gaps | [#38](https://github.com/ohektner/shac-sb-index/pull/38) | `docs/CALIBRATION.md` + DECISIONS entry | Merged (#38) |
| 2. Task 1.2 test and decision tree | [#39](https://github.com/ohektner/shac-sb-index/pull/39) | `docs/BACKTEST_CLOSE.md` + DECISIONS entry, committed before 1.2 has results | Merged (#43) |
| 3. Task 1.10 ledger and pricing spec | [#40](https://github.com/ohektner/shac-sb-index/pull/40) | `docs/LEDGER.md` + DECISIONS entry | Merged (#43) |
| 4. Leakage and soundness review | [#42](https://github.com/ohektner/shac-sb-index/pull/42) | `docs/REVIEW_2026-11-12.md`: 16 findings, nothing fixed | Merged with this update |
| 5a. Continuity candidate, design only | [#41](https://github.com/ohektner/shac-sb-index/pull/41) | `docs/CONTINUITY.md` + DECISIONS entry | Merged (#43); on hold |
| 5b. G2 design (extraction schema, labeling-guide check, task 2.5 method) | [#44](https://github.com/ohektner/shac-sb-index/pull/44) | `docs/EXTRACTION.md`, `docs/LABELING_GUIDE.md` (rewritten), `docs/PLAYER_IMPACT.md`, one new ledger column in `docs/LEDGER.md`, task 2.0 in PLAN.md | Merged (#44) |
| 5c. G3 design (line-movement model and its held-out test) | [#45](https://github.com/ohektner/shac-sb-index/pull/45) | `docs/LINE_MOVEMENT.md` | Merged (#45) |
| 6. Decisions and status for 5b and 5c | [#46](https://github.com/ohektner/shac-sb-index/pull/46) | Five DECISIONS.md entries and this page | Merged (#46) |

**G2 and G3 design (second session, 2026-11-12).** Docs only again; nothing was run and no number was compared with a line. Owen approved all of it on 2026-11-12 (#44, #45, #46). The points that matter for the builders:

- **The first labeling guide was not enough.** The first two days of collected headlines are mostly other sports, other leagues, previews and old stories. The rewrite shows the model and Owen a numbered list of each team's players, lets only a listed player be flagged, and fixes a stratified pick of 240 items (40 for writing the prompt, 200 for the score).
- **Reading accuracy is judged per item:** the model's set of (player, status) must equal the label's.
- **Task 2.5 adjusts for new absences only,** with one learned number (points per full-time player) and sit rates measured from our own flags. `questionable` adjusts nothing until 50 cases exist.
- **One ledger column was added** (`absence_adj`), because the columns can't change after the first row. Task 1.10 builds 23 columns, not 22.
- **News runs must move earlier** when extraction is built (13:07 and 20:17 UTC): today they start a few minutes after each pricing run would.
- **G3's held-out test** freezes its coefficients on or after 2027-02-01 and scores only games that tip after that.

**The `main` ruleset `protect-main` is active as of 2026-11-12** (checked through the GitHub API: enforcement `active`). It closes gap 2 in `docs/LEDGER.md`, section 6 (block force pushes and deletions on `main`). Scheduled jobs can still push straight to `main`.

Main review result: no leak in the backtest path. The one High finding is that the ledger's append-only test doesn't cover direct commits to `main`, which is how the ledger is written; the fix is in `docs/LEDGER.md`, section 6.

## In progress

- **Task 1.11, news collection** (PR #33, merged 2026-11-11). Google News is the primary source by Owen's decision (DECISIONS.md): twice a day, one search per team playing in the next 36 hours, headlines only, private repo. A 3-team test run on GitHub Actions worked (18 items), so it runs there; the first full run is the 14:53 UTC slot. Watch `data/status/news_log.csv` for `blocked` rows. Secondary source added (PR for task 1.11 step 4): team-site feeds for 312 of 365 teams (`data/team_sites.csv`, found 2026-11-11 via Wikidata + robots.txt checks), read daily at 20:13 UTC. 53 teams to review in `data/team_sites_review.csv` (46 with no Wikidata site; 7 with no usable feed, including Little Rock, whose robots.txt blocks feeds). Re-run `shac.news.team_sites` about monthly.

- **Scheduled jobs skip slots:** GitHub still skips some (on 2026-11-11 it missed the 13:17 UTC odds slot). The backup trigger started it at 13:53 UTC, once the PC woke. The backup only works while the PC is awake.

## Waiting on

| What | Waiting for | Blocks | Fallback |
| --- | --- | --- | --- |
| Task 1.2: are CBBD's lines true closes? | About a week of finished games in our own snapshots | The $30 historical-odds decision | n/a |
| Availability report access | HD Intelligence's reply (email drafted 2026-11-11 to andy@hdintelligence.com, Owen sends) | Official reports for conference games | Google News headlines (task 1.11, running) |

## Next up

1. **About 2026-11-13:** check the G0 gate and run the auditor.
2. **About 2026-11-17:** task 1.2, comparing CBBD's lines with our pre-tip snapshots. **Run exactly the tests in `docs/BACKTEST_CLOSE.md` (PR #43) and read the branch off its rules;** if there are too few games, wait. Then recommend to Owen whether to buy one month of historical odds. It looks likely, because CBBD has no opening spreads before 2024-25. The $30 would not make the model more accurate (lines are never inputs); it would firm up the yardstick, add opening lines for CLV in more seasons, and feed the G3 line-movement study.
3. **After 1.2: fix the backtest close in DECISIONS.md, then run the scorecard on real data for the first time** (`uv run python -m shac.scorecard.run --data-dir ../shac-sb-index-data`). It now scores the margin model next to the baseline, which is task 1.8's exit check. Don't run it before; no number against a line has been seen, on purpose.
4. **Task 1.9 (calibration and tiers) is next.** Its model logic is now fully specified in `docs/CALIBRATION.md` (PR #38, approved), so Opus can build it step by step on made-up data; anything the spec doesn't cover goes to Owen, not to the builder's judgment. It needs lines, so it also waits for 1.2. `leave_one_season_out` in `shac.model.margin` already gives it the out-of-sample edges.
5. **Task 1.10 (ledger and pricing jobs) follows `docs/LEDGER.md` (PR #43).** It also needs task 1.9's parameters file. The ruleset on `main` that blocks force pushes and deletions is in place (`protect-main`, active 2026-11-12). If #44 is merged, the ledger has 23 columns (`absence_adj` is the last). The fixes from `docs/REVIEW_2026-11-12.md` are assigned to tasks there.
6. **G2 and G3 are specified, not built** (#44, #45, approved 2026-11-12). task 2.0 (daily box scores) is small and can be built any time; tasks 2.1 to 2.5 follow `docs/EXTRACTION.md`, `docs/LABELING_GUIDE.md` and `docs/PLAYER_IMPACT.md` once G1 is closed and about 30 days of news exist; G3 follows `docs/LINE_MOVEMENT.md` after task 1.10, and its coefficients are frozen on or after 2027-02-01. Builders make no choices: anything a spec doesn't cover goes to Owen.
7. **Before the first live pricing run (task 1.10):** top up the 2026-27 Time Machine files with `--seasons 2027` (from Owen's PC), then rebuild the tables. Also in PLAN.md, task 1.10.

## Notes for the next session

- Start Claude Code in `C:\Users\omhek\shac-sb-index` so `CLAUDE.md` loads.
- **The Torvik task missed 5 a.m. on 2026-11-11.** The PC was asleep, and "run when available" didn't catch it. It was run by hand at 7:33 a.m. Owen was given the command to let the task wake the PC (`WakeToRun`, plus wake timers allowed on AC power). If its `LastRunTime` is stale, check that first.
- **Leakage tests on real data:** after `shac.tables.build`, run `SHAC_DATA_DIR=../shac-sb-index-data uv run pytest tests/test_no_leakage.py`. CI runs only the synthetic half.
- **Local folders:**
  - `C:\Users\omhek\shac-sb-index` is the main checkout.
  - `C:\Users\omhek\shac-sb-index-data` is the private repo (raw data and built tables).
  - `C:\Users\omhek\shac-sb-index-runner` is used only by the Torvik task. Don't work in it.
  - `C:\Users\omhek\shac-sb-index-wt-*` folders are task worktrees (`-baseline` is task 1.7, merged; `-margin` is task 1.8). Safe to remove with `git worktree remove` once their branches are merged.
- Merge rules are in DECISIONS.md (2026-11-10): Claude merges docs, plumbing and data PRs when the PR's own check passes; Owen signs off on model, ledger and scorecard PRs.
- Free-tier budgets: The Odds API about 434 of 500 credits a month; CBBD 504 of 1,000 used in November.
