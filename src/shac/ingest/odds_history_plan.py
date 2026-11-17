"""Count the Odds API historical calls a bought month would need. Makes no request.

Run as:
    uv run python -m shac.ingest.odds_history_plan --data-dir <private repo> [--seasons 2023-2026]

The rule is the pull plan in docs/BACKTEST_CLOSE.md ("If a month is bought"):
- For each game day (US Eastern date of the tip), group that day's games by tip time
  (`games.tip_at`). Take a snapshot 5 minutes before each of the busiest tip times, busiest
  first, until 80% of the day's games are covered, at most 8 a day.
- Add one snapshot at 15:00 UTC that day (an early line, for CLV).
- One call returns every listed game at that moment and costs 10 credits.
- Order: newest season first; stop at 1,900 calls (19,000 credits).

Every game in `games` counts toward the day, as the plan says; games with a placeholder
tip time (`tip_tbd`) are counted and reported, but their time is used as given. Prints
counts only.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import duckdb
import pandas as pd

from shac.ingest.cbbd import parse_seasons
from shac.tables.build import query_df

SNAPSHOTS_PER_DAY_MAX = 8
COVERAGE = 0.80
CREDITS_PER_CALL = 10
CALL_CAP = 1900
EASTERN = "America/New_York"


def snapshots_for_day(tip_times: pd.Series) -> int:
    """Pre-tip snapshots for one day: busiest tip times first until 80% are covered (max 8)."""
    n_games = len(tip_times)
    if n_games == 0:
        return 0
    # Busiest first; ties go to the earlier tip time so the result never depends on order.
    counts = tip_times.value_counts().rename_axis("tip").reset_index(name="n")
    counts = counts.sort_values(["n", "tip"], ascending=[False, True])
    needed = math.ceil(COVERAGE * n_games)
    covered = 0
    taken = 0
    for n in counts["n"]:
        if covered >= needed or taken >= SNAPSHOTS_PER_DAY_MAX:
            break
        covered += int(n)
        taken += 1
    return taken


def plan(games: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """Calls per season (newest first), with the running total against the 1,900 cap."""
    rows = []
    for season in sorted(seasons, reverse=True):
        g = games[games["season"] == season]
        tips = pd.to_datetime(g["tip_at"], utc=True)
        day = tips.dt.tz_convert(EASTERN).dt.date
        pre_tip = sum(snapshots_for_day(t) for _d, t in tips.groupby(day))
        n_days = int(day.nunique())
        calls = pre_tip + n_days  # plus the 15:00 UTC snapshot each game day
        rows.append(
            {
                "season": season,
                "games": len(g),
                "tip_tbd_games": int(g["tip_tbd"].fillna(False).astype(bool).sum()),
                "game_days": n_days,
                "pre_tip_calls": pre_tip,
                "calls": calls,
                "credits": calls * CREDITS_PER_CALL,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["running_calls"] = out["calls"].cumsum()
    out["within_cap"] = out["running_calls"] <= CALL_CAP
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--seasons", default="2023-2026", help="held-out seasons by default")
    args = parser.parse_args(argv)

    path = args.data_dir / "tables" / "games.parquet"
    if not path.exists():
        print("missing tables/games.parquet (run shac.tables.build first)")
        return 1
    con = duckdb.connect()
    target = path.as_posix().replace("'", "''")
    games = query_df(con, f"SELECT game_id, season, tip_at, tip_tbd FROM read_parquet('{target}')")
    out = plan(games, parse_seasons(args.seasons))
    pd.set_option("display.width", 200)
    print(out.to_string(index=False))
    total = int(out["calls"].sum()) if len(out) else 0
    print(f"\ntotal: {total} calls ({total * CREDITS_PER_CALL} credits); cap {CALL_CAP} calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
