"""First early-line read on captured 2026-27 games (docs/EARLY_LINE.md, section 3).

Shadow pricing, not a live record: no call was committed before tip. Run from the repo:

    uv run python <this file> <private data repo> <output folder>

Reads the private tables only; writes summary CSVs to the output folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from shac.model.calibration import FIT_COLUMNS, PREDICTORS, calibrate_all, tier_for
from shac.model.margin import FEATURE_SET, build_features, fit, training_seasons
from shac.tables.build import US_BOOKS, query_df
from shac.tables.history import build_game_history

DATA = Path(sys.argv[1])
OUT = Path(sys.argv[2])
TABLES = DATA / "tables"
LIVE = 2027
START = pd.Timestamp("2026-11-14T00:00:00Z")
ET = "America/New_York"
GROUPS = [(0.50, 0.52), (0.52, 0.54), (0.54, 0.56), (0.56, 0.58), (0.58, 0.60), (0.60, 1.01)]
GROUP_NAMES = ["50-52%", "52-54%", "54-56%", "56-58%", "58-60%", "60%+"]

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)


def load(name: str) -> pd.DataFrame:
    con = duckdb.connect()
    df = query_df(con, f"SELECT * FROM read_parquet('{(TABLES / name).as_posix()}.parquet')")
    con.close()
    return df


gh = load("game_history")
games = load("games")
results = load("results")
ratings = load("ratings_daily")
closing = load("closing_lines")
snaps = load("odds_snapshots")


def features_at(call: pd.DataFrame) -> pd.DataFrame:
    """build_features with ratings known before each game's call time.

    The game_history rule (latest file with as_of before tip, both teams from it) is run
    with call_at in place of tip_at.
    """
    g = games.merge(call[["game_id", "call_at"]], on="game_id", how="inner")
    g["tip_at"] = g["call_at"]
    g = g.drop(columns="call_at")
    hist = build_game_history(g, results, ratings, closing)
    return build_features(hist)


# ---------- the two predictors for 2026-27 (trained on tip-time history) ----------
feat_tip = build_features(gh)
train = training_seasons(LIVE)
model_fit = fit(feat_tip, train, FEATURE_SET)
t = feat_tip[
    feat_tip["season"].isin(train)
    & (feat_tip["home"] == 1)
    & feat_tip["home_margin"].notna()
    & feat_tip["rating_margin"].notna()
]
HOME_COURT = float((t["home_margin"] - t["rating_margin"]).mean())


def predict(feat: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": feat["game_id"],
            "pred_torvik_baseline": feat["rating_margin"] + HOME_COURT * feat["home"],
            "pred_margin_model": model_fit.predict(feat),
        }
    )


# ---------- the curve (section 2): edges vs CBBD's open, ratings at 18:00 ET the evening before
eve = games[["game_id", "tip_at"]].copy()
eve_day = pd.to_datetime(eve["tip_at"].dt.tz_convert(ET).dt.date) - pd.Timedelta(days=1)
eve["call_at"] = (eve_day + pd.Timedelta(hours=18)).dt.tz_localize(ET).dt.tz_convert("UTC")
feat_eve = features_at(eve)
lines_open = gh[["game_id", "cbbd_open_home_spread"]].rename(
    columns={"cbbd_open_home_spread": "close_home_spread"}
)
fits, _ = calibrate_all(feat_eve, lines_open, [LIVE])
fits = fits[FIT_COLUMNS]
slope = {r.predictor: r.slope_b for r in fits.itertuples()}

# ---------- captured games (section 3) ----------
own = closing[closing["source"] == "own_snapshots"][
    ["game_id", "open_home_spread", "open_snapshot_at", "close_home_spread", "close_snapshot_at"]
]
pin = closing[closing["source"] == "pinnacle"][
    ["game_id", "close_home_spread", "close_snapshot_at"]
].rename(columns={"close_home_spread": "pin_close", "close_snapshot_at": "pin_close_at"})
cand = (
    games[(games["season"] == LIVE) & (games["status"] == "final") & (games["tip_at"] >= START)]
    .merge(own, on="game_id")
    .merge(results[["game_id", "home_margin"]], on="game_id")
    .merge(pin, on="game_id", how="left")
)
counts = {"captured_final_with_own_open_and_close": len(cand)}
cand = cand[cand["neutral_site"].notna()]
counts["neutral_site_known"] = len(cand)

# morning-run line: latest US-book snapshot before 14:37 UTC on the tip's Eastern date
us = snaps[snaps["book"].isin(US_BOOKS) & snaps["home_spread"].notna() & snaps["game_id"].notna()]
per = us.groupby(["game_id", "snapshot_at"], as_index=False)["home_spread"].median()
m = per.merge(cand[["game_id", "tip_at"]], on="game_id")
run_at = (
    pd.to_datetime(m["tip_at"].dt.tz_convert(ET).dt.date) + pd.Timedelta(hours=14, minutes=37)
).dt.tz_localize("UTC")
m = m[(m["snapshot_at"] < run_at) & (m["snapshot_at"] < m["tip_at"])]
m = m.loc[m.groupby("game_id")["snapshot_at"].idxmax()]
morning = m.rename(columns={"snapshot_at": "morning_snap_at", "home_spread": "morning_line"})
cand = cand.merge(morning[["game_id", "morning_snap_at", "morning_line"]], on="game_id", how="left")
cand["morning_call_at"] = (
    pd.to_datetime(cand["tip_at"].dt.tz_convert(ET).dt.date) + pd.Timedelta(hours=14, minutes=37)
).dt.tz_localize("UTC")


def priced(call_col: str) -> pd.DataFrame:
    """Candidates with both predictions at that call time."""
    call = cand[["game_id", call_col]].rename(columns={call_col: "call_at"}).dropna()
    preds = predict(features_at(call))
    out = cand.merge(preds, on="game_id")
    return out.dropna(subset=["pred_torvik_baseline", "pred_margin_model"])


def summarize(df: pd.DataFrame, line_col: str, snap_col: str, label: str):
    rows, groups, tiers = [], [], []
    for p in PREDICTORS:
        d = df.dropna(subset=[line_col]).copy()
        d["edge"] = d[f"pred_{p}"] + d[line_col]  # prediction minus the line's implied margin
        d["side"] = np.sign(d["edge"])
        no_side = int((d["side"] == 0).sum())
        d = d[d["side"] != 0]
        ok_own = d["close_snapshot_at"] > d[snap_col]
        clv = (d["side"] * (d[line_col] - d["close_home_spread"]))[ok_own]
        ok_pin = d["pin_close"].notna() & (d["pin_close_at"] > d[snap_col])
        clv_pin = (d["side"] * (d[line_col] - d["pin_close"]))[ok_pin]
        ats = d["side"] * (d["home_margin"] + d[line_col])
        b = slope[p]
        d["cover_prob"] = 1 / (1 + np.exp(-b * d["edge"].abs()))
        d["tier"] = d["cover_prob"].map(tier_for)
        d["clv"] = np.where(ok_own, d["side"] * (d[line_col] - d["close_home_spread"]), np.nan)
        d["result"] = np.select([ats > 0, ats < 0], ["win", "loss"], "push")

        def ci(x):
            n = len(x)
            if n < 2:
                return (np.nan, np.nan)
            half = 1.96 * x.std(ddof=1) / np.sqrt(n)
            return (x.mean() - half, x.mean() + half)

        lo, hi = ci(clv)
        plo, phi = ci(clv_pin)
        w, lsum, pu = int((ats > 0).sum()), int((ats < 0).sum()), int((ats == 0).sum())
        rows.append(
            {
                "line": label,
                "predictor": p,
                "n_games": len(d),
                "n_no_side": no_side,
                "clv_n": len(clv),
                "clv_left_out_same_snapshot": int((~ok_own).sum()),
                "clv_mean": clv.mean(),
                "clv_ci_low": lo,
                "clv_ci_high": hi,
                "clv_share_positive": (clv > 0).mean(),
                "clv_share_zero": (clv == 0).mean(),
                "pin_clv_n": len(clv_pin),
                "pin_clv_mean": clv_pin.mean(),
                "pin_clv_ci_low": plo,
                "pin_clv_ci_high": phi,
                "ats_w": w,
                "ats_l": lsum,
                "ats_p": pu,
                "ats_win_pct": w / (w + lsum) if w + lsum else np.nan,
                "mean_abs_edge": d["edge"].abs().mean(),
            }
        )
        decided = d[d["result"] != "push"]
        for (lo_g, hi_g), name in zip(GROUPS, GROUP_NAMES, strict=True):
            g = decided[(decided["cover_prob"] >= lo_g) & (decided["cover_prob"] < hi_g)]
            groups.append(
                {
                    "line": label,
                    "predictor": p,
                    "group": name,
                    "n_games": len(g),
                    "n_wins": int((g["result"] == "win").sum()),
                    "predicted": g["cover_prob"].mean(),
                    "actual": (g["result"] == "win").mean() if len(g) else np.nan,
                }
            )
        for tier in ["pick", "lean"]:
            g = d[d["tier"] == tier]
            gw, gl = int((g["result"] == "win").sum()), int((g["result"] == "loss").sum())
            tiers.append(
                {
                    "line": label,
                    "predictor": p,
                    "tier": tier,
                    "n_calls": len(g),
                    "wins": gw,
                    "losses": gl,
                    "pushes": int((g["result"] == "push").sum()),
                    "win_pct": gw / (gw + gl) if gw + gl else np.nan,
                    "mean_clv": g["clv"].mean(),
                }
            )
    return rows, groups, tiers


first = priced("open_snapshot_at")
morn = priced("morning_call_at")
counts["priced_at_first_seen"] = len(first)
counts["priced_at_morning"] = len(morn)
counts["morning_no_line_yet"] = int(morn["morning_line"].isna().sum())

r1, g1, t1 = summarize(first, "open_home_spread", "open_snapshot_at", "primary: first-seen line")
r2, g2, t2 = summarize(morn, "morning_line", "morning_snap_at", "secondary: morning-run line")
summary = pd.DataFrame(r1 + r2)
groups = pd.DataFrame(g1 + g2)
tiers = pd.DataFrame(t1 + t2)

OUT.mkdir(parents=True, exist_ok=True)
fits.to_csv(OUT / "curve_fit_vs_open.csv", index=False, float_format="%.5f")
summary.to_csv(OUT / "clv_ats_summary.csv", index=False, float_format="%.4f")
groups.to_csv(OUT / "reliability.csv", index=False, float_format="%.4f")
tiers.to_csv(OUT / "tiers.csv", index=False, float_format="%.4f")
pd.Series(counts).to_csv(OUT / "counts.csv", header=["n"])

print(f"home court (baseline, 2027): {HOME_COURT:.3f}; model weights: {model_fit.weights_dict()}")
print("\ncurve fit vs CBBD open (2026-27 slope)")
print(fits.to_string(index=False))
print("\ncounts")
print(pd.Series(counts).to_string())
print("\nCLV and ATS")
print(summary.to_string(index=False))
print("\nreliability")
print(groups[groups["n_games"] > 0].to_string(index=False))
print("\ntiers")
print(tiers.to_string(index=False))
games_out = first[["game_id", "tip_at", "open_snapshot_at", "close_snapshot_at"]]
print("\ngames tip range:", games_out["tip_at"].min(), "to", games_out["tip_at"].max())

# Per-game rows for the controls (private: derived from raw lines; not for publishing).
keep = [
    "game_id",
    "tip_at",
    "neutral_site",
    "home_margin",
    "open_home_spread",
    "open_snapshot_at",
    "close_home_spread",
    "close_snapshot_at",
    "pin_close",
    "pin_close_at",
    "pred_torvik_baseline",
    "pred_margin_model",
]
from shac.tables.build import write_parquet  # noqa: E402

write_parquet(first[keep].reset_index(drop=True), OUT / "private_games_first_seen.parquet")
