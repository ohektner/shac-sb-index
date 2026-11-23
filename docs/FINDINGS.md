# Findings, November 2026: v1 and the market

Shac SB Index, *a college basketball forecasting model*. This page records what the first model (v1) showed against the betting market, and how each result was tested.

## Summary

Three tests, each with its method and its reading written down before it was run:

1. **Against the closing line, v1 has no edge.** Its distance to CBBD's line is a hair smaller than Torvik's own number (1.989 vs. 2.006 points; G1 passes), but against the spread it goes 50.3%. The calibrated cover chance barely moves off 50%, so the Pick/Lean rule makes almost no calls (1 Lean in four seasons).
2. **Against the night-before line, v1 has no edge either.** Over four seasons of timed odds (18,299 games), the line moved toward v1's side by **+0.015 points** on average from about 10:20 p.m. Eastern the night before to the close (95% CI −0.002 to +0.031). That is no better than a coin flip (+0.013).
3. **Even perfect knowledge of who sits doesn't add a measurable edge.** On games with a new absence (6,816), adding the planned availability adjustment with hindsight raised CLV by **+0.014** (95% CI −0.004 to +0.032).

Public team ratings are already in the price by the night before. The availability layer (G2) as specified would not change that, so it isn't being built as specified.

## Setup

- **Games and results:** CollegeBasketballData (CBBD), D-I vs. D-I, 2018-19 to 2026-27.
- **Ratings:** Bart Torvik's Time Machine files, used strictly as of before each prediction. A file dated D is known at 06:00 Eastern on D+1; the first reading of that rule would have leaked 52 late-night games (task 1.4, `docs/TORVIK.md`). Tests in `tests/test_no_leakage.py`.
- **Torvik baseline:** the rating gap times expected possessions, plus a home-court constant fit on earlier seasons (`docs/PREREGISTRATION.md`).
- **Model v1:** the same two parts with fitted weights (about 1.0 × the rating gap, plus about 3.4 points of home court), fit walk-forward. Four extra inputs were tried on training seasons only; none passed the keep rule (`docs/MODEL.md`).
- **Lines:**
  - CBBD's line for past seasons (not a verified close: task 1.2, branch C);
  - our own snapshots of US books and Pinnacle since 2026-11-10;
  - a bought month of The Odds API's historical archive: 1,761 snapshots, three per game day, 2022-23 to 2025-26, US books (`docs/EARLY_LINE.md` section 4).
- **CLV** for a game is how far the line moved toward the predicted side between the call and the close, in points: `side × (call line − close line)` in home-spread terms. Positive is good.

## Test 1: against the close (task 1.9)

From `scorecard/backtest/` (held-out seasons 2022-23 to 2025-26, pooled):

| | Model v1 | Torvik baseline |
| --- | --- | --- |
| Distance to the close (G1 test) | 1.989 | 2.006 |
| ATS | 50.3% | 50.3% |
| Brier score (coin flip = 0.25) | 0.2500 | 0.2500 |
| Pick / Lean calls, four seasons | 0 / 1 | 0 / 1 |

The calibration slope is 0.001 to 0.008. The live curve would need about 20 points of edge for a Lean, which never happens.

**Data problem found here.** The first calibration run produced a 10–1 Pick record, and almost all of it came from CBBD lines with the right number on the wrong team (for example, Duke −8.5 stored as Texas Tech −8.5). Two line checks now drop such lines (63 of 26,116 scored games). A hand check of 6 against public listings found 5 on the wrong team and 1 likely. Small wrong-team lines (about 1% of Draft Kings lines, at neutral sites) can't be caught and stay in; both predictors face them equally. (DECISIONS.md 2026-11-22; `docs/TABLES.md`, "Line checks".)

## Test 2: against the night-before line (archive)

The question: pros take the early number and are judged by whether the line moves their way. Does v1 see where early lines go?

**First read** (390 captured 2026-27 games, Nov 14–21): CLV +0.46 (95% CI 0.30 to 0.61), "promising" by the reading fixed beforehand. Checks added afterwards showed most of it was a one-week drift toward underdogs ("always take the underdog" earned +0.33). A rough full-season check on CBBD's untimed opens gave +0.12.

