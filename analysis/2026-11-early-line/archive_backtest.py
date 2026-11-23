"""Early-line backtest on the bought archive (docs/EARLY_LINE.md, section 4).

Run from the repo, after shac.tables.build, shac.ingest.odds_history and
shac.tables.odds_history:

    uv run python <this file> <private data repo> <output folder>

Order, as the spec fixes it: build per-game lines, run check 1 (coverage) and check 2
(agreement with CBBD), and stop there if either fails. Only then compute the measures,
the curve and the verdict. Writes summary CSVs; prints counts and summaries only.
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
ET = "America/New_York"
ARCHIVE_SEASONS = [2023, 2024, 2025, 2026]
SCORED_SEASONS = [2025, 2026]
LIVE = 2027
COVERAGE_BAR = 0.90
AGREE_MEDIAN_BAR = 0.5
AGREE_FLIP_BAR = 0.01
Z = 1.96
GROUPS = [(0.50, 0.52), (0.52, 0.54), (0.54, 0.56), (0.56, 0.58), (0.58, 0.60), (0.60, 1.01)]
GROUP_NAMES = ["50-52%", "52-54%", "54-56%", "56-58%", "58-60%", "60%+"]

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
OUT.mkdir(parents=True, exist_ok=True)


def load(name: str) -> pd.DataFrame:
    """A private table, with every timestamp column in UTC at one resolution.

    DuckDB hands timestamps back in the machine's time zone; joins on time need one form.
    """
    con = duckdb.connect()
    df = query_df(con, f"SELECT * FROM read_parquet('{(TABLES / name).as_posix()}.parquet')")
    con.close()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    return df


games, results = load("games"), load("results")
ratings, closing, gh = load("ratings_daily"), load("closing_lines"), load("game_history")
hist, reqs = load("odds_history"), load("odds_history_requests")
reqs["game_day"] = pd.to_datetime(reqs["game_day"]).dt.date

# ---------------------------------------------------------------- per-game lines
fg = games[games["season"].isin(ARCHIVE_SEASONS) & (games["status"] == "final")].copy()
fg["game_day"] = fg["tip_at"].dt.tz_convert(ET).dt.date
fg = fg.merge(results[["game_id", "home_margin"]], on="game_id")

h = hist[hist["game_id"].notna() & hist["book"].isin(US_BOOKS) & hist["home_spread"].notna()]
h = h.drop_duplicates(["game_id", "snapshot_at", "book"])
h = h.assign(game_id=h["game_id"].astype("int64"))
snap_lines = (
    h.groupby(["game_id", "snapshot_at"])
    .agg(line=("home_spread", "median"), n_books=("book", "nunique"))
    .reset_index()
)
# Tip: the feed's latest commence time, capped at CBBD's scheduled tip when it is real.
feed_tip = (
    hist[hist["game_id"].notna()]
    .sort_values("snapshot_at")
    .drop_duplicates("game_id", keep="last")[["game_id", "commence_at"]]
)
feed_tip["game_id"] = feed_tip["game_id"].astype("int64")
fg = fg.merge(feed_tip, on="game_id", how="left")
cbbd_real = ~fg["tip_tbd"].fillna(False).astype(bool)
fg["tip"] = fg["commence_at"]
use_cbbd = cbbd_real & (fg["commence_at"].isna() | (fg["tip_at"] < fg["commence_at"]))
fg.loc[use_cbbd, "tip"] = fg.loc[use_cbbd, "tip_at"]

evening = reqs[reqs["kind"] == "evening"][["game_day", "snapshot_at"]].rename(
    columns={"snapshot_at": "call_at"}
)
fg = fg.merge(evening, on="game_day", how="left")
call = snap_lines.rename(columns={"snapshot_at": "call_at", "line": "call_line"})
fg = fg.merge(call, on=["game_id", "call_at"], how="left")

pre_tip = snap_lines.merge(fg[["game_id", "tip"]], on="game_id")
pre_tip = pre_tip[pre_tip["snapshot_at"] < pre_tip["tip"]]
close = pre_tip.loc[pre_tip.groupby("game_id")["snapshot_at"].idxmax()][
    ["game_id", "snapshot_at", "line"]
].rename(columns={"snapshot_at": "close_at", "line": "close_line"})
fg = fg.merge(close, on="game_id", how="left")
fg["minutes_before_tip"] = (fg["tip"] - fg["close_at"]).dt.total_seconds() / 60

# ---------------------------------------------------------------- check 1: coverage
cov = fg.groupby("season").agg(
    final_games=("game_id", "size"), with_call_line=("call_line", lambda s: s.notna().sum())
)
cov["share"] = cov["with_call_line"] / cov["final_games"]
cov["passes"] = cov["share"] >= COVERAGE_BAR
print("CHECK 1, coverage")
print(cov.to_string())

# ---------------------------------------------------------------- check 2: agreement with CBBD
ag = fg.merge(gh[["game_id", "cbbd_close_home_spread"]], on="game_id")
ag = ag.dropna(subset=["close_line", "cbbd_close_home_spread"])
diff = (ag["close_line"] - ag["cbbd_close_home_spread"]).abs()
both_big = (ag["close_line"].abs() >= 3) & (ag["cbbd_close_home_spread"].abs() >= 3)
flipped = both_big & (np.sign(ag["close_line"]) != np.sign(ag["cbbd_close_home_spread"]))
agree = {
    "n_games": len(ag),
    "median_abs_diff": float(diff.median()),
    "mean_abs_diff": float(diff.mean()),
    "share_opposite_favorites": float(flipped.mean()),
}
agree["passes"] = (
    agree["median_abs_diff"] <= AGREE_MEDIAN_BAR
    and agree["share_opposite_favorites"] <= AGREE_FLIP_BAR
)
print("\nCHECK 2, agreement with CBBD")
print(agree)

cov.to_csv(OUT / "check1_coverage.csv")
pd.Series(agree).to_csv(OUT / "check2_agreement.csv", header=["value"])
# Owen, 2026-11-23 (DECISIONS.md): check 1 failed on timing (lines posted only on game
# day), not matching; proceed as written. Check 2 must still pass.
CHECK1_OVERRIDE = "Owen 2026-11-23: check 1 failed on timing; proceed as written"
if not agree["passes"]:
    print("\nSTOP: check 2 failed. No result is computed (EARLY_LINE.md section 4).")
    sys.exit(2)
if not cov["passes"].all():
    print(f"\nCheck 1 failed; proceeding by decision ({CHECK1_OVERRIDE}).")
print("\nComputing the measures.")


# ---------------------------------------------------------------- features and predictions
def features_at(call_times: pd.DataFrame) -> pd.DataFrame:
    """build_features with ratings known before each game's call time."""
    g = games.merge(call_times[["game_id", "call_at"]], on="game_id", how="inner")
    g["tip_at"] = g["call_at"]
    return build_features(build_game_history(g.drop(columns="call_at"), results, ratings, closing))


