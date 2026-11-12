# Plan

The project moves through gates. A gate closes when its exit criteria are met, not on a date. The end of the 2026-27 season is the only fixed deadline.

Each task lists who owns it, which model that agent runs on, what it depends on and the check that says it's done. "Fable" is the lead, who designs and reviews. Checks are run by `qa` and reported with output.

## Gates

| Gate | Exit criteria |
| --- | --- |
| **G0, urgent** | The odds snapshot job runs on schedule, several times a day, 3 days in a row, writing to the private data repo. |
| **G1, Foundation** | History for 2018-19 through 2025-26 is joined. The team-name map matches 100% of games with no duplicates. The Torvik baseline scorecard runs. The margin model matches or beats the Torvik baseline on held-out seasons, or Torvik's number ships as the model and the README says so. Calibration is fit on training seasons only. Tier cutoffs were committed to PREREGISTRATION.md before the per-tier backtest ran, and the per-tier backtest is reported even if no tier beats 52.4%. The board and ledger update 7 nights unattended, with a morning and a late run. News collection is running. |
| **G2, AI availability layer** | Reading accuracy 95%+ on about 200 hand-labeled items. Precision 95% on rotation players, checked against box scores. Recall reported. The effect on error vs. the close is measured on games with flagged absences. The accuracy report is drafted. |
| **G3, Market study** | The line-movement model is tested on held-out games. If Kalshi gives permission, the Kalshi vs. sportsbook-close study is complete (by conference tier, time before tip and volume). If not, G3 is the line-movement model alone, and the README says why. |
| **G4, Season review** | The case study is drafted for ohektner.github.io. |

## G0: snapshots

| # | Task | Owner (model) | Depends on | Exit check |
| --- | --- | --- | --- | --- |
| 0.1 | Scaffold the repo, CLAUDE.md, docs, agent definitions | Fable | Owen's approval | `uv run ruff check . && uv run pytest -q` pass |
| 0.2 | Odds API client and snapshot script | data-engineer (sonnet) | 0.1 | Tests pass with a fake response; missing key fails cleanly |
| 0.3 | Odds snapshot workflow: 7 runs a day (US books plus Pinnacle), raw file to the private repo, status row to the public repo | data-engineer (sonnet) | 0.2, secrets | A manual run is green and the file lands in the private repo |
| 0.4 | Daily Torvik CSV snapshot (2 files, once a day). Actions is blocked by the site (tested 2026-11-10), so this runs from Owen's machine. | data-engineer (sonnet) | 0.1, secrets | A local run saves both files and pushes them |
| 0.5 | Review: timestamps, no key or raw odds in the public repo or logs | auditor (opus) | 0.2 to 0.4 | Written findings, no open blockers |
| 0.6 | First real call: confirm one call returns every game, record books seen | data-quality (opus) | 0.3 | VERIFIED.md updated |
| **Gate** | 3 days in a row of scheduled runs | qa (sonnet) | 0.3 | `data/status/odds_snapshot_log.csv` shows at least 8 `ok` rows on each of 3 straight days |

## G1: foundation

Tasks 1.1, 1.3 and 1.11 can run in parallel. So can 1.9 and 1.10 once the ledger schema is fixed.

