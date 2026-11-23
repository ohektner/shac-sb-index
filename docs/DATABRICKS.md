# Databricks phase (D0–D4)

Owen approved 2026-11-23: live, scope = test 1 plus the 2026-27 season. This phase doesn't look for an edge (v1 has none, `docs/FINDINGS.md`). It runs the same work as a data platform on Databricks Free Edition. The repos stay canonical: if Databricks and the repo disagree, the repo wins.

## Design

1. **Everything is code.** A Databricks Asset Bundle in `databricks/` defines the pipeline, job, model and dashboard. `databricks bundle deploy` rebuilds the workspace from scratch. That covers Free Edition's lack of an SLA and its deletion of inactive accounts.
2. **Live.** A nightly job scores this season's games with the frozen v1 (`champion`) and updates the season scorecard against the close. Every result is out of sample.
3. **Data checks are pipeline expectations.** The CBBD line checks (DECISIONS.md 2026-11-22), the 100% team-map match and the no-leakage rule (ratings dated before tip) run as Lakeflow pipeline expectations, with pass and drop counts.
4. **MLflow.** v1 and the four rejected inputs (early-season form, rest, offense vs. defense, conference home court) are logged with the same leave-one-season-out metrics. v1 is registered in Unity Catalog with the alias `champion`.
5. **Public face.** Free Edition dashboards and Apps need a login, and Apps stop after 24 hours. So each night `site/season.json` (derived numbers only) is committed to this repo, and ohektner.github.io/shac/ charts it with an "updated through" date. Screenshots and a recording show the dashboard, the pipeline graph, the MLflow comparison and a Genie space.

**Out of scope:** tests 2 and 3 and all Odds API data. They stay in Python.

## Data flow

GitHub's schedule runs late (the CBBD ingest's own cron fired 7–10 hours late in November 2026), so nothing here depends on GitHub's timing:

