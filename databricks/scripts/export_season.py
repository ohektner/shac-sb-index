"""Write site/season.json from the Databricks season tables (docs/DATABRICKS.md, D3).

Run by the `season-export` workflow. Reads gold_season_scorecard and gold_season_weekly
through the SQL Statement API (Databricks CLI) and writes derived numbers only: no team
names, ratings or lines. The /shac/ page on ohektner.github.io charts this file.

    python databricks/scripts/export_season.py --out site/season.json

Standard library only. Exits 0 without writing if there is nothing graded yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_raw import run  # noqa: E402

NS = "workspace.shac"


def warehouse_id() -> str:
    warehouses = json.loads(run("warehouses", "list", "--output", "json") or "[]")
    if not warehouses:
        raise SystemExit("no SQL warehouse in the workspace")
    return warehouses[0]["id"]


def query(wh: str, sql: str) -> list[dict]:
    body = json.dumps(
        {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "format": "JSON_ARRAY"}
    )
    res = json.loads(run("api", "post", "/api/2.0/sql/statements/", "--json", body))
    state = res["status"]["state"]
    if state != "SUCCEEDED":
        raise SystemExit(f"query {state}: {res['status'].get('error', {}).get('message', '')}")
    cols = [c["name"] for c in res["manifest"]["schema"]["columns"]]
    return [
        dict(zip(cols, row, strict=True)) for row in res.get("result", {}).get("data_array") or []
    ]


def num(v, digits: int = 4):
    if v is None:
        return None
    f = float(v)
    return int(f) if digits == 0 else round(f, digits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    wh = warehouse_id()
    cards = query(wh, f"SELECT * FROM {NS}.gold_season_scorecard ORDER BY scope")
    weekly = query(wh, f"SELECT * FROM {NS}.gold_season_weekly ORDER BY scope, week_start")
    model = query(
        wh,
        f"SELECT feature, weight FROM {NS}.gold_model_fits "
        "WHERE feature_set = 'core' AND test_season = 2027",
    )
    if not cards:
        print("nothing graded yet; season.json not written")
        return 0

    def card(row: dict) -> dict:
        return {
            "games": num(row["n_games"], 0),
            "updated_through": row["updated_through"],
            "mean_abs_distance_to_close": num(row["mean_abs_distance_to_close"]),
            "wins": num(row["wins"], 0),
            "losses": num(row["losses"], 0),
            "pushes": num(row["pushes"], 0),
            "win_pct": num(row["win_pct"]),
            "mae_model": num(row["mae_model"]),
            "mae_close": num(row["mae_close"]),
        }

    doc = {
        "name": "Shac SB Index",
        "subtitle": "a college basketball forecasting model",
        "season": "2026-27",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "break_even_win_pct": 0.524,
        "model": {
            "name": "v1 (champion)",
            "weights": {r["feature"]: num(r["weight"]) for r in model},
        },
        "scopes": {r["scope"]: card(r) for r in cards},
        "weekly": {
            scope: [
                {
                    "week_start": w["week_start"],
                    "games": num(w["cum_games"], 0),
                    "wins": num(w["cum_wins"], 0),
                    "losses": num(w["cum_losses"], 0),
                    "win_pct": num(w["cum_win_pct"]),
                    "mean_abs_distance_to_close": num(w["cum_mean_abs_distance"]),
                }
                for w in weekly
                if w["scope"] == scope
            ]
            for scope in sorted({w["scope"] for w in weekly})
        },
        "notes": [
            "season: every 2026-27 game, rebuilt nightly with ratings published before tip.",
            "live: only games the nightly job priced before tip (its last pre-tip number).",
            "Graded against CBBD's line (Draft Kings, else the median); research, not betting "
            "advice.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    s = doc["scopes"].get("season", {})
    print(
        f"wrote {args.out}: season {s.get('wins')}-{s.get('losses')}, "
        f"through {s.get('updated_through')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
