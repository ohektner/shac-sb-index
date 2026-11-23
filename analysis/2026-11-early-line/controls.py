"""Controls for the early-line read. Added AFTER seeing the read; they don't change its verdict.

Is the model's CLV at the first-seen line just a market drift that any simple rule would
also catch? Compares the model with naive sides, and splits by how many books the
first-seen line came from.
"""

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path(sys.argv[1])
TABLES = Path(sys.argv[2]) / "tables"
_con = duckdb.connect()
d = _con.execute(
    f"SELECT * FROM read_parquet('{(OUT / 'private_games_first_seen.parquet').as_posix()}')"
).df()
_con.close()
d = d[d["close_snapshot_at"] > d["open_snapshot_at"]].copy()  # same rule as the read
line, close = d["open_home_spread"], d["close_home_spread"]


def clv_stats(side: pd.Series, name: str) -> dict:
    s = side[side != 0]
    c = (s * (line - close))[s.index]
    n = len(c)
    half = 1.96 * c.std(ddof=1) / np.sqrt(n)
    ats = s * (d.loc[s.index, "home_margin"] + line[s.index])
    w, l_ = int((ats > 0).sum()), int((ats < 0).sum())
    return {
        "side rule": name,
        "n": n,
        "clv_mean": round(c.mean(), 3),
        "ci_low": round(c.mean() - half, 3),
        "ci_high": round(c.mean() + half, 3),
        "ats": f"{w}-{l_}",
        "ats_pct": round(w / (w + l_), 3),
    }


model_side = np.sign(d["pred_margin_model"] + line)
fav_side = -np.sign(line)  # home when the home spread is negative
rng = np.random.default_rng(0)
rows = [
    clv_stats(model_side, "model v1"),
    clv_stats(np.sign(d["pred_torvik_baseline"] + line), "Torvik baseline"),
    clv_stats(pd.Series(1.0, index=d.index), "always home"),
    clv_stats(pd.Series(-1.0, index=d.index), "always away"),
    clv_stats(fav_side, "always favorite"),
    clv_stats(-fav_side, "always underdog"),
    clv_stats(pd.Series(rng.choice([-1.0, 1.0], len(d)), index=d.index), "coin flip"),
    clv_stats(model_side.where(model_side == fav_side, 0), "model, when it takes the favorite"),
    clv_stats(model_side.where(model_side == -fav_side, 0), "model, when it takes the underdog"),
]
print(pd.DataFrame(rows).to_string(index=False))

# How many US books made up the first-seen line?
con = duckdb.connect()
con.register("g", d[["game_id", "open_snapshot_at"]])
from shac.tables.build import US_BOOKS  # noqa: E402

con.register("us", pd.DataFrame({"book": US_BOOKS}))
nb = con.execute(
    f"""
SELECT g.game_id, count(DISTINCT s.book) AS n_books
FROM g JOIN read_parquet('{(TABLES / "odds_snapshots.parquet").as_posix()}') s
  ON s.game_id = g.game_id AND s.snapshot_at = g.open_snapshot_at
WHERE s.home_spread IS NOT NULL AND s.book IN (SELECT book FROM us)
GROUP BY 1
"""
).df()
d = d.merge(nb, on="game_id", how="left")
line, close = d["open_home_spread"], d["close_home_spread"]
model_side = np.sign(d["pred_margin_model"] + line)
print("\nbooks in the first-seen line:", d["n_books"].describe()[["min", "50%", "max"]].to_dict())
for lo, hi, name in [(1, 2, "1-2 books"), (3, 5, "3-5 books"), (6, 99, "6+ books")]:
    mask = d["n_books"].between(lo, hi)
    s = model_side[mask]
    c = s * (line[mask] - close[mask])
    print(f"model CLV, first-seen line from {name}: n={mask.sum()}  mean={c.mean():.3f}")

move = -(close - line)  # implied-margin move toward home
print(f"\nmean line move toward home: {move.mean():.3f} (n={len(move)})")
fav = -np.sign(line)
print(f"mean move toward the opening favorite: {(fav * move)[fav != 0].mean():.3f}")
