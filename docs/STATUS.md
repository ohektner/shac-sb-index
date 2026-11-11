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
- **Clean tables** in the private repo: games, results, player_box, cbbd_lines, odds_snapshots, closing_lines (PR #10), ratings_daily (PR #18). Rebuild with `uv run python -m shac.tables.build --data-dir <private repo>`.
- **Polymarket collection** (PR #8), the G3 second market, with written permission.
- **Task 1.3, Torvik Time Machine backfill:** finished 2026-11-10 with Bart Torvik's OK. 1,185 daily ratings files (the day before each season's first game through its last, 2018-19 to 2025-26) plus 8 `super_sked` files, 0 blocks, 0 missing days. Re-running makes 0 requests. Manifest at `torvik/timemachine/manifest.csv` in the private repo (PRs #14, #15).
- **Task 1.4, Time Machine columns and timing** (PR #18, merged 2026-11-11). All 45 columns named; for two seasons they match the season-end CSV exactly (`docs/TORVIK.md`). The file dated D already counts D's games, late West Coast and Hawaii games included, so it is known at 06:00 Eastern on D+1. A game uses the latest file known before its tip. Rule 3 was tightened: the old Eastern-date reading would have leaked 52 late-night games. One early count remains in 90,423 team-games (an 11 a.m. game against a non-D-I team).
- **Polymarket 2025-26 history backfill:** finished 2026-11-10. 21,983 calls over 10 runs, 0 errors, 21,906 price-history files. New games are picked up by the daily `polymarket-history` job.

## In progress

- **Task 1.6, `game_history` and the no-leakage tests:** PR #19. CI is green. The `auditor` found no blockers; its three fixes are in (daily pulls never mixed, a real-data record-clock test, `status` moved to labels). **Waiting for Owen's sign-off**, because every feature comes from this table and it clarifies the baseline's "file dated D−1" in PREREGISTRATION.md to the as-of rule. Real data: 44,740 games, 0 with ratings known at or after tip.
- **Scheduled jobs skip slots:** GitHub still skips some (on 2026-11-11 it missed the 13:17 UTC odds slot). The backup trigger started it at 13:53 UTC, once the PC woke. The backup only works while the PC is awake.

## Waiting on

| What | Waiting for | Blocks | Fallback |
| --- | --- | --- | --- |
| PR #19 (task 1.6) | Owen's sign-off | Task 1.7 | n/a |
| Task 1.2: are CBBD's lines true closes? | About a week of finished games in our own snapshots, and 2026-27 CBBD games ingested | The $30 historical-odds decision | n/a |
| Availability report access | Nothing sent yet; held until task 1.11 | G2 news collection | Google News RSS, team sites |

## Next up

1. **About 2026-11-13:** check the G0 gate and run the auditor.
2. **Before task 1.2:** ingest this season's (2026-27) CBBD games and lines with the `cbbd-ingest` workflow. No 2027 games are loaded yet, so every snapshot event is `no_game` and no `own_snapshots` close can be built.
3. **About 2026-11-17:** task 1.2, comparing CBBD's lines with our pre-tip snapshots. Then recommend to Owen whether to buy one month of historical odds. It looks likely, because CBBD has no opening spreads before 2024-25.
4. **Task 1.7 and everything after it is model logic, for Fable.** Once PR #19 is merged, the baseline scorecard reads `game_history`. Tell Owen; don't start it.

## Notes for the next session

- Start Claude Code in `C:\Users\omhek\shac-sb-index` so `CLAUDE.md` loads.
- **The Torvik task missed 5 a.m. on 2026-11-11.** The PC was asleep, and "run when available" didn't catch it. It was run by hand at 7:33 a.m. Owen was given the command to let the task wake the PC (`WakeToRun`, plus wake timers allowed on AC power). If its `LastRunTime` is stale, check that first.
- **Leakage tests on real data:** after `shac.tables.build`, run `SHAC_DATA_DIR=../shac-sb-index-data uv run pytest tests/test_no_leakage.py`. CI runs only the synthetic half.
- **Local folders:**
  - `C:\Users\omhek\shac-sb-index` is the main checkout.
  - `C:\Users\omhek\shac-sb-index-data` is the private repo (raw data and built tables).
  - `C:\Users\omhek\shac-sb-index-runner` is used only by the Torvik task. Don't work in it.
  - `C:\Users\omhek\shac-sb-index-wt-*` folders are task worktrees (`-tm` is task 1.4, merged; `-history` is task 1.6, PR #19). Safe to remove with `git worktree remove` once their branches are merged.
- Merge rules are in DECISIONS.md (2026-11-10): Claude merges docs, plumbing and data PRs when the PR's own check passes; Owen signs off on model, ledger and scorecard PRs.
- Free-tier budgets: The Odds API about 434 of 500 credits a month; CBBD 504 of 1,000 used in November.
