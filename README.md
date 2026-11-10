# Shac SB Index

*A college basketball forecasting model.*

Shac SB Index prices every D-I men's basketball game against the point spread and keeps a public, append-only record of every call it makes, wins and losses. It's a portfolio project in data and AI. Research, not betting advice.

**Status:** setting up. The first job, which saves the day's lines several times a day, is being switched on. The model, the board and the public record come next.

## How it works

1. Scheduled jobs save raw copies of the lines and team ratings, each stamped with the time it was fetched.
2. A model predicts each game's margin from ratings as they stood before tip-off.
3. That number is compared with the line. Each game gets a Pick, a Lean or no call, based on how often edges of that size have actually covered.
4. Every call is committed to this repo before tip-off and never edited.
5. A scorecard compares the calls with the closing line and the results.

## Docs

- [Architecture](docs/ARCHITECTURE.md): the data flow diagram, table schemas and the no-leakage rule
- [Plan](docs/PLAN.md): gates and tasks
- [Pre-registration](docs/PREREGISTRATION.md): how the model is judged, fixed before any results
- [Decision log](docs/DECISIONS.md): what was decided and why
- [Source verification](docs/VERIFIED.md): what was checked about each data source and its terms

## Data and credit

Lines come from The Odds API. Games and box scores come from CollegeBasketballData.com. Ratings come from Bart Torvik's T-Rank. Raw data from these sources is kept in a private repo and is not republished here.