feat_tip = build_features(gh)
called = fg.dropna(subset=["call_line"])
feat_call = features_at(called[["game_id", "call_at"]])

preds = []
for season in ARCHIVE_SEASONS:
    train = training_seasons(season)
    mfit = fit(feat_tip, train, FEATURE_SET)
    t = feat_tip[
        feat_tip["season"].isin(train)
        & (feat_tip["home"] == 1)
        & feat_tip["home_margin"].notna()
        & feat_tip["rating_margin"].notna()
    ]
    home_court = float((t["home_margin"] - t["rating_margin"]).mean())
    f = feat_call[feat_call["season"] == season]
    preds.append(
        pd.DataFrame(
            {
                "game_id": f["game_id"],
                "pred_torvik_baseline": f["rating_margin"] + home_court * f["home"],
                "pred_margin_model": mfit.predict(f),
            }
        )
    )
d = called.merge(pd.concat(preds), on="game_id")
d = d.dropna(subset=["pred_torvik_baseline", "pred_margin_model"]).reset_index(drop=True)
d["month"] = d["tip"].dt.tz_convert(ET).dt.month
d["period"] = np.where(d["month"].isin([11, 12]), "Nov-Dec", "Jan-Apr")
d["books"] = pd.cut(d["n_books"], [0, 2, 5, 99], labels=["1-2", "3-5", "6+"])
d["clv_ok"] = d["close_at"].notna() & (d["close_at"] > d["call_at"])
fav = -np.sign(d["call_line"])  # +1 home favored at the call line, 0 pick'em


