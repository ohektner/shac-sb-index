"""The free call counter for a bought Odds API month (docs/BACKTEST_CLOSE.md)."""

from __future__ import annotations

import pandas as pd

from shac.ingest.odds_history_plan import plan, snapshots_for_day


def _tips(*pairs):
    """(time, count) pairs -> a Series of tip times."""
    out = []
    for when, n in pairs:
        out += [pd.Timestamp(when)] * n
    return pd.Series(out)


def test_busiest_tip_times_first_until_80_percent():
    # 10 games: 4 at 19:00, 3 at 21:00, 2 at 17:00, 1 at 23:00. 80% = 8 games:
    # 19:00 (4), 21:00 (7), 17:00 (9) -> 3 snapshots.
    day = _tips(
        ("2027-01-10T19:00Z", 4),
        ("2027-01-10T21:00Z", 3),
        ("2027-01-10T17:00Z", 2),
        ("2027-01-10T23:00Z", 1),
    )
    assert snapshots_for_day(day) == 3


def test_at_most_eight_a_day():
    # 20 tip times with one game each: 80% needs 16, but the cap is 8.
    day = pd.Series(pd.date_range("2027-01-10T12:00Z", periods=20, freq="15min"))
    assert snapshots_for_day(day) == 8


def test_plan_adds_one_early_snapshot_per_game_day_and_runs_newest_first():
    games = pd.DataFrame(
        {
            "game_id": [1, 2, 3, 4],
            "season": [2025, 2025, 2026, 2026],
            # 2025: two Eastern days. 2026: one day (01:00 UTC is still Jan 10 in Eastern).
            "tip_at": pd.to_datetime(
                [
                    "2025-01-10T19:00Z",
                    "2025-01-11T19:00Z",
                    "2026-01-10T19:00Z",
                    "2026-01-11T01:00Z",
                ],
                utc=True,
            ),
            "tip_tbd": [False, False, False, True],
        }
    )
    out = plan(games, [2025, 2026])
    assert out["season"].tolist() == [2026, 2025]
    s26 = out.iloc[0]
    assert s26["game_days"] == 1
    assert s26["pre_tip_calls"] == 2  # 2 games at 2 times: 80% needs both
    assert s26["calls"] == 3 and s26["credits"] == 30
    assert s26["tip_tbd_games"] == 1
    s25 = out.iloc[1]
    assert s25["calls"] == 4  # 2 days x (1 pre-tip + 1 at 15:00 UTC)
    assert s25["running_calls"] == 7 and bool(s25["within_cap"])
