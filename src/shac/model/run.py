"""Fit the margin model walk-forward and save its backtest predictions (task 1.8).

Run as:
    uv run python -m shac.model.run --data-dir <private repo>

Reads (private repo):  tables/game_history.parquet
Writes (private repo): tables/model_predictions.parquet (one row per game, scored seasons)

Prints:
1. The input comparison: each candidate input list's error against FINAL SCORES on the
   three train-only seasons, leave-one-season-out (shac.model.margin.compare_feature_sets).
2. The fitted weights for each scored season.

It prints nothing about the held-out seasons' accuracy and never reads a line. How the
model compares with the Torvik baseline and the closing line is the scorecard's job
(shac.scorecard.run), which stays switched off until task 1.2 fixes the backtest close.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.model.margin import (
    FEATURE_SET,
    build_features,
    compare_feature_sets,
    season_roles,
    walk_forward,
)
from shac.tables.build import query_df, write_parquet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    args = parser.parse_args(argv)

    history_path = (args.data_dir / "tables" / "game_history.parquet").as_posix()
    con = duckdb.connect()
    game_history = query_df(con, f"SELECT * FROM read_parquet('{history_path}')")
    con.close()

    roles = season_roles()
    train_only = [int(s) for s in roles.loc[roles["role"] == "train_only", "season"]]
    comparison = compare_feature_sets(build_features(game_history), train_only)
    print(f"\ninput comparison: error vs final scores, leave-one-season-out on {train_only}")
    print(comparison.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    predictions, fits = walk_forward(game_history)
    print(f"\nfitted weights (inputs: {FEATURE_SET})")
    weights = pd.DataFrame(
        [
            {
                "season": season,
                "train_seasons": " ".join(str(s) for s in f.train_seasons),
                "n_train_games": f.n_train_games,
                **f.weights_dict(),
            }
            for season, f in sorted(fits.items())
        ]
    )
    print(weights.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    out_path = args.data_dir / "tables" / "model_predictions.parquet"
    write_parquet(predictions, out_path)
    n_priced = int(predictions["pred_margin"].notna().sum())
    print(f"\nwrote {len(predictions)} rows ({n_priced} with a prediction) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
