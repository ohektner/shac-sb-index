"""Task 1.2: run the backtest-close tests from docs/BACKTEST_CLOSE.md and print the numbers.

Run as:
    uv run python -m shac.tables.backtest_close --data-dir <private repo>

T1-T3, T4(a), T5 and the links L1-L4 are SQL (sql/checks/backtest_close.sql) over the built
tables, so run `shac.tables.build` first. T4(b) reads the raw CBBD `lines` files, because the
tables keep only the latest copy of each line.

Prints counts and shares only. It reads no branch off the results and decides nothing: the
rules in docs/BACKTEST_CLOSE.md do that, and the DECISIONS.md entry needs Owen's sign-off.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

from shac.tables import raw
from shac.tables.check import open_tables
from shac.teams.check import split_checks

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_PATH = REPO_ROOT / "sql" / "checks" / "backtest_close.sql"

TESTED_PROVIDERS = ("Draft Kings", "Bovada")
SEASON = 2027  # 2026-27, the only season with our own snapshots
MIN_TESTED_GAMES = 150  # T1, T3, T4
MIN_MOVERS = 40  # T2


def run_sql(con: duckdb.DuckDBPyConnection, sql_text: str) -> dict[str, pd.DataFrame]:
    """Run every '-- check:' block in order. Blocks that return nothing (views) are skipped."""
    out: dict[str, pd.DataFrame] = {}
    for name, query in split_checks(sql_text):
        cur = con.execute(query)
        if cur.description is None:
            continue
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        if name == "tested":  # the CREATE VIEW block
            continue
        out[name] = pd.DataFrame(rows, columns=cols)
    return out


def t4b(
    wrapped_files: list[tuple[str, dict]],
    season: int = SEASON,
    providers: tuple[str, ...] = TESTED_PROVIDERS,
) -> pd.DataFrame:
    """T4(b): among games fetched two or more times after they were final, how many lines changed.

    A fetch counts as "after final" when the record carries both scores and was fetched after
    the game's start. Only fetches with a spread for the provider count. Each fetch time is
    counted once, even when overlapping windows hold the same game.
    """
    spreads: dict[tuple[int, str], dict[str, float]] = defaultdict(dict)
    for _name, wrapped in wrapped_files:
        fetched_at = wrapped["fetched_at"]
        fetched_ts = pd.Timestamp(fetched_at)
        for rec in wrapped.get("data") or []:
            if rec.get("season") != season:
                continue
            if rec.get("homeScore") is None or rec.get("awayScore") is None:
                continue
            start = rec.get("startDate")
            if start is None or fetched_ts <= pd.Timestamp(start):
                continue
            for line in rec.get("lines") or []:
                provider = line.get("provider")
                if provider in providers and line.get("spread") is not None:
                    spreads[(rec["gameId"], provider)][fetched_at] = float(line["spread"])

    rows = []
    for provider in providers:
        seen = [v for (gid, p), v in spreads.items() if p == provider and len(v) >= 2]
        changed = sum(1 for v in seen if len(set(v.values())) > 1)
        rows.append(
            {
                "provider": provider,
                "games": len(seen),
                "changed": changed,
                "t4b_changed_pct": round(100.0 * changed / len(seen), 1) if seen else None,
            }
        )
    return pd.DataFrame(rows)


def sample_status(tests: pd.DataFrame) -> list[str]:
    """One line per provider: whether the sample sizes in BACKTEST_CLOSE.md are reached."""
    lines = []
    for _, row in tests.iterrows():
        t134 = "ok" if row["tested_games"] >= MIN_TESTED_GAMES else "WAIT"
        t2 = "ok" if row["movers"] >= MIN_MOVERS else "WAIT"
        lines.append(
            f"{row['provider']}: tested games {row['tested_games']}/{MIN_TESTED_GAMES} ({t134}), "
            f"movers {row['movers']}/{MIN_MOVERS} ({t2})"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    args = parser.parse_args(argv)

    con, missing = open_tables(args.data_dir / "tables")
    if missing:
        print("missing tables:", ", ".join(missing), "(run shac.tables.build first)")
        return 1

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    results = run_sql(con, SQL_PATH.read_text(encoding="utf-8"))
    for name, df in results.items():
        print(f"## {name}")
        print(df.to_string(index=False))
        print()
    print("## t4b (raw CBBD lines fetched after the game was final)")
    print(t4b(raw.load_endpoint(args.data_dir, "lines")).to_string(index=False))
    print()
    print("## sample size (docs/BACKTEST_CLOSE.md)")
    for line in sample_status(results["tests_by_provider"]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
