"""`ratings_daily`: Bart Torvik team ratings by day, point-in-time (tasks 1.3 and 1.4).

Two sources feed it:

- `torvik_time_machine`: the backfill, one JSON file per day,
  `torvik/timemachine/<season>/YYYYMMDD_team_results.json.gz`. Each file is a list of rows
  with no header; the 45 positions are named in `TIMEMACHINE_COLUMNS` (see docs/TORVIK.md).
- `torvik_daily`: our own pulls of the live season, `torvik/raw/YYYY/MM/DD/
  <season>_team_results_<stamp>.csv.gz` (same 45 columns, sometimes with a header row).

When is a Time Machine file known? Task 1.4 tested it (docs/DECISIONS.md, 2026-11-11):
the file dated D already counts every game played on D, including late West Coast and
Hawaii games that end after midnight Eastern. So its `as_of` is 06:00 US Eastern on D+1,
and a game may use a file only if `as_of < tip_at` (no-leakage rule 2). For our own daily
pulls, `as_of` is simply the fetch time.

One more guard: each season's first file is dated the day before its first game, so
every team should be 0-0 in it. Rows that already show a game there are dropped
(one known case, 2025-26: an 8 a.m. Eastern opener counted in the file dated the day
before).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from pathlib import Path

import pandas as pd

# The 45 positions of a Time Machine row, named after the header of Torvik's
# `<season>_team_results.csv` (same generator, same order). docs/TORVIK.md says which of
# these were checked against the data and which are named by position only.
TIMEMACHINE_COLUMNS = [
    "rank",  # 0  rank by barthag
    "team",  # 1
    "conf",  # 2
    "record",  # 3  "W-L", every game Torvik counts (non-D-I opponents too, from 2019-20)
    "adj_o",  # 4  adjusted offensive efficiency (points per 100 possessions)
    "adj_o_rank",  # 5
    "adj_d",  # 6  adjusted defensive efficiency
    "adj_d_rank",  # 7
    "barthag",  # 8  chance of beating an average D-I team on a neutral floor
    "barthag_rank",  # 9  same as position 0
    "proj_w",  # 10
    "proj_l",  # 11
    "proj_conf_w",  # 12
    "proj_conf_l",  # 13
    "conf_record",  # 14
    "sos",  # 15
    "nc_sos",  # 16
    "conf_sos",  # 17
    "proj_sos",  # 18
    "proj_nc_sos",  # 19
    "proj_conf_sos",  # 20
    "elite_sos",  # 21
    "elite_nc_sos",  # 22
    "opp_oe",  # 23
    "opp_de",  # 24
    "opp_proj_oe",  # 25
    "opp_proj_de",  # 26
    "conf_adj_oe",  # 27
    "conf_adj_de",  # 28
    "qual_o",  # 29
    "qual_d",  # 30
    "qual_barthag",  # 31
    "qual_games",  # 32
    "fun",  # 33
    "conf_pf",  # 34
    "conf_pa",  # 35
    "conf_poss",  # 36
    "conf_oe",  # 37
    "conf_de",  # 38
    "conf_sos_remain",  # 39
    "conf_win_pct",  # 40
    "wab",  # 41  wins above bubble
    "wab_rank",  # 42
    "fun_rank",  # 43
    "adj_tempo",  # 44  adjusted possessions per 40 minutes
]

RATINGS_COLUMNS = [
    "rating_date",
    "season",
    "team_id",
    "source",
    "adj_o",
    "adj_d",
    "adj_tempo",
    "barthag",
    "rank",
    "wins",
    "losses",
    "as_of",
    "fetched_at",
]

# A Time Machine file dated D is treated as known at this hour (US Eastern) on D+1.
AS_OF_HOUR_EASTERN = 6
EASTERN = "America/New_York"

TM_FILE = re.compile(r"^(\d{8})_team_results\.json\.gz$")
DAILY_FILE = re.compile(r"^(\d{4})_team_results_(\d{8}T\d{6}Z)\.csv\.gz$")
RECORD = re.compile(r"^(\d+)-(\d+)$")


def time_machine_as_of(rating_date: pd.Series) -> pd.Series:
    """06:00 US Eastern on the day after `rating_date`, in UTC."""
    local = pd.to_datetime(rating_date) + pd.Timedelta(days=1, hours=AS_OF_HOUR_EASTERN)
    return local.dt.tz_localize(EASTERN).dt.tz_convert("UTC")


def parse_record(record: pd.Series) -> pd.DataFrame:
    """Split "W-L" into integer `wins` and `losses`. Raises on anything else."""
    parts = record.str.extract(RECORD)
    if parts.isna().any().any():
        bad = record[parts.isna().any(axis=1)].unique()[:5]
        raise ValueError(f"unexpected record values: {list(bad)}")
    return pd.DataFrame({"wins": parts[0].astype(int), "losses": parts[1].astype(int)})


def read_manifest_fetch_times(data_dir: Path) -> dict[str, str]:
    """Time Machine file path (as in the manifest) -> fetched_at_utc, for saved files."""
    path = data_dir / "torvik" / "timemachine" / "manifest.csv"
    manifest = pd.read_csv(path, dtype=str)
    ok = manifest[manifest["status"] == "ok"]
    return dict(zip(ok["file"], ok["fetched_at_utc"], strict=True))


def load_time_machine(data_dir: Path) -> pd.DataFrame:
    """Every saved Time Machine file as one wide frame: the 45 named columns plus
    `season`, `rating_date` and `fetched_at` (from the manifest)."""
    fetched = read_manifest_fetch_times(data_dir)
    frames = []
    root = data_dir / "torvik" / "timemachine"
    for path in sorted(root.glob("*/*_team_results.json.gz")):
        match = TM_FILE.match(path.name)
        if not match:
            continue
        rows = json.loads(gzip.decompress(path.read_bytes()))
        bad = {len(r) for r in rows} - {len(TIMEMACHINE_COLUMNS)}
        if bad:
            raise ValueError(f"{path.name}: rows with {sorted(bad)} fields, expected 45")
        frame = pd.DataFrame(rows, columns=TIMEMACHINE_COLUMNS)
        frame["season"] = int(path.parent.name)
        frame["rating_date"] = pd.Timestamp(match.group(1)).date()
        rel = path.relative_to(data_dir).as_posix()
        frame["fetched_at"] = fetched.get(rel)
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[*TIMEMACHINE_COLUMNS, "season", "rating_date", "fetched_at"])
    return pd.concat(frames, ignore_index=True)


def _csv_rows(path: Path) -> list[list[str]]:
    raw = gzip.decompress(path.read_bytes())
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    return [r for r in rows if len(r) > 1 and r[1] and r[1] != "team"]


def load_daily(data_dir: Path) -> pd.DataFrame:
    """Every saved daily pull (`<season>_team_results_<stamp>.csv.gz`) as one wide frame,
    with `season` and `fetched_at` (from the file name's UTC stamp)."""
    frames = []
    for path in sorted((data_dir / "torvik" / "raw").rglob("*_team_results_*.csv.gz")):
        match = DAILY_FILE.match(path.name)
        # Skip torvik/raw/seasons/: those are season-END files for past seasons, fetched
        # long after the games, not point-in-time pulls.
        if not match or "seasons" in path.relative_to(data_dir).parts:
            continue
        rows = _csv_rows(path)
        bad = {len(r) for r in rows} - {len(TIMEMACHINE_COLUMNS)}
        if bad:
            raise ValueError(f"{path.name}: rows with {sorted(bad)} fields, expected 45")
        frame = pd.DataFrame(rows, columns=TIMEMACHINE_COLUMNS)
        frame["season"] = int(match.group(1))
        stamp = pd.to_datetime(match.group(2), format="%Y%m%dT%H%M%SZ", utc=True)
        frame["fetched_at"] = stamp.isoformat()
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[*TIMEMACHINE_COLUMNS, "season", "fetched_at"])
    return pd.concat(frames, ignore_index=True)


