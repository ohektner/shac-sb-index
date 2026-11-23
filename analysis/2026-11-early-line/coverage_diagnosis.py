"""Why do some final games have no call line? Lines and matching only; no model numbers."""

import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.tables.build import US_BOOKS, query_df

T = Path(sys.argv[1]) / "tables"
ET = "America/New_York"


def load(name):
    con = duckdb.connect()
    df = query_df(con, f"SELECT * FROM read_parquet('{(T / name).as_posix()}.parquet')")
    con.close()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    return df


games, hist = load("games"), load("odds_history")
matches, reqs = load("odds_history_matches"), load("odds_history_requests")
reqs["game_day"] = pd.to_datetime(reqs["game_day"]).dt.date
g = games[games["season"].isin([2023, 2024, 2025, 2026]) & (games["status"] == "final")].copy()
g["game_day"] = g["tip_at"].dt.tz_convert(ET).dt.date

h = hist[hist["game_id"].notna() & hist["book"].isin(US_BOOKS) & hist["home_spread"].notna()]
h = h.assign(game_id=h["game_id"].astype("int64"))
snaps = h[["game_id", "snapshot_at"]].drop_duplicates()
snaps = snaps.merge(reqs[["snapshot_at", "kind", "game_day"]], on="snapshot_at", how="left")
ev = reqs[reqs["kind"] == "evening"][["game_day", "snapshot_at"]]
has_call = g.merge(ev, on="game_day").merge(
    snaps[["game_id", "snapshot_at"]], on=["game_id", "snapshot_at"]
)
g["call"] = g["game_id"].isin(has_call["game_id"])
g["any_line"] = g["game_id"].isin(snaps["game_id"])
swapped = (
    matches.loc[matches["match_type"] == "swapped", "candidate_game_id"].dropna().astype("int64")
)
g["swapped"] = g["game_id"].isin(swapped)
g["neutral"] = g["neutral_site"].fillna(False).astype(bool)
g["month"] = g["tip_at"].dt.tz_convert(ET).dt.month


def reason(r):
    if r.call:
        return "has call line"
    if r.any_line:
        return "line only later (posted after 22:20 ET night before)"
    if r.swapped:
        return "feed lists home/away reversed (swapped)"
    return "no line in any pulled snapshot"


g["reason"] = g.apply(reason, axis=1)
pd.set_option("display.width", 300)
pd.set_option("display.max_columns", None)
print(pd.crosstab(g["season"], g["reason"], margins=True))
miss = g[~g["call"]]
print("\nmissing call line, neutral-site share by reason:")
print(miss.groupby("reason")["neutral"].mean().round(2).to_string())
print("\nmissing call line by month (all seasons):")
print(pd.crosstab(miss["month"], miss["reason"]))
print("\nif swapped events were included (home spread negated), coverage would be:")
cov = g.assign(fixed=g["call"] | g["swapped"]).groupby("season")["fixed"].mean().round(3)
print(cov.to_string())