1. **Collection:** CBBD (`cbbd-ingest`, GitHub, daily, often late) and Torvik (Owen's PC, 5 AM Phoenix) save to the private data repo.
2. **Upload:**
   - `databricks-sync` runs right after each CBBD ingest finishes (a `workflow_run` trigger) and uploads new CBBD files and the team map.
   - The PC Torvik task uploads new Torvik files right after its pull.
   - Both use `databricks/scripts/sync_raw.py`, which sends only files the volume doesn't have yet.
3. **Nightly job (`shac nightly`):** Databricks starts it on its own when a new file lands in `landing/raw/` (a file-arrival trigger, 3-minute settle, at most every 15 minutes). Its tasks:
   - **refresh:** the pipeline (Auto Loader picks up only new files).
   - **score** (`src/score.py`): the `champion` model prices every 2026-27 game into `gold_live_predictions`. Games not yet tipped are appended to `gold_prediction_log`, which is append-only. A game's live call is its last row before tip.
   - **season** (`src/season.py`): `gold_season_scorecard` and `gold_season_weekly`, in two scopes. `live` uses the logged pre-tip calls. `season` uses tonight's point-in-time rebuild of every game, including those played before the job existed.
   - **freshness** (`src/freshness.py`): fails if the newest CBBD fetch or daily Torvik pull is more than 48 hours old.
   - Any failure emails the job owner.
4. **Public file:** `season-export` (GitHub, twice a day) commits `site/season.json`, which holds derived numbers only. If GitHub runs it late, the page shows an older "updated through" date.
5. **Deploys:** `databricks-deploy` deploys the bundle whenever `databricks/` changes on main.
6. **Watchdog (`shac watchdog`):** runs on Databricks' own schedule at 03:00 UTC (8 PM Phoenix), so it doesn't depend on any upload. It fails, and emails the owner, if `shac nightly` hasn't succeeded in 30 hours or if CBBD or Torvik data is more than 48 hours old. That catches silence (no uploads, so no run and no failure email), which the nightly job alone can't.

**What gets uploaded:** see "Upload rule" below.

**Built (D2):** `src/pipeline/model.sql` (season roles, `gold_model_features`, `gold_baseline_predictions`); `src/train.py` (input selection and walk-forward fits, MLflow experiment `shac-margin-model`, champion registration); `src/scorecard.py` (coverage and metrics 2–4 as `gold_scorecard_*`). Calibration (metrics 5–6) stays in Python.

**Built (D1):** `databricks/src/pipeline/` holds bronze (`bronze.py`: Auto Loader on the landing volume, plus the team-map CSVs), silver (`silver.sql`: games, results, CBBD lines, Torvik ratings) and gold (`gold.sql`: CBBD closes, `gold_game_history`). The `d1_parity` job refreshes the pipeline, then `src/parity.py` compares each table with the local build's parquet in `landing/parity/`.

## Phases

| Phase | What | Exit check |
| --- | --- | --- |
| D0, Setup | Owen: Free Edition account, token, GitHub secrets. Claude: CLI, a hello-world bundle, a volume upload, a job started from Actions, the data-upload rule. | A job starts from a GitHub Action. The upload rule is in DECISIONS.md. |
| D1, Pipeline | Bronze, silver and gold for 2018-19 to now, with expectations. | Gold matches `shac.tables.build` exactly (counts and keys). The line checks drop the same games as the local build. **Met 2026-11-23:** `d1_parity` matched all 5 tables on every column (46,040 games, 433,122 rating rows). Expectation counts: 65 wrong-team, 68 books-disagree, 0 leakage, 0 unmapped names, 2 preseason rows. |
| D2, Models | v1 and the four rejected inputs in MLflow. `champion` registered. | Parity with the published scorecard: 1.989 vs. 2.006, 50.3% ATS, 22,132 games. **Owen signs off** (scorecard logic). **Met 2026-11-23, awaiting sign-off:** `d2_models` matched features, both predictors' per-game predictions, all 90 walk-forward weights, the input comparison and metrics 2–4 (pooled: 1.9894 vs. 2.0058, 50.30% vs. 50.30%, 22,132 games). `workspace.shac.margin_model` v1 = `champion` (2027 fit: 1.0441 × rating_margin + 3.3443 × home). |
| D3, Nightly | The sync workflow, the job schedule, the freshness alarm and `season.json`. | 7 straight nights with no manual commits. One forced failure emails Owen. **Built 2026-11-23;** first manual run passed (priced 1,300 games; season to date 476–463 through 11-22). The 7-night count starts 2026-11-24. |
| D4, Show it | Dashboard, Genie space, the /shac/ live chart, a README section with a diagram, screenshots. | Owen approves the page. |

## Upkeep and end

- **No routine work.** Fixes happen when a failure email arrives: a new team alias, a missed Torvik day (backfill from the Time Machine), token renewal, or a Free Edition change.
- **Tokens:** Databricks revokes a token unused for 90 days. The nightly run keeps it in use.
- **End of season:** when it ends in early April, pause the job schedule (`databricks/` config) and the `cbbd-ingest` and Torvik jobs.

## Free Edition limits that shaped this (docs checked 2026-11-23)

- Serverless compute only, with a daily quota. Going over stops compute for the rest of the day; data is kept.
- One SQL warehouse (2X-Small), at most 5 concurrent job tasks, and one active pipeline per type.
- Outbound internet is limited to trusted domains, so collection stays outside Databricks.
- Non-commercial use only. The terms say "Databricks reserves the right to train on your data," with no scope or opt-out stated.

## Workspace

The workspace is Databricks Free Edition, moved on 2026-11-23. The first signup turned out to be a time-limited free trial ($40 of credits). Everything was rebuilt from the repo with `bundle deploy`, the history was re-uploaded with `sync_raw.py`, and D1 and D2 parity both passed again. The local CLI profile `free` is the default.

## Upload rule (Owen, 2026-11-23)

Raw Torvik and CBBD files go to the landing volume as-is, so the point-in-time join (ratings published before tip) and the line checks run inside the pipeline. Odds API data stays out (scope). Only derived numbers are ever public. The Free Edition training clause and Torvik's robots.txt stance on AI bots were weighed: the ratings are already public, they stay in a private workspace, and nothing is republished.