def clv_of(side: pd.Series) -> pd.Series:
    """CLV in points for a side (+1 home, -1 away); empty where not defined."""
    ok = d["clv_ok"] & (side != 0)
    return (side * (d["call_line"] - d["close_line"])).where(ok)


def mean_ci(x: pd.Series) -> tuple[float, float, float, int]:
    x = x.dropna()
    n = len(x)
    if n < 2:
        return (np.nan, np.nan, np.nan, n)
    half = Z * x.std(ddof=1) / np.sqrt(n)
    return (x.mean(), x.mean() - half, x.mean() + half, n)


def wilson(w: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p = w / n
    den = 1 + Z**2 / n
    mid = (p + Z**2 / (2 * n)) / den
    half = Z * np.sqrt(p * (1 - p) / n + Z**2 / (4 * n**2)) / den
    return (mid - half, mid + half)


sides = {p: np.sign(d[f"pred_{p}"] + d["call_line"]) for p in PREDICTORS}
rng = np.random.default_rng(0)
rules = {
    **{p: sides[p] for p in PREDICTORS},
    "always_underdog": -fav,
    "always_home": pd.Series(1.0, index=d.index),
    "coin_flip": pd.Series(rng.choice([-1.0, 1.0], len(d)), index=d.index),
}
clv = {name: clv_of(side) for name, side in rules.items()}
ats = {name: side * (d["home_margin"] + d["call_line"]) for name, side in rules.items()}

scopes = [(str(s), d["season"] == s) for s in ARCHIVE_SEASONS] + [
    ("pooled", pd.Series(True, index=d.index))
]
rows = []
for scope, mask in scopes:
    for name in rules:
        m, lo, hi, n = mean_ci(clv[name][mask])
        a = ats[name][mask & (rules[name] != 0)]
        w, lo_ = int((a > 0).sum()), int((a < 0).sum())
        rows.append(
            {
                "scope": scope,
                "rule": name,
                "clv_n": n,
                "clv_mean": m,
                "clv_ci_low": lo,
                "clv_ci_high": hi,
                "clv_share_positive": (clv[name][mask].dropna() > 0).mean(),
                "ats_w": w,
                "ats_l": lo_,
                "ats_p": int((a == 0).sum()),
                "ats_win_pct": w / (w + lo_) if w + lo_ else np.nan,
            }
        )
summary = pd.DataFrame(rows)

paired_rows = []
for scope, mask in scopes:
    for p in PREDICTORS:
        diff_ = (clv[p] - clv["always_underdog"])[mask & (fav != 0)]
        m, lo, hi, n = mean_ci(diff_)
        paired_rows.append(
            {"scope": scope, "predictor": p, "n": n, "mean": m, "ci_low": lo, "ci_high": hi}
        )
paired = pd.DataFrame(paired_rows)

split_rows = []
for col in ["period", "books"]:
    for val, grp in d.groupby(col, observed=True):
        for p in [*PREDICTORS, "always_underdog"]:
            m, lo, hi, n = mean_ci(clv[p][grp.index])
            split_rows.append(
                {
                    "split": col,
                    "value": val,
                    "rule": p,
                    "n": n,
                    "clv_mean": m,
                    "ci_low": lo,
                    "ci_high": hi,
                }
            )
splits = pd.DataFrame(split_rows)
mbt = d.loc[d["clv_ok"], "minutes_before_tip"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9])
no_clv = int((~d["clv_ok"]).sum())

# ---------------------------------------------------------------- curve and tiers
all_feat = feat_tip[~feat_tip["game_id"].isin(feat_call["game_id"])]
cal_features = pd.concat([all_feat, feat_call], ignore_index=True)
cal_lines = called[["game_id", "call_line"]].rename(columns={"call_line": "close_home_spread"})
fits, _ = calibrate_all(cal_features, cal_lines, [*SCORED_SEASONS, LIVE])
fits = fits[FIT_COLUMNS]
slope = {(r.predictor, int(r.scored_season)): r.slope_b for r in fits.itertuples()}