| # | Task | Owner (model) | Depends on | Exit check |
| --- | --- | --- | --- | --- |
| 1.1 | CBBD client and backfill for 8 seasons (games, results, lines, team box, player box), paging by date window | data-engineer (sonnet) | `CBBD_API_KEY` | No window returns the row cap; calls used are logged and under 400 |
| 1.2 | Test what CBBD lines mean: sign, open vs. close, which books, coverage by conference. Then recommend to Owen whether to buy one month of the $30 Odds API tier for timestamped historical odds (10 credits per call; estimate 15,000 to 22,500 credits for 2021-22 through 2025-26). Owen decides. | data-quality (opus) | 1.1 | VERIFIED.md updated with tested answers, and a written recommendation on the paid month |
| 1.3 | Torvik Time Machine backfill, 10+ seconds apart, cached for good | data-engineer (sonnet) | Bart Torvik's OK | One file per in-season day, 2018-19 through 2025-26; a re-run makes zero requests |
| 1.4 | Decode Time Machine columns; test whether a date's file includes that date's games | data-quality (opus) | 1.3 | Columns documented; no-leakage rule 3 confirmed or relaxed in DECISIONS.md |
| 1.5 | Team-name map across CBBD, Torvik and The Odds API | data-quality (opus) | 1.1, 1.3, G0 data | `sql/checks/team_map.sql` returns 0 unmatched and 0 duplicate rows |
| 1.6 | Join history; no-leakage tests | data-quality (opus) | 1.5 | `uv run pytest tests/test_no_leakage.py` passes |
| 1.7 | Torvik baseline scorecard in DuckDB SQL | modeler (opus) | 1.6 | `sql/scorecard/*.sql` run on held-out seasons and produce every pre-registered metric. Built and tested on made-up data 2026-11-11; the first real run waits for 1.2 to fix the backtest close |
| 1.8 | Margin model, walk-forward by season | modeler (opus) | 1.7 | Distance to the close reported next to the baseline for each held-out season. Built and fit 2026-11-11 (`docs/MODEL.md`); the comparison prints with the first real scorecard run, after 1.2 |
| 1.9 | Calibration, tiers and the per-tier backtest | modeler (opus) | 1.8 | Reliability table and per-tier record with confidence intervals |
| 1.10 | Ledger writer, append-only test, morning and late pricing jobs. Before the first live run, top up the 2026-27 Time Machine files (`uv run python -m shac.ingest.torvik_timemachine --data-dir <private repo> --seasons 2027`, from Owen's PC) so days a daily pull missed have ratings, then rebuild the tables | data-engineer (sonnet) | 1.8 | CI fails on a PR that edits a ledger row; a job run adds rows before tip |
| 1.11 | News collection to the private repo | data-engineer (sonnet) | Permitted sources settled | Rows with fetch timestamps arrive daily |
| 1.12 | Board and scorecard page on Cloudflare Pages | web-builder (sonnet) | 1.10 | The site builds from the ledger and scorecard files alone |
| 1.13 | Leakage and evaluation audit | auditor (opus) | 1.6 to 1.10 | Written findings; every blocker fixed or accepted by Owen |
| 1.14 | README with the diagram and decision log link | docs-writer (sonnet) | 1.12 | Reviewed by Fable |
| **Gate** | 7 nights unattended | qa (sonnet) | all | The ledger has morning and late rows on 7 straight days with no manual commits |

`docs/PREREGISTRATION.md` is already committed. Tasks 1.7 to 1.9 must not change it. If a metric or cutoff has to change, that is a dated DECISIONS.md entry made before the affected results are looked at.

## G2: AI availability layer

| # | Task | Owner (model) | Exit check |
| --- | --- | --- | --- |
| 2.1 | Labeling set: about 200 news items, hand-labeled by Owen | ai-extraction (opus) | Label file committed with a labeling guide |
| 2.2 | Extraction step with schema-constrained JSON output, starting on `claude-haiku-5-5` | ai-extraction (opus) | Runs on the labeled set; cost per day logged |
| 2.3 | Evaluation harness: reading accuracy, precision and recall against box scores | ai-extraction (opus) | One command prints all three with intervals |
| 2.4 | Move to `claude-sonnet-5-5` only if Haiku misses a bar | ai-extraction (opus) | Side-by-side results recorded in DECISIONS.md |
| 2.5 | Player impact and status probabilities; adjust the margin for new absences only | modeler (opus) | Error vs. the close on flagged games, with and without the adjustment |
| 2.6 | Audit and accuracy report | auditor (opus), docs-writer (sonnet) | Report drafted |

## G3 and G4

- **G3:** a line-movement model on our own snapshots (does the open-to-close move predict anything, and does our number predict the move), tested on held-out games. The Kalshi study is added only with Kalshi's written permission.
- **G4:** the season case study: the problem, the system diagram, results including what failed, and what Owen would change.

## Optional: Databricks Free Edition mirror (portfolio, not scheduled)

Added 2026-11-12 as an idea, not a gate task. Do it only after task 1.2 and the first real scorecard run, and only if there are results worth showing. It is a read-only mirror; the repos stay canonical, and if a Databricks number and the repo disagree, the repo wins.

- **What:** load the derived clean tables into Delta tables in Unity Catalog (bronze, silver, gold naming over the existing raw, clean and scorecard layers); port `sql/scorecard/*.sql` to Databricks SQL and show it as a dashboard (baseline vs. model, calibration, CLV); log each model version as an MLflow run (parameters, leave-one-season-out metrics). About one or two sessions.
- **What not:** the collection jobs stay on GitHub Actions and Owen's PC (Free Edition limits outbound internet). Raw files stay out (volume).
- **Before uploading anything:** check the Torvik, Polymarket and Odds API terms and permissions. A public dashboard shows only our own derived numbers (edges, scorecard metrics), not their ratings or prices.
- **Exit check:** the dashboard's scorecard numbers match `shac.scorecard.run` output exactly.

## What Owen does

- Adds the secrets (see ARCHITECTURE.md).
- Sends the three permission emails (Torvik, Kalshi, availability reports).
- Merges PRs after reviewing them.
- Hand-labels the G2 set.
- Checks his employer's outside-activity policy before the picks page goes live.
