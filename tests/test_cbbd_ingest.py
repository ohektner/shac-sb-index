"""Tests for the CBBD raw ingestion. No network: httpx.MockTransport with invented data."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from shac.ingest import cbbd
from shac.ingest.windows import CAP_ROWS, block_windows, month_windows, season_bounds, split_window

KEY = "sekret-key-12345"
SENTINEL = "SENTINEL_TEAM_XYZ"
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def rows(n: int) -> list[dict]:
    """Invented records. The team name is a sentinel so tests can spot leaks into stdout."""
    return [{"id": i, "homeTeam": SENTINEL, "homePoints": 70 + i, "spread": -3.5} for i in range(n)]


class FakeApi:
    """A fake CBBD. `reply` maps a request to a Response; every request is recorded."""

    def __init__(self, reply=None):
        self.requests: list[httpx.Request] = []
        self.reply = reply or (lambda request: httpx.Response(200, json=rows(2)))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.reply(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def run(tmp_path, monkeypatch, api, *extra, mode="discover", sleeps=None, now=clock):
    monkeypatch.setenv("CBBD_API_KEY", KEY)
    argv = ["--data-dir", str(tmp_path), "--mode", mode, *extra]
    return cbbd.main(
        argv,
        transport=api.transport,
        clock=now,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


def manifest(tmp_path) -> list[dict[str, str]]:
    with open(tmp_path / "cbbd" / "raw" / "manifest.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def all_text_files(tmp_path) -> str:
    """Everything saved, decompressed, as one string (to search for the key)."""
    parts = []
    for path in tmp_path.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            parts.append(gzip.decompress(raw).decode() if path.suffix == ".gz" else raw.decode())
    return "\n".join(parts)


def test_file_shape_and_path(tmp_path, monkeypatch):
    api = FakeApi()
    assert run(tmp_path, monkeypatch, api, "--max-calls", "3") == 0
    row = manifest(tmp_path)[2]  # /teams?season=2026
    assert row["endpoint"] == "teams"
    assert row["status"] == "ok" and row["http_status"] == "200" and row["n_rows"] == "2"
    path = tmp_path / row["file"]
    assert row["file"] == "cbbd/raw/teams/season-2026_20261110T120000Z.json.gz"
    payload = json.loads(gzip.decompress(path.read_bytes()))
    assert set(payload) == {"fetched_at", "source", "endpoint", "params", "n_rows", "data"}
    assert payload["source"] == "cbbd" and payload["params"] == {"season": 2026}
    assert payload["n_rows"] == 2 and len(payload["data"]) == 2
    assert row["params_json"] == '{"season": 2026}'
    assert int(row["bytes"]) == path.stat().st_size


def test_request_paths_params_and_auth_header(tmp_path, monkeypatch):
    api = FakeApi()
    run(tmp_path, monkeypatch, api)
    paths = [r.url.path for r in api.requests]
    assert paths == [
        "/conferences",
        "/lines/providers",
        "/teams",
        "/games",
        "/lines",
        "/games/teams",
        "/games/players",
        "/games",
    ]
    assert api.requests[0].headers["authorization"] == f"Bearer {KEY}"
    day = api.requests[3].url.params
    assert day["startDateRange"] == "2026-01-10T00:00:00Z"
    assert day["endDateRange"] == "2026-01-11T00:00:00Z"


def test_key_never_in_files_manifest_or_output(tmp_path, monkeypatch, capsys):
    api = FakeApi()
    run(tmp_path, monkeypatch, api)
    assert KEY not in all_text_files(tmp_path)
    assert KEY not in capsys.readouterr().out


def test_key_not_printed_on_errors(tmp_path, monkeypatch, capsys):
    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url} with Bearer {KEY}", request=request)

    assert run(tmp_path, monkeypatch, FakeApi(boom), "--max-calls", "5") == 1
    out = capsys.readouterr().out
    assert KEY not in out and "ConnectError" in out
    assert KEY not in all_text_files(tmp_path)

    api = FakeApi(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    assert run(tmp_path, monkeypatch, api) == 1
    assert KEY not in capsys.readouterr().out
    assert KEY not in all_text_files(tmp_path)


def test_cached_skip_makes_zero_calls_on_second_run(tmp_path, monkeypatch, capsys):
    first = FakeApi()
    run(tmp_path, monkeypatch, first)
    assert len(first.requests) == 8
    second = FakeApi()
    assert run(tmp_path, monkeypatch, second) == 0
    assert second.requests == []
    assert "cached skips: 8" in capsys.readouterr().out
    assert len(manifest(tmp_path)) == 8


def test_error_rows_are_retried_next_run(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, FakeApi(lambda r: httpx.Response(404)), "--max-calls", "2")
    assert all(r["status"] == "error" for r in manifest(tmp_path))
    api = FakeApi()
    run(tmp_path, monkeypatch, api, "--max-calls", "1")
    assert len(api.requests) == 1  # the earlier 404 did not count as "saved"


def test_max_calls_stops_cleanly(tmp_path, monkeypatch, capsys):
    api = FakeApi()
    assert run(tmp_path, monkeypatch, api, "--max-calls", "3") == 0
    assert len(api.requests) == 3
    assert "--max-calls 3" in capsys.readouterr().out


def test_monthly_budget_stops_cleanly(tmp_path, monkeypatch, capsys):
    api = FakeApi()
    assert run(tmp_path, monkeypatch, api, "--monthly-budget", "2") == 0
    assert len(api.requests) == 2
    assert "monthly budget" in capsys.readouterr().out
    # A second run in the same month may make no calls at all.
    again = FakeApi()
    assert run(tmp_path, monkeypatch, again, "--monthly-budget", "2") == 0
    assert again.requests == []
    # Next month the count starts over.
    later = FakeApi()
    next_month = lambda: NOW + timedelta(days=30)  # noqa: E731
    run(tmp_path, monkeypatch, later, "--monthly-budget", "2", now=next_month)
    assert len(later.requests) == 2


def test_discover_makes_at_most_eight_calls(tmp_path, monkeypatch):
    # Even if every response is capped, discover must not split.
    capped = FakeApi(lambda r: httpx.Response(200, json=rows(3000)))
    run(tmp_path, monkeypatch, capped, "--max-calls", "50")
    assert len(capped.requests) == 8


def test_header_written_once(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, FakeApi(), "--max-calls", "2")
    run(tmp_path, monkeypatch, FakeApi(), "--max-calls", "5")
    lines = (tmp_path / "cbbd" / "raw" / "manifest.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(cbbd.MANIFEST_COLUMNS)
    assert sum(1 for line in lines if line.startswith("fetched_at_utc,")) == 1


def window_of(request: httpx.Request) -> tuple[datetime, datetime]:
    p = request.url.params
    parse = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)  # noqa: E731
    return parse(p["startDateRange"]), parse(p["endDateRange"])


def test_cap_detection_splits_in_half(tmp_path, monkeypatch):
    seen: list[tuple[datetime, datetime]] = []

    def reply(request):
        if request.url.path != "/games":
            return httpx.Response(200, json=[])
        window = window_of(request)
        seen.append(window)
        # Only the first call (the whole 2-month window) is capped.
        return httpx.Response(200, json=rows(3000 if len(seen) == 1 else 5))

    api = FakeApi(reply)
    start = season_bounds(2026)[0]
    ingestor = cbbd.Ingestor(
        tmp_path,
        KEY,
        max_calls=50,
        monthly_budget=800,
        transport=api.transport,
        clock=clock,
        sleep=lambda s: None,
        out=lambda s: None,
    )
    ingestor.fetch_window("games", (start, start + timedelta(days=30)), split=True)
    assert len(seen) == 3
    whole, first, second = seen
    assert first == (whole[0], whole[0] + timedelta(days=15))
    assert second == (first[1], whole[1])
    statuses = [r["status"] for r in manifest(tmp_path)]
    assert statuses == ["capped", "ok", "ok"]


def test_splitting_stops_at_one_day(tmp_path, monkeypatch, capsys):
    api = FakeApi(lambda r: httpx.Response(200, json=rows(CAP_ROWS["games"])))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ingestor = cbbd.Ingestor(
        tmp_path,
        KEY,
        max_calls=100,
        monthly_budget=800,
        transport=api.transport,
        clock=clock,
        sleep=lambda s: None,
        out=lambda s: None,
    )
    ingestor.fetch_window("games", (start, start + timedelta(days=3)), split=True)
    # 3 days -> 1 + 2 days -> ... every piece is capped, ending at single days.
    spans = sorted((window_of(r)[1] - window_of(r)[0]).days for r in api.requests)
    assert min(spans) == 1
    assert len(ingestor.still_capped) == 3  # three single days still capped
    assert len(api.requests) == 5  # 3d, 1d, 2d, 1d, 1d


def test_split_window_halves_meet():
    start, end = season_bounds(2026)
    a, b = split_window((start, end))
    assert a[0] == start and a[1] == b[0] and b[1] == end


@pytest.mark.parametrize("season", [2019, 2024, 2026])
def test_season_windows_have_no_gaps(season):
    start, end = season_bounds(season)
    assert start == datetime(season - 1, 11, 1, tzinfo=UTC)
    assert end == datetime(season, 4, 16, tzinfo=UTC)  # Apr 15 is included
    for windows in (month_windows(season), block_windows(season)):
        assert windows[0][0] == start and windows[-1][1] == end
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
            assert prev_end == next_start
        assert all(a < b for a, b in windows)
    assert len(month_windows(season)) == 6
    assert all((b - a).days <= 10 for a, b in block_windows(season))


def test_5xx_retry_then_success(tmp_path, monkeypatch):
    attempts = {"n": 0}

    def reply(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=rows(1))

    sleeps: list[float] = []
    api = FakeApi(reply)
    assert run(tmp_path, monkeypatch, api, "--max-calls", "3", sleeps=sleeps) == 0
    assert len(api.requests) == 3  # one fetch, three attempts
    assert [r["status"] for r in manifest(tmp_path)] == ["retry", "retry", "ok"]
    assert len(sleeps) == 2  # backoff before each retry


def test_5xx_three_failures_is_an_error_row(tmp_path, monkeypatch):
    api = FakeApi(lambda r: httpx.Response(500))
    assert run(tmp_path, monkeypatch, api, "--max-calls", "3") == 1
    assert [r["status"] for r in manifest(tmp_path)] == ["retry", "retry", "error"]


def test_network_error_retries_then_succeeds(tmp_path, monkeypatch):
    attempts = {"n": 0}

    def reply(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=rows(1))

    assert run(tmp_path, monkeypatch, FakeApi(reply), "--max-calls", "1") == 0
    assert [r["status"] for r in manifest(tmp_path)] == ["ok"]


def test_401_stops_the_whole_run(tmp_path, monkeypatch):
    api = FakeApi(lambda r: httpx.Response(401))
    assert run(tmp_path, monkeypatch, api) == 1
    assert len(api.requests) == 1
    row = manifest(tmp_path)[0]
    assert row["status"] == "error" and row["http_status"] == "401"


def test_other_4xx_is_not_retried_and_run_continues(tmp_path, monkeypatch):
    def reply(request):
        return (
            httpx.Response(404)
            if request.url.path == "/conferences"
            else httpx.Response(200, json=[])
        )

    api = FakeApi(reply)
    assert run(tmp_path, monkeypatch, api) == 1  # an error row was written
    assert len(api.requests) == 8  # but everything else still ran


def test_missing_key_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    assert cbbd.main(["--data-dir", str(tmp_path), "--mode", "discover"]) == 1
    assert "not set" in capsys.readouterr().out


def test_output_contains_no_row_values(tmp_path, monkeypatch, capsys):
    api = FakeApi(lambda r: httpx.Response(200, json=rows(3)))
    run(tmp_path, monkeypatch, api)
    out = capsys.readouterr().out
    assert SENTINEL not in out
    assert "homeTeam:str" in out and "homePoints:int" in out  # names and types are fine
    assert "calls made this run: 8" in out


def test_backfill_order_newest_season_first_and_whole_run(tmp_path, monkeypatch):
    api = FakeApi(lambda r: httpx.Response(200, json=rows(1)))
    run(
        tmp_path, monkeypatch, api, "--seasons", "2025-2026", "--max-calls", "1000", mode="backfill"
    )
    teams = [r.url.params["season"] for r in api.requests if r.url.path == "/teams"]
    assert teams == ["2026", "2025"]
    # per season: 1 teams + 6*3 monthly + 17 player blocks
    assert len(api.requests) == 2 * (1 + 18 + len(block_windows(2026)))
    # a re-run costs nothing
    again = FakeApi()
    run(
        tmp_path,
        monkeypatch,
        again,
        "--seasons",
        "2025-2026",
        "--max-calls",
        "1000",
        mode="backfill",
    )
    assert again.requests == []


def test_parse_seasons():
    assert cbbd.parse_seasons("2019-2021") == [2019, 2020, 2021]
    assert cbbd.parse_seasons("2024, 2026") == [2024, 2026]
    assert cbbd.parse_seasons("2025") == [2025]


def test_manifest_path_never_inside_public_repo_paths(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, FakeApi(), "--max-calls", "1")
    assert Path(manifest(tmp_path)[0]["file"]).parts[:2] == ("cbbd", "raw")


# ---------- current mode (the season in progress) ----------


def fixed(when: datetime):
    return lambda: when


def calls_by_path(api: FakeApi) -> dict[str, list]:
    out: dict[str, list] = {}
    for request in api.requests:
        params = request.url.params
        key = request.url.path
        out.setdefault(key, []).append(
            window_of(request) if "startDateRange" in params else dict(params)
        )
    return out


def test_current_season_from_date():
    assert cbbd.current_season(datetime(2026, 11, 11, tzinfo=UTC)) == 2027
    assert cbbd.current_season(datetime(2027, 3, 20, tzinfo=UTC)) == 2027
    assert cbbd.current_season(datetime(2027, 7, 1, tzinfo=UTC)) == 2028


def test_current_early_season_fetches_live_games_and_lines_only(tmp_path, monkeypatch):
    api = FakeApi()
    now = datetime(2026, 11, 11, 10, 7, tzinfo=UTC)
    assert run(tmp_path, monkeypatch, api, mode="current", now=fixed(now)) == 0
    calls = calls_by_path(api)
    live = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC))
    assert calls == {"/teams": [{"season": "2027"}], "/games": [live], "/lines": [live]}

    # Same day again: everything is cached.
    api2 = FakeApi()
    run(tmp_path, monkeypatch, api2, mode="current", now=fixed(now))
    assert api2.requests == []

    # Next day: the live window ends a day later, so games and lines are fetched again,
    # and the first player block (Nov 1 to 5) is now 7 days old, so it settles.
    api3 = FakeApi()
    run(tmp_path, monkeypatch, api3, mode="current", now=fixed(now + timedelta(days=1)))
    later = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 21, tzinfo=UTC))
    first_block = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 5, tzinfo=UTC))
    assert calls_by_path(api3) == {
        "/games": [later],
        "/lines": [later],
        "/games/players": [first_block],
    }


def test_current_settles_old_windows_once_and_keeps_live_after_them(tmp_path, monkeypatch):
    api = FakeApi()
    now = datetime(2026, 12, 10, 10, 7, tzinfo=UTC)
    run(tmp_path, monkeypatch, api, mode="current", now=fixed(now))
    calls = calls_by_path(api)
    november = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    live = (datetime(2026, 12, 1, tzinfo=UTC), datetime(2026, 12, 19, tzinfo=UTC))
    assert calls["/games"] == [november, live]
    assert calls["/lines"] == [november, live]
    assert calls["/games/teams"] == [november]  # box stats only once settled
    # Player blocks that ended by Dec 3 (7 days before): Nov 1 to Dec 3, 4 days each.
    blocks = calls["/games/players"]
    assert len(blocks) == 8 and blocks[-1][1] == datetime(2026, 12, 3, tzinfo=UTC)
    # The settled windows are the same as backfill's, so backfill would skip them.
    assert november == month_windows(2027)[0]
    assert blocks == block_windows(2027)[:8]


def test_current_after_season_end_has_no_live_window(tmp_path, monkeypatch):
    api = FakeApi()
    now = datetime(2027, 5, 1, 10, 7, tzinfo=UTC)
    run(tmp_path, monkeypatch, api, "--max-calls", "1000", mode="current", now=fixed(now))
    games = calls_by_path(api)["/games"]
    assert games == month_windows(2027)  # all settled, no live window past Apr 16
