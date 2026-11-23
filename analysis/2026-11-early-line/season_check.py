"""Extra check, added AFTER the read: does the underdog drift and the model's CLV hold over a
whole season? Uses CBBD's open and CBBD's later line (not a verified close) for 2024-25 and
2025-26, with ratings known at 18:00 ET the evening before. Rough: CBBD lines have no
timestamps and come from different books than ours. Not a pre-registered measure.
"""

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from shac.model.margin import FEATURE_SET, build_features, fit, training_seasons
from shac.tables.build import query_df
from shac.tables.history import build_game_history

TABLES = Path(sys.argv[1]) / "tables"
ET = "America/New_York"


def load(name):
    con = duckdb.connect()
    df = query_df(con, f"SELECT * FROM read_parquet('{(TABLES / name).as_posix()}.parquet')")
    con.close()
    return df


gh, games, results = load("game_history"), load("games"), load("results")
ratings, closing = load("ratings_daily"), load("closing_lines")
feat_tip = build_features(gh)

sub = games[games["season"].isin([2025, 2026])][["game_id", "tip_at"]].copy()
day = pd.to_datetime(sub["tip_at"].dt.tz_convert(ET).dt.date) - pd.Timedelta(days=1)
sub["call_at"] = (day + pd.Timedelta(hours=18)).dt.tz_localize(ET).dt.tz_convert("UTC")
g = games.merge(sub[["game_id", "call_at"]], on="game_id")
g["tip_at"] = g["call_at"]
feat_eve = build_features(build_game_history(g.drop(columns="call_at"), results, ratings, closing))

rows = []
for season in [2025, 2026]:
    mf = fit(feat_tip, training_seasons(season), FEATURE_SET)
    f = feat_eve[feat_eve["season"] == season].copy()
    f["pred"] = mf.predict(f)
    d = f.merge(
        gh[["game_id", "tip_at", "cbbd_open_home_spread", "cbbd_close_home_spread"]], on="game_id"
    ).dropna(subset=["pred", "cbbd_open_home_spread", "cbbd_close_home_spread", "home_margin"])
    d["month"] = d["tip_at"].dt.tz_convert(ET).dt.month
    line, close = d["cbbd_open_home_spread"], d["cbbd_close_home_spread"]
    side = np.sign(d["pred"] + line)
    fav = -np.sign(line)
    d["clv_model"] = side * (line - close)
    d["clv_dog"] = np.where(fav != 0, -fav * (line - close), np.nan)
    d["model_dog"] = (side == -fav) & (fav != 0)
    d["ats_model"] = side * (d["home_margin"] + line)
    d["moved"] = close != line
    for month, m in [("all", d), *[(str(k), v) for k, v in d.groupby("month")]]:
        dec = m[m["ats_model"] != 0]
        rows.append(
            {
                "season": season,
                "month": month,
                "n": len(m),
                "share_moved": round(m["moved"].mean(), 3),
                "model_clv": round(m["clv_model"].mean(), 3),
                "model_clv_se": round(m["clv_model"].std(ddof=1) / np.sqrt(len(m)), 3),
                "always_dog_clv": round(m["clv_dog"].mean(), 3),
                "model_takes_dog": round(m["model_dog"].mean(), 3),
                "model_ats": round((dec["ats_model"] > 0).mean(), 3),
            }
        )
pd.set_option("display.width", 200)
print(pd.DataFrame(rows).to_string(index=False))
