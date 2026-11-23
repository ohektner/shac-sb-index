"""G2 ceiling test: perfect knowledge of new absences (docs/PLAYER_IMPACT.md section 9).

Run from the repo, after archive_backtest.py has written private_archive_games.parquet:

    uv run python <this file> <private data repo> <output folder>

Hindsight on purpose: S_true uses who actually sat. v is fit walk-forward (section 2 on
training_seasons(S)), so it never sees the season it adjusts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from shac.model.margin import build_features, leave_one_season_out, training_seasons
from shac.tables.build import query_df

DATA = Path(sys.argv[1])
OUT = Path(sys.argv[2])
TABLES = DATA / "tables"
SEASONS = [2023, 2024, 2025, 2026]
Z = 1.96
BIG = 2.0

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)


def load_sql(sql: str) -> pd.DataFrame:
    con = duckdb.connect()
    df = query_df(con, sql)
    con.close()
    return df


def table(name: str) -> str:
    return f"read_parquet('{(TABLES / name).as_posix()}.parquet')"


games = load_sql(f"SELECT * FROM {table('game_history')}")
rot = load_sql(
    f"SELECT game_id, team_id, is_rotation, games_sat_in_a_row_before, played, mpg_before "
    f"FROM {table('rotation_players')}"
)
boxes = load_sql(f"SELECT DISTINCT game_id, team_id FROM {table('player_box')}")
arch = load_sql(
    f"SELECT * FROM read_parquet('{(OUT / 'private_archive_games.parquet').as_posix()}')"
)

# ---------------------------------------------------------------- S_true per team-game
cand = rot[rot["is_rotation"] & (rot["games_sat_in_a_row_before"] == 0)].copy()
cand["w"] = np.minimum(cand["mpg_before"] / 40.0, 1.0)
sat = cand[~cand["played"].astype(bool)]
s_true = sat.groupby(["game_id", "team_id"])["w"].sum().rename("s").reset_index()
team_games = boxes.merge(s_true, on=["game_id", "team_id"], how="left").fillna({"s": 0.0})


def load_d(frame: pd.DataFrame) -> pd.DataFrame:
    """Add S_home, S_away and D; drop games without both teams' boxes (counted)."""
    h = team_games.rename(columns={"team_id": "home_team_id", "s": "s_home"})
    a = team_games.rename(columns={"team_id": "away_team_id", "s": "s_away"})
    out = frame.merge(h, on=["game_id", "home_team_id"], how="left").merge(
        a, on=["game_id", "away_team_id"], how="left"
    )
    out["has_boxes"] = out["s_home"].notna() & out["s_away"].notna()
    out["D"] = out["s_home"] - out["s_away"]
    return out


# ---------------------------------------------------------------- v, walk-forward (section 2)
feat = build_features(games)
teams = games[["game_id", "home_team_id", "away_team_id"]]
v_rows, v_by_season = [], {}
stop = []
for season in SEASONS:
    fit_seasons = training_seasons(season)
    loso = leave_one_season_out(feat, fit_seasons)
    g = load_d(loso.merge(teams, on="game_id"))
    dropped_no_box = int((~g["has_boxes"]).sum())
    g = g[g["has_boxes"] & g["home_margin"].notna() & g["pred_margin"].notna()]
    r = g["home_margin"] - g["pred_margin"]
    D = g["D"]
    nz = D != 0
    v = float(-(D * r).sum() / (D * D).sum())
    e = (r + v * D)[nz]
    n = int(nz.sum())
    se = float(np.sqrt((e * e).sum() / (n - 1) / (D * D).sum()))
    per = {}
    for s in [x for x in fit_seasons if x in (2019, 2020, 2022)]:
        gs = g[g["season"] == s]
        rs, Ds = gs["home_margin"] - gs["pred_margin"], gs["D"]
        per[s] = float(-(Ds * rs).sum() / (Ds * Ds).sum())
    v_by_season[season] = v
    v_rows.append(
        {
            "season": season,
            "fit_seasons": " ".join(map(str, fit_seasons)),
            "v": v,
            "se_v": se,
            "v_over_se": v / se,
            "n_games_D_nonzero": n,
            "n_fit_games": len(g),
            "dropped_no_box": dropped_no_box,
            "keep_rule_v_2019_2020_2022": per,
        }
    )
    if not (0 <= v <= 12) or n < 2000:
        stop.append(season)
vfit = pd.DataFrame(v_rows)
print("v, walk-forward (section 2)")
print(vfit.to_string(index=False))
vfit.to_csv(OUT / "g2_v_fit.csv", index=False, float_format="%.4f")
if stop:
    print(f"\nSTOP: section 2 stop condition hit for {stop}. No result is computed.")
    sys.exit(2)

