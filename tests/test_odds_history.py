"""Tests for the historical-odds pull and its tables. No network: httpx.MockTransport."""

from __future__ import annotations

import gzip
import json
from datetime import date

import httpx
import pandas as pd

from shac.ingest import odds_history as oh
from shac.tables import odds_history as th
from shac.teams.sources import load_odds_names

KEY = "secret-key-123"


def games(rows):
    """rows: (game_id, season, tip UTC, status, home id, away id)."""
    df = pd.DataFrame(
        rows, columns=["game_id", "season", "tip_at", "status", "home_team_id", "away_team_id"]
    )
    df["tip_at"] = pd.to_datetime(df["tip_at"], utc=True)
    return df


GAMES = games(
    [
        # 7 p.m. Eastern (EST) on Nov 14, 2025: season 2026.
        (1, 2026, "2025-11-15T00:00:00Z", "final", 10, 20),
        # 7 p.m. Eastern (EDT, after the March change) on Mar 20, 2026.
        (2, 2026, "2026-03-20T23:00:00Z", "final", 10, 20),
        # Not final: no game day of its own.
        (3, 2026, "2025-11-20T00:00:00Z", "postponed", 10, 20),
        # Not in the pull's seasons.
        (4, 2022, "2021-11-15T00:00:00Z", "final", 10, 20),
        # Season 2025, pulled after 2026.
        (5, 2025, "2024-11-10T00:00:00Z", "final", 10, 20),
    ]
)


def event(commence="2025-11-15T00:00:00Z", home="Home Hawks", away="Away Owls", point=-8.5):
    return {
        "id": "ev1",
        "sport_key": "basketball_ncaab",
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "spreads",
                        "last_update": "2025-11-14T03:00:00Z",
                        "outcomes": [
                            {"name": home, "price": -110, "point": point},
                            {"name": away, "price": -110, "point": -point},
                        ],
                    }
                ],
            }
        ],
    }