rel_rows, tier_rows = [], []
for p in PREDICTORS:
    edge = d[f"pred_{p}"] + d["call_line"]
    b = d["season"].map(lambda s, p=p: slope.get((p, int(s)), np.nan))
    prob = 1 / (1 + np.exp(-b * edge.abs()))
    prob = prob.where((edge != 0) & d["season"].isin(SCORED_SEASONS))
    tier = prob.map(lambda x: tier_for(x) if pd.notna(x) else None)
    res = ats[p]
    for scope, mask in [(str(s), d["season"] == s) for s in SCORED_SEASONS] + [
        ("pooled", d["season"].isin(SCORED_SEASONS))
    ]:
        decided = mask & prob.notna() & (res != 0)
        for (lo, hi), name in zip(GROUPS, GROUP_NAMES, strict=True):
            g = decided & (prob >= lo) & (prob < hi)
            rel_rows.append(
                {
                    "scope": scope,
                    "predictor": p,
                    "group": name,
                    "n": int(g.sum()),
                    "wins": int((res[g] > 0).sum()),
                    "predicted": prob[g].mean(),
                    "actual": (res[g] > 0).mean() if g.any() else np.nan,
                }
            )
        for t in ["pick", "lean"]:
            g = mask & (tier == t)
            w, l_ = int((res[g] > 0).sum()), int((res[g] < 0).sum())
            wl, wh = wilson(w, w + l_)
            pct = w / (w + l_) if w + l_ else np.nan
            tier_rows.append(
                {
                    "scope": scope,
                    "predictor": p,
                    "tier": t,
                    "calls": int(g.sum()),
                    "wins": w,
                    "losses": l_,
                    "pushes": int((res[g] == 0).sum()),
                    "win_pct": pct,
                    "wilson_low": wl,
                    "wilson_high": wh,
                    "gap_to_52_4": pct - 0.524 if w + l_ else np.nan,
                    "mean_clv": clv[p][g].mean(),
                }
            )
reliability, tiers = pd.DataFrame(rel_rows), pd.DataFrame(tier_rows)

# ---------------------------------------------------------------- verdict (fixed in the spec)
pm = summary[(summary["scope"] == "pooled") & (summary["rule"] == "margin_model")].iloc[0]
pp = paired[(paired["scope"] == "pooled") & (paired["predictor"] == "margin_model")].iloc[0]
if pm["clv_ci_low"] > 0 and pp["ci_low"] > 0:
    verdict = "v1 beats early lines"
elif pm["clv_ci_low"] > 0:
    verdict = "only the drift"
else:
    verdict = "no"

for name, df in [
    ("clv_ats_summary", summary),
    ("paired_vs_underdog", paired),
    ("splits", splits),
    ("curve_fit", fits),
    ("reliability", reliability),
    ("tiers", tiers),
]:
    df.to_csv(OUT / f"{name}.csv", index=False, float_format="%.4f")
counts = {
    "final_games": len(fg),
    "with_call_line": int(fg["call_line"].notna().sum()),
    "priced": len(d),
    "clv_left_out_no_later_snapshot": no_clv,
}
pd.Series(counts).to_csv(OUT / "counts.csv", header=["n"])

print("\ncounts", counts)
print("\nminutes from close snapshot to tip (games with CLV):")
print(mbt.round(0).to_string())
print("\nCLV and ATS")
print(summary.to_string(index=False))
print("\npaired: model CLV minus always-underdog CLV")
print(paired.to_string(index=False))
print("\nsplits")
print(splits.to_string(index=False))
print("\ncurve fit (edges vs the call line)")
print(fits.to_string(index=False))
print("\nreliability (n > 0)")
print(reliability[reliability["n"] > 0].to_string(index=False))
print("\ntiers")
print(tiers.to_string(index=False))
print(f"\nVERDICT (EARLY_LINE.md section 4 reading): {verdict}")

# Per-game rows for the G2 ceiling test (PLAYER_IMPACT.md section 9). Private: lines.
from shac.tables.build import write_parquet  # noqa: E402

keep = [
    "game_id",
    "season",
    "home_team_id",
    "away_team_id",
    "home_margin",
    "call_line",
    "close_line",
    "clv_ok",
    "n_books",
    "pred_torvik_baseline",
    "pred_margin_model",
]
write_parquet(d[keep].reset_index(drop=True), OUT / "private_archive_games.parquet")
