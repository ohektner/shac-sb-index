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

1. Collection: CBBD (`cbbd-ingest`, GitHub, daily) and Torvik (Owen's PC, 5 AM) save to the private data repo. Every other collection job was stopped on 2026-11-23 (DECISIONS.md).
2. `databricks-sync` (new nightly GitHub workflow): builds the tables, uploads the day's export to a Unity Catalog volume, and starts the Databricks job.
3. Databricks job:
   - **Pipeline:** Auto Loader (`availableNow`) lands the export in bronze. Silver joins and checks it, with the expectations. Gold holds the features, predictions and scorecard.
   - **Score:** the `champion` model prices upcoming games.
   - **Scorecard:** updates the season record.
   - **Freshness check:** fails if no new games or ratings have arrived in 2 days. Job failure emails Owen.
4. The workflow reads the gold scorecard and commits `site/season.json`.

**What gets uploaded:** decided in D0 (see "Open").

## Phases

| Phase | What | Exit check |
| --- | --- | --- |
| D0, Setup | Owen: Free Edition account, token, GitHub secrets. Claude: CLI, a hello-world bundle, a volume upload, a job started from Actions, the data-upload rule. | A job starts from a GitHub Action. The upload rule is in DECISIONS.md. |
| D1, Pipeline | Bronze, silver and gold for 2018-19 to now, with expectations. | Gold matches `shac.tables.build` exactly (counts and keys). The line checks drop the same 63 games. |
| D2, Models | v1 and the four rejected inputs in MLflow. `champion` registered. | Parity with the published scorecard: 1.989 vs. 2.006, 50.3% ATS, 22,132 games. **Owen signs off** (scorecard logic). |
| D3, Nightly | The sync workflow, the job schedule, the freshness alarm and `season.json`. | 7 straight nights with no manual commits. One forced failure emails Owen. |
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

## Open

- **What gets uploaded (Owen):** recommended, per-game features and results only (rating gaps and components as differences, game scores, CBBD lines). Torvik's ratings and all raw files stay out of Databricks, given the training clause and Torvik's robots.txt stance on AI bots. This keeps the earlier rule "raw files stay out."