def attach_team_ids(wide: pd.DataFrame, aliases: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Map Torvik names to team_id with the `torvik` aliases valid that season.

    Returns the frame (unmatched rows dropped) and how many rows were unmatched.
    """
    torvik = aliases[aliases["source"] == "torvik"][
        ["alias", "valid_from_season", "valid_to_season", "team_id"]
    ]
    merged = wide.merge(torvik, left_on="team", right_on="alias", how="left")
    valid_to = merged["valid_to_season"].fillna(9999)
    in_range = (merged["season"] >= merged["valid_from_season"]) & (merged["season"] <= valid_to)
    merged.loc[~in_range, "team_id"] = pd.NA
    # One row per original row: keep the alias that applies (the map check guarantees at
    # most one), else a single unmatched row.
    merged["_matched"] = merged["team_id"].notna()
    merged = merged.sort_values("_matched", ascending=False, kind="stable")
    merged = merged.drop_duplicates(["season", "rating_key", "team"], keep="first")
    n_unmatched = int(merged["team_id"].isna().sum())
    merged = merged[merged["team_id"].notna()].copy()
    merged["team_id"] = merged["team_id"].astype(int)
    return merged.drop(columns=["alias", "valid_from_season", "valid_to_season", "_matched"]), (
        n_unmatched
    )


def _finish(df: pd.DataFrame, source: str) -> pd.DataFrame:
    record = parse_record(df["record"].astype(str).reset_index(drop=True))
    df = df.reset_index(drop=True)
    out = pd.DataFrame(
        {
            "rating_date": pd.to_datetime(df["rating_date"]).dt.date,
            "season": df["season"].astype(int),
            "team_id": df["team_id"].astype(int),
            "source": source,
            "adj_o": df["adj_o"].astype(float),
            "adj_d": df["adj_d"].astype(float),
            "adj_tempo": df["adj_tempo"].astype(float),
            "barthag": df["barthag"].astype(float),
            "rank": df["rank"].astype(int),
            "wins": record["wins"],
            "losses": record["losses"],
            "as_of": df["as_of"],
            "fetched_at": pd.to_datetime(df["fetched_at"], utc=True),
        }
    )
    return out[RATINGS_COLUMNS]


def build_ratings_daily(
    time_machine: pd.DataFrame, daily: pd.DataFrame, aliases: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build `ratings_daily` from the two wide frames. Returns the table and counts."""
    counts: dict[str, int] = {}

    tm = time_machine.copy()
    tm["rating_key"] = tm["rating_date"].astype(str)
    tm, counts["time_machine_unmatched_names"] = attach_team_ids(tm, aliases)
    tm["as_of"] = time_machine_as_of(pd.to_datetime(tm["rating_date"]))
    # Pre-season guard: in each season's first file every team should be 0-0.
    first_date = tm.groupby("season")["rating_date"].transform("min")
    wl = parse_record(tm["record"].astype(str).reset_index(drop=True))
    played = (wl["wins"] + wl["losses"]).to_numpy() > 0
    drop = (tm["rating_date"] == first_date).to_numpy() & played
    counts["time_machine_preseason_rows_dropped"] = int(drop.sum())
    tm = tm[~drop]
    counts["time_machine_rows"] = len(tm)

    dl = daily.copy()
    fetched = pd.to_datetime(dl["fetched_at"], utc=True)
    # rating_date for our own pulls = the US Eastern date of the pull; keep the first pull
    # of each date (the scheduled one). as_of is the fetch time itself.
    dl["rating_date"] = fetched.dt.tz_convert(EASTERN).dt.date
    dl["as_of"] = fetched
    dl["rating_key"] = dl["fetched_at"].astype(str)
    dl, counts["daily_unmatched_names"] = attach_team_ids(dl, aliases)
    dl = dl.sort_values("as_of").drop_duplicates(["rating_date", "team_id"], keep="first")
    counts["daily_rows"] = len(dl)

    parts = [_finish(tm, "torvik_time_machine")]
    if len(dl):
        parts.append(_finish(dl, "torvik_daily"))
    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["source", "rating_date", "team_id"]).reset_index(drop=True)
    return out, counts
