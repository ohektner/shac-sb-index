# Shac SB Index: rules for Claude

Shac SB Index is a college basketball forecasting model: D-I men's games, point spreads only. It is a portfolio project. Owen bets no money on it. Honest evaluation is the point; beating the market is a bonus.

Always show the name with its subtitle, "a college basketball forecasting model." Never spell out what "SB" stands for in the name or branding. Calling data a "sportsbook line" is fine.

Read these before working:

- `docs/ARCHITECTURE.md`: data flow, table schemas, sign conventions, the no-leakage contract.
- `docs/PLAN.md`: gates, tasks, who owns what.
- `docs/PREREGISTRATION.md`: metrics and tier cutoffs fixed before any backtest result was seen.
- `docs/DECISIONS.md`: what was decided and why. Add an entry for any modeling or design choice.
- `docs/VERIFIED.md`: what we checked about each data source, and its terms.

## Rules

- **Plan before multi-step work.** Show the plan and wait for Owen's approval before executing.
- **Don't create GitHub repos or touch secrets without Owen's go-ahead.** Owen adds secrets himself.
- **Merging (Owen approved 2026-11-10):** Claude may open PRs and merge docs, plumbing and data-collection PRs once checks pass and the lead has reviewed the diff. Anything touching the model, features, the ledger or the scorecard needs Owen's sign-off before merge. Stop for Owen's review at every gate.
- **Code changes go on a branch and through a PR.** Never commit code to `main` directly. The one exception: scheduled jobs commit data files (status logs, the ledger, site data) straight to `main`. Owen approved this on 2026-11-09 so the public timestamps are real.
- **Never delete files** unless Owen says to.
- **No leakage.** Every feature is point-in-time and carries an `as_of` timestamp earlier than tip-off. Season-end ratings never touch earlier games. Walk-forward by season, never shuffled across time. The 2020-21 season is handled separately (see DECISIONS.md).
- **The ledger is append-only,** enforced by a test. Existing rows are never edited or removed.
- **Calls are locked before tip and never edited.** Tier cutoffs change only through a dated DECISIONS.md entry, and only for games priced after that date. Every call stays on the public record, wins and losses.
- **Respect terms and rate limits.** Raw third-party data (odds feeds, API pulls, ratings files, news text) goes in the private repo `shac-sb-index-data`, never here. This repo publishes derived numbers only. Cache everything so nothing is fetched twice.
- **Kalshi is on hold.** No requests to Kalshi's API and no stored Kalshi data until Kalshi gives written permission (see DECISIONS.md).
- **Store raw source data with fetch timestamps** (UTC) so history can be rebuilt.
- **Report honestly.** If a check fails or a step was skipped, say so with the output.
- **Write for Owen.** He works in Python (pandas) and SQL and is newer to ML. Plain-language docstrings, readable code over clever code, and a sentence or two in DECISIONS.md for each modeling choice.
- **No picks marketing.** No units, "locks," paid tiers or affiliate links. The site carries a one-line "research, not betting advice" note.

## Stack

Python 3.13, uv, ruff, pytest, pandas, scikit-learn, DuckDB. Backtest and scorecard queries are DuckDB SQL files in `sql/` (Owen wants the SQL practice). All timestamps are stored in UTC.

Checks before any PR:

```
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

## Working with subagents

Agent definitions are in `.claude/agents/`. Fable (the lead) designs, delegates and reviews; Opus and Sonnet agents build.

- **Every handoff is self-contained:** goal, files, the relevant data contract, the exit check command, what to report back. Subagents start cold.
- **Development loop:** implement, run the exit check, fix. Stop when the check passes or after 3 failed attempts, then report with the failing output. Never weaken a test to make it pass.
- **Review:** the lead reviews every diff before it merges. The `auditor` reviews anything touching features, backtests, the ledger or the scorecard, and never reviews its own code.
- **One change at a time** for model and feature work, so the scorecard can attribute improvements.
- **Run independent tasks in parallel.** Owen has opted into multi-agent workflows for this project; keep each to about 5 agents.

Production jobs are not agents. They are plain Python on GitHub Actions cron. The only LLM call in production is the Phase 2 availability extraction step.