def api(remaining=19990, status=200, seen=None):
    """A fake API. The snapshot time is the requested time minus 3 minutes."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": "nope"})
        asked = pd.Timestamp(request.url.params["date"])
        snap = (asked - pd.Timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "timestamp": snap,
            "previous_timestamp": snap,
            "next_timestamp": snap,
            "data": [event()],
        }
        headers = {
            "x-requests-remaining": str(remaining),
            "x-requests-used": "10",
            "x-requests-last": "10",
        }
        return httpx.Response(200, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


def no_sleep(_seconds):
    return None


def test_plan_follows_the_eastern_clock_through_the_time_change():
    p = oh.plan(GAMES)
    # Three game days (Nov 14 2025, Mar 20 2026, Nov 9 2024), three requests each.
    assert len(p) == 9
    # Newest season first.
    assert list(p["season"]) == [2026] * 6 + [2025] * 3
    nov = p[p["game_day"] == date(2025, 11, 14)].set_index("kind")["requested_at"]
    assert nov["evening"] == pd.Timestamp("2025-11-14T03:20:00Z")  # 22:20 EST, Nov 13
    assert nov["afternoon"] == pd.Timestamp("2025-11-14T18:55:00Z")  # 13:55 EST
    assert nov["late"] == pd.Timestamp("2025-11-14T23:55:00Z")  # 18:55 EST
    mar = p[p["game_day"] == date(2026, 3, 20)].set_index("kind")["requested_at"]
    assert mar["evening"] == pd.Timestamp("2026-03-20T02:20:00Z")  # 22:20 EDT, Mar 19
    assert mar["afternoon"] == pd.Timestamp("2026-03-20T17:55:00Z")
    assert mar["late"] == pd.Timestamp("2026-03-20T22:55:00Z")


def test_plan_summary_counts_credits():
    s = oh.plan_summary(oh.plan(GAMES))
    assert list(s["calls"]) == [6, 3]
    assert list(s["credits"]) == [60, 30]
    assert list(s["running_credits"]) == [60, 90]


def test_run_saves_wrapped_files_and_a_manifest_without_the_key(tmp_path):
    seen = []
    p = oh.plan(GAMES).head(2)
    result = oh.run(api(seen=seen), KEY, tmp_path, p, sleep=no_sleep)
    assert result["ok"] == 2 and result["stopped"] == "plan_finished"
    assert result["credits_remaining"] == 19990
    # The request asks for the planned time, US books, spreads.
    assert seen[0].url.params["date"] == "2025-11-14T03:20:00Z"
    assert seen[0].url.params["regions"] == "us"
    assert seen[0].url.params["markets"] == "spreads"
    files = sorted((tmp_path / "odds" / "history").rglob("odds_hist_*.json.gz"))
    assert len(files) == 2
    wrapped = json.loads(gzip.decompress(files[0].read_bytes()))
    assert wrapped["fetched_at"] == "2025-11-14T03:17:00Z"  # the archive's snapshot time
    assert wrapped["kind"] == "evening"
    assert wrapped["data"][0]["id"] == "ev1"
    for f in files:
        assert KEY.encode() not in gzip.decompress(f.read_bytes())
    manifest = (tmp_path / oh.MANIFEST).read_text(encoding="utf-8")
    assert KEY not in manifest
    rows = pd.read_csv(tmp_path / oh.MANIFEST)
    assert list(rows["status"]) == ["ok", "ok"]
    assert list(rows["snapshot_at_utc"])[0] == "2025-11-14T03:17:00Z"


def test_run_again_skips_what_is_saved(tmp_path):
    p = oh.plan(GAMES).head(2)
    oh.run(api(), KEY, tmp_path, p, sleep=no_sleep)
    again = oh.run(api(), KEY, tmp_path, p, sleep=no_sleep)
    assert again["calls"] == 0 and again["skipped_done"] == 2


def test_stops_before_eating_the_reserve(tmp_path):
    # After the first call 1,005 credits remain; the next would leave 995 < 1,000.
    result = oh.run(api(remaining=1005), KEY, tmp_path, oh.plan(GAMES), sleep=no_sleep)
    assert result["calls"] == 1 and result["stopped"] == "reserve"


def test_max_calls(tmp_path):
    result = oh.run(api(), KEY, tmp_path, oh.plan(GAMES), max_calls=1, sleep=no_sleep)
    assert result["calls"] == 1 and result["stopped"] == "max_calls"


def test_stops_on_401_and_logs_no_key(tmp_path):
    result = oh.run(api(status=401), KEY, tmp_path, oh.plan(GAMES), sleep=no_sleep)
    assert result["stopped"] == "http_401" and result["calls"] == 1
    rows = pd.read_csv(tmp_path / oh.MANIFEST)
    assert list(rows["status"]) == ["error"]
    assert KEY not in (tmp_path / oh.MANIFEST).read_text(encoding="utf-8")


def test_stops_after_three_errors_in_a_row(tmp_path):
    result = oh.run(api(status=503), KEY, tmp_path, oh.plan(GAMES), sleep=no_sleep)
    assert result["stopped"] == "errors_in_a_row" and result["calls"] == 3


def test_archive_files_flow_through_the_live_matching(tmp_path):
    p = oh.plan(GAMES).head(3)  # Nov 14, 2025: evening, afternoon, late
    oh.run(api(), KEY, tmp_path, p, sleep=no_sleep)
    files = th.load_history_files(tmp_path)
    aliases = pd.DataFrame(
        {
            "source": ["odds_api", "odds_api"],
            "alias": ["Home Hawks", "Away Owls"],
            "valid_from_season": [2023, 2023],
            "valid_to_season": pd.array([None, None], dtype="Int64"),
            "team_id": [10, 20],
        }
    )
    history, matches, requests, _counts = th.build(files, GAMES, aliases)
    assert set(matches["match_type"]) == {"matched"}
    assert set(history["game_id"].dropna()) == {1}
    assert set(history["snapshot_at"]) == {
        pd.Timestamp("2025-11-14T03:17:00Z"),
        pd.Timestamp("2025-11-14T18:52:00Z"),
        pd.Timestamp("2025-11-14T23:52:00Z"),
    }
    assert sorted(requests["kind"]) == ["afternoon", "evening", "late"]
    cov = th.coverage(history, requests, GAMES).set_index("season")
    # Season 2026 has two final games (game 3 was postponed); only game 1's day was pulled.
    assert cov.loc[2026, "with_call_line"] == 1
    assert cov.loc[2026, "final_games"] == 2
    # Archive names reach the team map with their own season.
    names = load_odds_names(tmp_path)
    assert set(names["season"]) == {2026}
    assert set(names["name"]) == {"Home Hawks", "Away Owls"}
