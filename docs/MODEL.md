# The margin model

What the model is, how it is fit and tested, and what has been tried. Built in task 1.8. The rules it is judged by are in [PREREGISTRATION.md](PREREGISTRATION.md); the choices behind it are in [DECISIONS.md](DECISIONS.md) (2026-11-11).

**Status: built and fit. Not yet compared with the Torvik baseline or the closing line.** That comparison is the scorecard's, and the scorecard waits for task 1.2 to fix the backtest close.

## What it is

```
pred_margin = slope × rating_margin + home_court × home
```

- `rating_margin` is the baseline's rating part: the two teams' efficiency gap times expected possessions.
- `home` is 1 on the home team's floor and 0 at a neutral site.
- `slope` and `home_court` are fit by least squares on past seasons' final scores. The Torvik baseline fixes the slope at 1; here it is learned.

It learns from final scores only. A line is never an input and never the thing it is fit to.

## Run it

```
uv run python -m shac.model.run --data-dir <private repo>
```

- Reads `tables/game_history.parquet`. No network.
- Prints the input comparison and the fitted weights (both below).
- Writes `tables/model_predictions.parquet` in the private repo: one row per game in the scored seasons.
- Prints nothing about held-out accuracy and reads no line.

| Code | What it does |
| --- | --- |
| `sql/model/features.sql` | The inputs, one row per game, from `game_history`'s key and feature columns |
| `src/shac/model/margin.py` | The fit, walk-forward, leave-one-season-out, and the input comparison |
| `src/shac/model/run.py` | The command above |
| `tests/test_margin_model.py` | Made-up games with hand-worked answers, and the leakage tests |

## How it is tested on the past

- **Walk-forward.** Each scored season (2023 to 2026, and 2020-21 in its own row) is predicted by a fit on the eligible seasons before it. 2020-21 is never trained on.
- **Leave one season out.** Within a set of training seasons, each is predicted by a fit on the others. This picks the inputs (below), and task 1.9's calibration will use it.

## Fitted weights (2026-11-11)

| Season predicted | Trained on | Games | Slope | Home court |
| --- | --- | ---: | ---: | ---: |
| 2020-21 | 2019, 2020 | 10,925 | 0.998 | 3.48 |
| 2023 | 2019, 2020, 2022 | 16,400 | 1.011 | 3.34 |
| 2024 | + 2023 | 22,115 | 1.008 | 3.41 |
| 2025 | + 2024 | 27,841 | 1.018 | 3.39 |
| 2026 | + 2025 | 33,605 | 1.032 | 3.38 |

A slope this close to 1 says Torvik's ratings are already on the right scale. So model v1 is very close to the baseline, and should land near it on the scorecard.

## Inputs tried

Each candidate adds one idea to the two-input model. They are compared on the three train-only seasons (2019, 2020, 2022), leave-one-season-out, by mean error against final scores. The held-out seasons are not used, so they have no say in what the model is.

**Keep rule, written before the first run:** the gain is at least 2 standard errors and the candidate wins in all three seasons.

| Candidate | Idea | Error (points) | Gain vs. core | Standard errors | Seasons won |
| --- | --- | ---: | ---: | ---: | ---: |
| `core` | The two inputs above | 8.913 | | | |
| `early` | A different slope in November and in December | 8.908 | +0.006 | 1.4 | 2 of 3 |
| `conf` | Different home court in conference games | 8.909 | +0.004 | 0.9 | 2 of 3 |
| `rest` | Rest-day advantage | 8.914 | −0.001 | −1.3 | 1 of 3 |
| `split` | Offense and defense gaps weighted separately | 8.914 | −0.001 | −1.2 | 1 of 3 |

16,400 games. None passed, so the model stays at two inputs.

**What this says.** Ratings-only inputs don't add to what Torvik's ratings already know. A real gain has to come from information the ratings don't have, which is what the availability layer (G2) is for.

## No leakage

- The feature query reads no score and no line. A test blanks them all and checks that no input moves.
- A season's predictions don't change when its own or any later results are rewritten, or when every line is rewritten. Tests check both, and that earlier results do move the fit.
- Rest days read only the status of earlier games.
