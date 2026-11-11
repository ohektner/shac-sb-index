"""The backtest scorecard: the Torvik baseline and every pre-registered metric (task 1.7).

Run as:
    uv run python -m shac.scorecard.run --data-dir <private repo> [--out-dir scorecard/backtest]

Reads (private repo):  tables/game_history.parquet
Writes (private repo): tables/baseline_predictions.parquet and
                       tables/model_predictions.parquet (one row per game each)
Writes (public repo):  scorecard/backtest/*.csv (summary numbers only, one file per metric)

All the logic is DuckDB SQL in sql/scorecard/. This module only wires the queries together:

1. baseline_predictions.sql turns game_history into one predicted margin per game.
2. scored_games.sql lines every prediction up with its result and its lines.
3. Each metric_*.sql file summarizes that per season and pooled.

The metric queries read a generic `predictions` table (predictor, game_id, pred_margin,
cover_prob), so the margin model (task 1.8) is scored by the same SQL as the baseline.

What is fixed in docs/PREREGISTRATION.md stays fixed: nothing here may be tuned to results.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.model.margin import walk_forward
from shac.tables.build import REPO_ROOT, query_df, write_parquet

SQL_DIR = REPO_ROOT / "sql" / "scorecard"

# The columns every predictor hands to the scorecard, one row per predictor per game.
#   pred_margin  predicted home margin; empty if the predictor can't price the game.
#   cover_prob   calibrated probability that the predictor's side covers the line it is
#                scored against. In a backtest that line is the CLOSE: the side is the one
#                against the closing line, and cover_prob must be about that side and that
#                line (DECISIONS.md, 2026-11-11). Empty until calibrated (task 1.9).
# A game is scored only if every predictor has a pred_margin for it, so the baseline and
# the model are always compared on the same games.
PREDICTION_COLUMNS = ["predictor", "game_id", "pred_margin", "cover_prob"]

# Task 1.2 decides what the backtest closing line is. Until that is written in
# DECISIONS.md, the command-line run refuses to touch real data, so no result can be seen
# before the yardstick is fixed. Flip this in the same PR as that DECISIONS.md entry.
BACKTEST_CLOSE_DECIDED = False

# Which game_history columns are "the close" and "the open". Backtest seasons only have
# CBBD's lines. Whether CBBD's line is a true close is task 1.2; until then, backtest
# numbers against it are provisional.
CLOSE_SOURCES = {
    "cbbd": ("cbbd_close_home_spread", "cbbd_open_home_spread"),
}

# Output name -> SQL file. The names are the CSV file names.
METRICS = {
    "coverage": "coverage.sql",
    "metric_1_clv": "metric_1_clv.sql",
    "metric_2_distance_to_close": "metric_2_distance_to_close.sql",
    "metric_3_error_vs_results": "metric_3_error_vs_results.sql",
    "metric_4_ats": "metric_4_ats.sql",
    "metric_5a_brier": "metric_5a_brier.sql",
    "metric_5b_reliability": "metric_5b_reliability.sql",
    "metric_6_tiers": "metric_6_tiers.sql",
}


def read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def baseline_predictions(game_history: pd.DataFrame) -> pd.DataFrame:
    """The Torvik baseline's predicted home margin for every game in a scored season.

    Scored seasons are the held-out ones and 2020-21 (sql/scorecard/seasons.sql). The
    home-court constant for each is fit only on earlier seasons. Besides the prediction
    columns, the frame carries how each number was made: `rating_margin`, `home_court`,
    `n_train_games` and `train_seasons`.
    """
    con = duckdb.connect()
    con.register("game_history", game_history)
    con.execute(read_sql("seasons.sql"))
    out = query_df(con, read_sql("baseline_predictions.sql"))
    con.close()
    return out


def baseline_fit(game_history: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    """Per scored season: the home-court constant, what it was fit on, and how many
    final games were left out because a team had no rating."""
    con = duckdb.connect()
    con.register("game_history", game_history)
    con.register("baseline", baseline)
    out = query_df(con, read_sql("baseline_fit.sql"))
    con.close()
    return out


def score(
    game_history: pd.DataFrame, predictions: pd.DataFrame, close_source: str = "cbbd"
) -> dict[str, pd.DataFrame]:
    """Run every metric query. Returns one DataFrame per metric, keyed by METRICS name.

    `predictions` needs PREDICTION_COLUMNS and may hold several predictors (the baseline
    and the model); every metric is reported for each, on the games all of them predict.
    """
    repeated = predictions.duplicated(["predictor", "game_id"])
    if repeated.any():
        raise ValueError(
            f"{int(repeated.sum())} repeated (predictor, game_id) rows in predictions; "
            "a game must be scored once per predictor"
        )
    close_col, open_col = CLOSE_SOURCES[close_source]
    con = duckdb.connect()
    con.register("game_history", game_history)
    con.register("predictions", predictions[PREDICTION_COLUMNS])
    con.execute(read_sql("seasons.sql"))
    con.execute(
        f"CREATE VIEW lines AS SELECT game_id, {close_col} AS close_home_spread, "
        f"{open_col} AS open_home_spread FROM game_history"
    )
    con.execute(read_sql("scored_games.sql"))
    out = {name: query_df(con, read_sql(sql_file)) for name, sql_file in METRICS.items()}
    con.close()
    for df in out.values():
        df.insert(0, "close_source", close_source)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "scorecard" / "backtest")
    parser.add_argument("--close-source", choices=sorted(CLOSE_SOURCES), default="cbbd")
    args = parser.parse_args(argv)

    if not BACKTEST_CLOSE_DECIDED:
        print(
            "Not run. The backtest closing line is not decided yet (task 1.2). Record the "
            "decision in docs/DECISIONS.md, then set BACKTEST_CLOSE_DECIDED in "
            "src/shac/scorecard/run.py."
        )
        return 1

    history_path = (args.data_dir / "tables" / "game_history.parquet").as_posix()
    con = duckdb.connect()
    game_history = query_df(con, f"SELECT * FROM read_parquet('{history_path}')")
    con.close()

    baseline = baseline_predictions(game_history)
    write_parquet(baseline, args.data_dir / "tables" / "baseline_predictions.parquet")

    # The margin model (task 1.8), walk-forward, scored by the same SQL on the same games.
    model, _ = walk_forward(game_history)
    write_parquet(model, args.data_dir / "tables" / "model_predictions.parquet")
    predictions = pd.concat(
        [baseline[PREDICTION_COLUMNS], model[PREDICTION_COLUMNS]], ignore_index=True
    )

    outputs = {"baseline_fit": baseline_fit(game_history, baseline)}
    outputs.update(score(game_history, predictions, args.close_source))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in outputs.items():
        df.to_csv(args.out_dir / f"{name}.csv", index=False, float_format="%.4f")
        print(f"\n{name}")
        print(df.to_string(index=False) if len(df) else "(no rows)")
    print(f"\nwrote {len(outputs)} files to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