**The archive** (the real test):

- **Checks.** Check 2 passed: the archive's close was a median 0.25 point from CBBD's line, with opposite favorites in 0.03% of games. Check 1, coverage, failed in two seasons (82.0% and 89.7% of games had a night-before line, against a 90% bar). The cause was timing: 8.3% of lines are posted only on game day, mostly November openers and March tournaments. Owen chose to proceed and recorded it before any result (DECISIONS.md 2026-11-23).
- **Results,** pooled over four seasons (18,299 games with CLV):

  | Side rule | CLV (95% CI) | ATS at the night-before line |
  | --- | --- | --- |
  | Model v1 | +0.015 (−0.002 to +0.031) | 50.1% |
  | Torvik baseline | +0.006 (−0.011 to +0.023) | 50.2% |
  | Coin flip | +0.013 (−0.004 to +0.029) | 49.7% |
  | Always home | +0.053 (+0.037 to +0.070) | 50.1% |
  | Always the underdog | −0.059 (−0.076 to −0.042) | 49.8% |

  - **By season:** +0.003, −0.010, +0.079, −0.013.
  - **Over four seasons, lines drift toward home teams and favorites,** the opposite of the one-week drift in the first read.
  - **The curve fit on night-before edges has a slightly negative slope:** bigger disagreements covered slightly less often. So there are no calls in the backtest, and none live under this season's curve.
- **Verdict by the pre-registered reading: "no."**

## Test 3: perfect knowledge of absences (G2 ceiling)

The question: G2's premise is that news about a player sitting reaches us before the line moves. If we had known exactly who would sit, would the adjusted model beat the night-before line? This uses hindsight on purpose, so the real G2 can only do worse.

- **The adjustment** (`docs/PLAYER_IMPACT.md` sections 1 and 2, approved 2026-11-12):
  - each new absence is a top-8 rotation player who played the previous game, weighted by minutes per game ÷ 40;
  - one value `v` converts the missing load into points, fit walk-forward on box scores and final scores.
- **`v` came out at 0.86 to 1.01 points per full-time player,** about 4–5 standard errors from zero. The effect is real but small: the average adjustment on absence games was 0.52 points, and only 9 games in four seasons reached 2 points.
- **On absence games** (6,816):
  - perfect-knowledge CLV is +0.036 (0.006 to 0.065), against the base model's +0.022;
  - the paired gain is +0.014 (−0.004 to +0.032);
  - ATS is 51.2% at the night-before line and 51.6% at the close.
- **Verdict by the pre-registered reading: "the line already prices it."**
- **Limit:** the adjustment weights every rotation minute the same, so it undercounts stars. A model that values players individually is a different, untested idea.

## What the process caught

- A leak of 52 late-night games in the first reading of the ratings timing rule (task 1.4).
- CBBD's Bovada lines failing the closing-line tests, so they were dropped (task 1.2).
- Wrong-team CBBD lines that made a fake 10–1 Pick record (line checks).
- A one-week underdog drift that made v1 look strong against early lines (the first read's checks, then the archive).
- A failed coverage check, handled by a dated decision before any result was computed.

Every rule above was committed and pushed before its result existed. The timestamps are in the PR history (#75, #76) and the decision log.

## What wasn't tested

- Player-level impact values (pros rate players, not teams).
- Stale lines: US books lagging Pinnacle after it moves. Our snapshots, 7 a day, are probably too coarse to catch them.
- Softer markets: totals, halves, props.
- The very first numbers posted, at one or two books. The archive's night-before snapshot comes after most books have posted. On the 666 games whose night-before line came from only 1–2 books, CLV was +0.10 (95% CI −0.01 to +0.21).

## Reproduce

The scripts and summary tables are in `analysis/2026-11-early-line/`. The raw data is private (the sources' terms).

1. `uv run python -m shac.tables.build --data-dir <private repo>`
2. `uv run python -m shac.scorecard.run --data-dir <private repo>` (test 1)
3. `uv run python -m shac.ingest.odds_history --data-dir <private repo>` (needs a paid key), then `uv run python -m shac.tables.odds_history --data-dir <private repo>`
4. The scripts in `analysis/2026-11-early-line/README.md`, in order (tests 2 and 3)
