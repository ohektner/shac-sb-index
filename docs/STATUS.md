# Status

Where the project stands right now. Update this at the end of every working session. For what was decided and why, see [DECISIONS.md](DECISIONS.md); for the plan, see [PLAN.md](PLAN.md).

**Last updated:** 2026-11-11

## Current gate

**G0, urgent.** Exit: the odds snapshot job runs on schedule several times a day, 3 days in a row. Scheduled runs started 2026-11-10, so the earliest close is about 2026-11-13. At the gate, run the check in PLAN.md and have the `auditor` review.

## Running unattended

| Job | Where | Schedule | Check it |
| --- | --- | --- | --- |
| Odds snapshot (US books + Pinnacle) | GitHub Actions | 7 a day | `data/status/odds_snapshot_log.csv` |
| Polymarket snapshot | GitHub Actions | 6 a day | `data/status/polymarket_snapshot_log.csv` |
| Polymarket history (recent games) | GitHub Actions | Daily, 10:29 UTC | Actions tab, `polymarket-history` |
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
- **Polymarket 2025-26 history backfill:** finished 2026-11-10. 21,983 calls over 10 runs, 0 errors, 21,906 price-history files. New games are picked up by the daily `polymarket-history` job.

## In progress

- **Scheduled jobs skip slots:** GitHub still skips some (on 2026-11-11 it missed the 13:17 UTC odds slot). The backup trigger started it at 13:53 UTC, once the PC woke. The backup only works while the PC is awake.

## Waiting on

| What | Waiting for | Blocks | Fallback |
| --- | --- | --- | --- |
| Task 1.2: are CBBD's lines true closes? | About a week of finished games in our own snapshots | The $30 historical-odds decision | n/a |
| Availability report access | HD Intelligence's reply (email drafted 2026-11-11 to andy@hdintelligence.com, Owen sends) | Official reports for conference games | News sources (below) |
| Task 1.11 news source | Owen's decision: Google News RSS and team sites are ruled out by their terms (VERIFIED.md); Bluesky is allowed but needs an account + app password, coverage untested; X is pay-per-read | G2 labeling set (2.1) | Official reports only |

## Next up

1. **About 2026-11-13:** check the G0 gate and run the auditor.
2. **About 2026-11-17:** task 1.2, comparing CBBD's lines with our pre-tip snapshots. Then recommend to Owen whether to buy one month of historical odds. It looks likely, because CBBD has no opening spreads before 2024-25.
3. **Task 1.7 and everything after it is model logic, for Fable.** The baseline scorecard reads `game_history`. Tell Owen; don't start it.
4. **Before the first live pricing run (task 1.10):** top up the 2026-27 Time Machine files with `--seasons 2027` (from Owen's PC), then rebuild the tables. Also in PLAN.md, task 1.10.

## Notes for the next session

- Start Claude Code in `C:\Users\omhek\shac-sb-index` so `CLAUDE.md` loads.
- **The Torvik task missed 5 a.m. on 2026-11-11.** The PC was asleep, and "run when available" didn't catch it. It was run by hand at 7:33 a.m. Owen was given the command to let the task wake the PC (`WakeToRun`, plus wake timers allowed on AC power). If its `LastRunTime` is stale, check that first.
- **Leakage tests on real data:** after `shac.tables.build`, run `SHAC_DATA_DIR=../shac-sb-index-data uv run pytest tests/test_no_leakage.py`. CI runs only the synthetic half.
- **Local folders:**
  - `C:\Users\omhek\shac-sb-index` is the main checkout.
  - `C:\Users\omhek\shac-sb-index-data` is the private repo (raw data and built tables).
  - `C:\Users\omhek\shac-sb-index-runner` is used only by the Torvik task. Don't work in it.
  - `C:\Users\omhek\shac-sb-index-wt-*` folders are task worktrees (`-tm`, `-history`, `-cbbdcur`: all merged). Safe to remove with `git worktree remove` once their branches are merged.
- Merge rules are in DECISIONS.md (2026-11-10): Claude merges docs, plumbing and data PRs when the PR's own check passes; Owen signs off on model, ledger and scorecard PRs.
- Free-tier budgets: The Odds API about 434 of 500 credits a month; CBBD 504 of 1,000 used in November.
