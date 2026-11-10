# Status

Where the project stands right now. Update this at the end of every working session. For what was decided and why, see [DECISIONS.md](DECISIONS.md); for the plan, see [PLAN.md](PLAN.md).

**Last updated:** 2026-11-10

## Current gate

**G0, urgent.** Exit: the odds snapshot job runs on schedule several times a day, 3 days in a row. Scheduled runs started 2026-11-10, so the earliest close is about 2026-11-13. At the gate, run the check in PLAN.md and have the `auditor` review.

## Running unattended

| Job | Where | Schedule | Check it |
| --- | --- | --- | --- |
| Odds snapshot (US books + Pinnacle) | GitHub Actions | 7 a day | `data/status/odds_snapshot_log.csv` |
| Polymarket snapshot | GitHub Actions | 6 a day | `data/status/polymarket_snapshot_log.csv` |
| Polymarket history (recent games) | GitHub Actions | Daily, 10:29 UTC | Actions tab, `polymarket-history` |
| Snapshot watch (alarm) | GitHub Actions | Twice a day | Fails and emails Owen if odds snapshots stop |
| Torvik daily snapshot | Owen's PC, Windows task | 5:00 AM Phoenix | `data/status/torvik_snapshot_log.csv`, and `%LOCALAPPDATA%\shac-sb-index\torvik-task.log` |

## Done

- **G0 build:** odds and Torvik snapshot jobs, status logs, watch alarm, CI (PRs #1–#2, #7).
- **Task 1.1:** CBBD raw backfill, 8 seasons, 496 calls (PRs #3, #5).
- **Task 1.5:** team-name map, 369 teams, 0 unmatched (PR #6).
- **Clean tables** in the private repo: games, results, player_box, cbbd_lines, odds_snapshots, closing_lines (PR #10). Rebuild with `uv run python -m shac.tables.build --data-dir <private repo>`.
- **Polymarket collection** (PR #8), the G3 second market, with written permission.
- **Polymarket 2025-26 history backfill:** finished 2026-11-10. 21,983 calls over 10 runs, 0 errors, 21,906 price-history files. New games are picked up by the daily `polymarket-history` job.

## In progress

Nothing running by hand. Everything above runs on its own schedule.

## Waiting on

| What | Waiting for | Blocks | Fallback |
| --- | --- | --- | --- |
| Torvik Time Machine backfill (task 1.3) | Bart Torvik's reply (DM on X, sent 2026-11-10) | Tasks 1.4, 1.6, 1.7 and everything after | After about a week, check whether Torvik's per-season `YYYY_results.csv` files hold pregame ratings |
| Task 1.2: are CBBD's lines true closes? | About a week of finished games in our own snapshots | The $30 historical-odds decision | n/a |
| Availability report access | Nothing sent yet; held until task 1.11 | G2 news collection | Google News RSS, team sites |

## Next up

1. **About 2026-11-13:** check the G0 gate and run the auditor.
2. **About 2026-11-17:** task 1.2, comparing CBBD's lines with our pre-tip snapshots. Then recommend to Owen whether to buy one month of historical odds. It looks likely, because CBBD has no opening spreads before 2024-25.
3. **Once Torvik ratings are in hand:** decode the columns (1.4), join history and write the leakage tests (1.6), then the baseline scorecard (1.7).

## Notes for the next session

- Start Claude Code in `C:\Users\omhek\shac-sb-index` so `CLAUDE.md` loads.
- **Local folders:**
  - `C:\Users\omhek\shac-sb-index` is the main checkout.
  - `C:\Users\omhek\shac-sb-index-data` is the private repo (raw data and built tables).
  - `C:\Users\omhek\shac-sb-index-runner` is used only by the Torvik task. Don't work in it.
  - `C:\Users\omhek\shac-sb-index-wt-*` folders are old task worktrees. Safe to remove with `git worktree remove` once their branches are merged.
- Merge rules are in DECISIONS.md (2026-11-10): Claude merges docs, plumbing and data PRs when the PR's own check passes; Owen signs off on model, ledger and scorecard PRs.
- Free-tier budgets: The Odds API about 434 of 500 credits a month; CBBD 504 of 1,000 used in November.