# ---------------------------------------------------------------- the ceiling
a = load_d(arch)
n_no_box = int((~a["has_boxes"]).sum())
a = a[a["has_boxes"]].reset_index(drop=True)
a["v"] = a["season"].map(v_by_season)
a["adj"] = -a["v"] * a["D"]
for p in ["margin_model", "torvik_baseline"]:
    a[f"oracle_{p}"] = a[f"pred_{p}"] + a["adj"]


def side(margin: pd.Series, line: pd.Series) -> pd.Series:
    return np.sign(margin + line)


def clv(s: pd.Series) -> pd.Series:
    return (s * (a["call_line"] - a["close_line"])).where(a["clv_ok"] & (s != 0))


def mean_ci(x: pd.Series):
    x = x.dropna()
    n = len(x)
    if n < 2:
        return np.nan, np.nan, np.nan, n
    half = Z * x.std(ddof=1) / np.sqrt(n)
    return x.mean(), x.mean() - half, x.mean() + half, n


def ats(s: pd.Series, line: pd.Series, mask: pd.Series) -> tuple[int, int, float]:
    res = (s * (a["home_margin"] + line))[mask & (s != 0)]
    w, l_ = int((res > 0).sum()), int((res < 0).sum())
    return w, l_, (w / (w + l_) if w + l_ else np.nan)


subsets = {
    "all games": pd.Series(True, index=a.index),
    "absence games (D != 0)": a["D"] != 0,
    "big-absence games (|v*D| >= 2)": a["adj"].abs() >= BIG,
}
rows = []
for p in ["margin_model", "torvik_baseline"]:
    s_base = side(a[f"pred_{p}"], a["call_line"])
    s_orc = side(a[f"oracle_{p}"], a["call_line"])
    s_orc_close = side(a[f"oracle_{p}"], a["close_line"])
    c_base, c_orc = clv(s_base), clv(s_orc)
    for sub, smask in subsets.items():
        for scope in [*SEASONS, "pooled"]:
            mask = smask & (a["season"] == scope if scope != "pooled" else True)
            mb, lb, hb, nb = mean_ci(c_base[mask])
            mo, lo, ho, no = mean_ci(c_orc[mask])
            md, ld, hd, nd = mean_ci((c_orc - c_base)[mask])
            wb, lb_, pb = ats(s_base, a["call_line"], mask)
            wo, lo_, po = ats(s_orc, a["call_line"], mask)
            wc, lc, pc = ats(s_orc_close, a["close_line"], mask & a["close_line"].notna())
            rows.append(
                {
                    "predictor": p,
                    "subset": sub,
                    "scope": scope,
                    "n_games": int(mask.sum()),
                    "n_side_differs": int((mask & (s_base != s_orc)).sum()),
                    "base_clv": mb,
                    "base_ci": f"{lb:.3f} to {hb:.3f}",
                    "oracle_clv": mo,
                    "oracle_ci_low": lo,
                    "oracle_ci_high": ho,
                    "paired_gain": md,
                    "paired_ci_low": ld,
                    "paired_ci_high": hd,
                    "clv_n": no,
                    "base_ats_pct": pb,
                    "oracle_ats_pct": po,
                    "oracle_ats_vs_close_pct": pc,
                    "oracle_ats_record": f"{wo}-{lo_}",
                }
            )
res = pd.DataFrame(rows)
res.to_csv(OUT / "g2_ceiling.csv", index=False, float_format="%.4f")

key = res[
    (res["predictor"] == "margin_model")
    & (res["subset"] == "absence games (D != 0)")
    & (res["scope"] == "pooled")
].iloc[0]
if key["oracle_ci_low"] > 0 and key["paired_ci_low"] > 0:
    verdict = "News can beat the night-before line"
else:
    verdict = "The line already prices it"

print(f"\narchive games left out for a missing box score: {n_no_box}")
print(
    f"absence games: {int((a['D'] != 0).sum())} of {len(a)}; "
    f"mean |adj| on them: {a.loc[a['D'] != 0, 'adj'].abs().mean():.2f} points"
)
cols = [
    "predictor",
    "subset",
    "scope",
    "n_games",
    "n_side_differs",
    "base_clv",
    "oracle_clv",
    "oracle_ci_low",
    "oracle_ci_high",
    "paired_gain",
    "paired_ci_low",
    "paired_ci_high",
    "base_ats_pct",
    "oracle_ats_pct",
    "oracle_ats_vs_close_pct",
    "oracle_ats_record",
]
print(res[cols].to_string(index=False))
print(f"\nVERDICT (PLAYER_IMPACT.md section 9 reading): {verdict}")
