"""Task 1.2 runner: T4(b) on made-up raw files, and the SQL blocks on made-up tables."""

from __future__ import annotations

import duckdb
import pandas as pd

from shac.tables import backtest_close as bc
from shac.tables.backtest_close import SQL_PATH, run_sql, sample_status, t4b


def _rec(gid, spread, scores=True, provider="Draft Kings", season=2027):
    return {
        "gameId": gid,
        "season": season,
        "startDate": "2026-11-12T00:00:00.000Z",
        "homeScore": 70 if scores else None,
        "awayScore": 60 if scores else None,
        "lines": [{"provider": provider, "spread": spread}],
    }


def _file(fetched_at, records):
    return (f"lines_{fetched_at}.json.gz", {"fetched_at": fetched_at, "data": records})


def test_t4b_counts_games_whose_line_changed_after_final():
    files = [
        # Before the start: never counts, even with a different number.
        _file("2026-11-11T10:00:00Z", [_rec(1, -9.0, scores=False), _rec(2, -9.0, scores=False)]),
        _file("2026-11-12T10:00:00Z", [_rec(1, -4.0), _rec(2, -3.0), _rec(3, -1.0)]),
        _file("2026-11-13T10:00:00Z", [_rec(1, -4.0), _rec(2, -3.5)]),
        # Same fetch time in an overlapping window: counted once.
        _file("2026-11-12T10:00:00Z", [_rec(3, -1.0)]),
        # Another season and another provider: ignored.
        _file("2026-11-14T10:00:00Z", [_rec(1, 5.0, season=2026), _rec(1, 5.0, provider="x")]),
    ]
    out = t4b(files).set_index("provider")
    dk = out.loc["Draft Kings"]
    # Game 1: two post-final fetches, same line. Game 2: changed. Game 3: one fetch time only.
    assert dk["games"] == 2
    assert dk["changed"] == 1
    assert dk["t4b_changed_pct"] == 50.0
    assert out.loc["Bovada", "games"] == 0
    assert pd.isna(out.loc["Bovada", "t4b_changed_pct"])


def test_sql_blocks_run_and_score_a_made_up_game():
    tip = pd.Timestamp("2026-11-12T00:00:00Z")
    games = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "season": [2027, 2026, 2023],
            "tip_at": [tip, tip, tip],
            "tip_tbd": [False, False, False],
        }
    )
    results = pd.DataFrame({"game_id": [1, 2, 3], "home_margin": [5, 10, 3]})
    lines = pd.DataFrame(
        {
            "game_id": [1, 2, 2, 3, 3],
            "provider": ["Draft Kings", "Draft Kings", "ESPN BET", "ESPN BET", "teamrankings"],
            "home_spread": [-5.0, -9.0, -8.5, -3.0, -3.5],
            "home_spread_open": [-3.0, -6.0, None, None, None],
        }
    )
    snaps = pd.DataFrame(
        {
            "snapshot_at": [tip - pd.Timedelta(hours=10), tip - pd.Timedelta(minutes=30)],
            "odds_event_id": ["e1", "e1"],
            "book": ["draftkings", "draftkings"],
            "commence_at": [tip, tip],
            "home_spread": [-3.0, -5.0],
            "game_id": [1, 1],
        }
    )
    con = duckdb.connect()
    for name, df in {
        "games": games,
        "results": results,
        "cbbd_lines": lines,
        "odds_snapshots": snaps,
    }.items():
        con.register(name, df)
    out = run_sql(con, SQL_PATH.read_text(encoding="utf-8"))
    assert set(out) == {"tests_by_provider", "links_l1_l3", "link_l2", "link_l4"}

    dk = out["tests_by_provider"].set_index("provider").loc["Draft Kings"]
    assert dk["tested_games"] == 1
    assert dk["movers"] == 1  # own open -3.0 -> own close -5.0
    assert dk["t2_near_close_pct"] == 100.0
    assert dk["t3_exact_pct"] == 100.0
    assert dk["t5_pct"] == 100.0  # CBBD open -3.0 vs own open -3.0

    l2 = out["link_l2"].iloc[0]
    assert l2["n"] == 1 and l2["near_spread_pct"] == 100.0
    l4 = out["link_l4"].iloc[0]
    assert l4["n"] == 1 and l4["within_half_pct"] == 100.0

    assert sample_status(out["tests_by_provider"]) == [
        "Draft Kings: tested games 1/150 (WAIT), movers 1/40 (WAIT)"
    ]


def test_lines_note_fills_in_the_current_draft_kings_numbers():
    # The scorecard's note on its lines (DECISIONS.md 2026-11-21) is rebuilt from the
    # current check numbers on every run, whichever way they move.
    tests = pd.DataFrame(
        {
            "provider": ["Bovada", "Draft Kings"],
            "tested_games": [192, 209],
            "movers": [109, 155],
            "t2_near_close_pct": [61.5, 91.0],
            "t3_exact_pct": [78.1, 88.5],
        }
    )
    note = bc.lines_note(tests)
    assert "Draft Kings, 2026-27, 209 games" in note
    assert "within half a point of the close in 88% of games (185 of 209)" in note
    assert "nearer the close than the open in 91% of the 155 games" in note
    assert "not verified closing lines" in note
