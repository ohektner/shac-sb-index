# Shac SB Index

*A college basketball forecasting model.*

Shac SB Index prices every D-I men's basketball game against the point spread and keeps a public, append-only record of every call it makes, wins and losses. It's a portfolio project in data and AI. Research, not betting advice.

**Status (November 2026):** data collection, model v1 and the backtest are done. The live record and board are not built.

## Findings so far

v1 (Torvik's ratings, rebuilt point-in-time with fitted weights) does not beat the market:
- **Against the closing line:** 50.3% against the spread, with almost no calls.
- **Against the night-before line, over four seasons of timed odds:** the line moved toward v1 by +0.015 points, about what a coin flip gets.
- **With perfect hindsight about which players would sit:** the planned availability adjustment adds +0.014 points, which is not measurable.

Each test's method and reading was committed before it ran. Details and limits: [docs/FINDINGS.md](docs/FINDINGS.md).

## How it works

1. Scheduled jobs save raw copies of the lines and team ratings, each stamped with the time it was fetched.
2. A model predicts each game's margin from ratings as they stood before tip-off.
3. That number is compared with the line. Each game gets a Pick, a Lean or no call, based on how often edges of that size have actually covered.
4. Every call is committed to this repo before tip-off and never edited.
5. A scorecard compares the calls with the closing line and the results.

## Docs

- [Findings](docs/FINDINGS.md): what v1 showed against the market, and how it was tested
- [Architecture](docs/ARCHITECTURE.md): the data flow diagram, table schemas and the no-leakage rule
- [Status](docs/STATUS.md): where things stand right now
- [Plan](docs/PLAN.md): gates and tasks
- [Pre-registration](docs/PREREGISTRATION.md): how the model is judged, fixed before any results
- [Decision log](docs/DECISIONS.md): what was decided and why
- [Source verification](docs/VERIFIED.md): what was checked about each data source and its terms

## Data and credit

Lines come from The Odds API. Games and box scores come from CollegeBasketballData.com. Ratings come from Bart Torvik's T-Rank. Raw data from these sources is kept in a private repo and is not republished here.
