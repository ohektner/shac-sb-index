"""Tests for the Polymarket history job. No network: httpx.MockTransport, invented data."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from shac.ingest import polymarket_history as ph
from shac.snapshots import polymarket

SENTINEL = "SENTINEL_TEAM_XYZ"
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def no_sleep(_seconds: float) -> None:
    pass


def stamp(moment: datetime) -> str:
    """Format like the API's gameStartTime: 2026-11-09 18:00:00+00."""
    return moment.strftime("%Y-%m-%d %H:%M:%S+00")


def spread(market_id: str, start: datetime, created: datetime | None = None, tokens=None) -> dict:
    return {
        "id": market_id,
        "sportsMarketType": "spreads",
        "question": f"Spread: {SENTINEL} (-3.5)",
        "line": -3.5,
        "outcomes": json.dumps([SENTINEL, "Other"]),
        "clobTokenIds": json.dumps(tokens or [f"{market_id}-a", f"{market_id}-b"]),
        "gameStartTime": stamp(start),
        "createdAt": (created or start - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.123456Z"),
    }


def event(slug: str, markets: list[dict], start: datetime | None = None) -> dict:
    start = start or NOW - timedelta(days=1)
    return {"slug": slug, "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "markets": markets}


def write_snapshot(data_dir: Path, events: list[dict], name="events_20261110T100000Z") -> None:
    """Save a fake live-snapshot file the way shac.snapshots.polymarket does."""
    path = data_dir / "polymarket" / "raw" / "2026" / "11" / "10" / f"{name}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": "x", "source": "polymarket-gamma", "request": {}, "pages": [events]}
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))


class FakeClob:
    """A fake CLOB API. Records every request; every answer is a tiny price history."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"history": [{"t": 1, "p": 0.5}, {"t": 2, "p": 0.6}]})

    @property
    def tokens(self) -> list[str]:
        return [r.url.params["market"] for r in self.requests]


def run_main(data_dir, api, *extra, mode="recent"):
    argv = ["--data-dir", str(data_dir), "--mode", mode, *extra]
    return ph.main(argv, transport=httpx.MockTransport(api), clock=clock, sleep=no_sleep)


def read_manifest(data_dir) -> list[dict]:
    path = data_dir / "polymarket" / "history" / "manifest.csv"
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_only_spread_markets_and_both_tokens(tmp_path):
    start = NOW - timedelta(hours=10)
    moneyline = {"id": "ml", "sportsMarketType": "moneyline", "clobTokenIds": '["m1", "m2"]'}
    total = {"id": "tot", "sportsMarketType": "totals", "clobTokenIds": '["t1", "t2"]'}
    write_snapshot(tmp_path, [event("g1", [moneyline, spread("sp1", start), total])])
    api = FakeClob()
    assert run_main(tmp_path, api) == 0
    assert api.tokens == ["sp1-a", "sp1-b"]


def test_window_and_params(tmp_path):
    start = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
    # Created 30 h before tip-off: later than tip-off - 48 h, so creation is the start.
    created = start - timedelta(hours=30)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start, created=created)])])
    api = FakeClob()
    run_main(tmp_path, api)
    params = api.requests[0].url.params
    assert api.requests[0].url.host == "clob.polymarket.com"
    assert api.requests[0].url.path == "/prices-history"
    assert params["fidelity"] == "5"
    expected_start = (created.replace(microsecond=123456)).timestamp()
    assert abs(int(params["startTs"]) - expected_start) < 1
    assert int(params["endTs"]) == int((start + timedelta(hours=4)).timestamp())


def test_window_uses_48_hours_when_market_is_older():
    start = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
    old = spread("sp1", start, created=start - timedelta(days=5))
    market = next(polymarket.spread_markets([event("g1", [old])]))
    window_start, window_end = ph.history_window(market)
    assert window_start == start - timedelta(hours=48)
    assert window_end == start + timedelta(hours=4)


def test_skips_games_not_yet_six_hours_past_start(tmp_path):
    just_started = NOW - timedelta(hours=5, minutes=59)
    old_enough = NOW - timedelta(hours=6, minutes=1)
    upcoming = NOW + timedelta(hours=3)
    write_snapshot(
        tmp_path,
        [
            event(
                "g1",
                [
                    spread("recent", just_started),
                    spread("done", old_enough),
                    spread("future", upcoming),
                ],
            )
        ],
    )
    api = FakeClob()
    run_main(tmp_path, api)
    assert api.tokens == ["done-a", "done-b"]


def test_file_contents_and_path(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("cbb-a-b-2026-11-10", [spread("sp1", start)])])
    run_main(tmp_path, FakeClob())
    files = sorted((tmp_path / "polymarket" / "history").rglob("*.json.gz"))
    assert [f.name for f in files] == [
        "sp1-a_20261110T120000Z.json.gz",
        "sp1-b_20261110T120000Z.json.gz",
    ]
    saved = json.loads(gzip.decompress(files[0].read_bytes()))
    assert set(saved) == {"fetched_at", "source", "params", "market", "data"}
    assert files[0].parent == tmp_path / "polymarket" / "history" / start.strftime("%Y/%m/%d")
    assert read_manifest(tmp_path)[0]["file"].startswith(
        "polymarket/history/" + start.strftime("%Y/%m/%d") + "/"
    )
    assert saved["source"] == "polymarket-clob"
    assert saved["data"] == {"history": [{"t": 1, "p": 0.5}, {"t": 2, "p": 0.6}]}
    market = saved["market"]
    assert market["event_slug"] == "cbb-a-b-2026-11-10"
    assert market["market_id"] == "sp1"
    assert market["line"] == -3.5
    # The JSON-string fields are stored as real lists.
    assert market["outcomes"] == [SENTINEL, "Other"]
    assert market["token_id"] == "sp1-a"
    assert market["game_start"] == iso(start)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_manifest_skip_on_rerun(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start), spread("sp2", start)])])
    api = FakeClob()
    assert run_main(tmp_path, api) == 0
    assert len(api.requests) == 4
    assert len(read_manifest(tmp_path)) == 4

    second = FakeClob()
    assert run_main(tmp_path, second) == 0
    assert second.requests == []
    assert len(read_manifest(tmp_path)) == 4  # nothing new recorded
    assert len(list((tmp_path / "polymarket" / "history").rglob("*.json.gz"))) == 4


def test_new_markets_are_fetched_on_a_later_run(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start)])])
    run_main(tmp_path, FakeClob())
    write_snapshot(
        tmp_path,
        [event("g1", [spread("sp1", start), spread("sp2", start)])],
        name="events_20261110T110000Z",
    )
    api = FakeClob()
    run_main(tmp_path, api)
    assert api.tokens == ["sp2-a", "sp2-b"]


def test_max_calls_stops_the_run(tmp_path, capsys):
    start = NOW - timedelta(hours=10)
    markets = [spread(f"sp{i}", start) for i in range(5)]
    write_snapshot(tmp_path, [event("g1", markets)])
    api = FakeClob()
    assert run_main(tmp_path, api, "--max-calls", "3") == 0
    assert len(api.requests) == 3
    assert "max-calls" in capsys.readouterr().out
    # The next run picks up where this one stopped.
    again = FakeClob()
    run_main(tmp_path, again, "--max-calls", "100")
    assert len(again.requests) == 7


def test_oldest_game_first(tmp_path):
    late = NOW - timedelta(hours=8)
    early = NOW - timedelta(hours=30)
    write_snapshot(tmp_path, [event("g1", [spread("late", late), spread("early", early)])])
    api = FakeClob()
    run_main(tmp_path, api)
    assert api.tokens[0] == "early-a"


def test_output_has_no_team_names(tmp_path, capsys):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start)])])
    run_main(tmp_path, FakeClob())
    out = capsys.readouterr().out
    assert SENTINEL not in out
    manifest_text = (tmp_path / "polymarket" / "history" / "manifest.csv").read_text("utf-8")
    assert SENTINEL not in manifest_text


def test_5xx_is_retried_then_recorded(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start, tokens=["only"])])])
    calls = {"n": 0}

    def api(request):
        calls["n"] += 1
        return httpx.Response(500, text="oops")

    assert run_main(tmp_path, api) == 1
    assert calls["n"] == 3
    statuses = [r["status"] for r in read_manifest(tmp_path)]
    assert statuses == ["retry", "retry", "error"]


def test_blocked_stops_the_whole_run(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start), spread("sp2", start)])])
    calls = {"n": 0}

    def api(request):
        calls["n"] += 1
        return httpx.Response(403, text="<html>Just a moment</html>")

    assert run_main(tmp_path, api) == 1
    assert calls["n"] == 1


def test_4xx_is_recorded_and_not_retried(tmp_path):
    start = NOW - timedelta(hours=10)
    write_snapshot(tmp_path, [event("g1", [spread("sp1", start, tokens=["bad"])])])
    calls = {"n": 0}

    def api(request):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad token"})

    assert run_main(tmp_path, api) == 1
    assert calls["n"] == 1
    assert [r["status"] for r in read_manifest(tmp_path)] == ["error"]


def test_closed_listing_pages_are_not_mistaken_for_snapshots(tmp_path):
    start = NOW - timedelta(hours=10)
    closed = tmp_path / "polymarket" / "raw" / "closed" / "events_fake.json.gz"
    closed.parent.mkdir(parents=True)
    closed.write_bytes(
        gzip.compress(json.dumps({"pages": [[event("c", [spread("zz", start)])]]}).encode())
    )
    api = FakeClob()
    run_main(tmp_path, api)
    assert api.requests == []


# ---- backfill --------------------------------------------------------------------------


class FakeBoth:
    """Serves closed-event listings (Gamma) and price history (CLOB) for backfill tests."""

    def __init__(self, events: list[dict]):
        self.events = events
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "clob.polymarket.com":
            return httpx.Response(200, json={"history": []})
        params = request.url.params
        low = polymarket.parse_when(params["start_time_min"])
        high = polymarket.parse_when(params["start_time_max"])
        inside = [e for e in self.events if low <= polymarket.parse_when(e["startTime"]) < high]
        offset = int(params["offset"])
        return httpx.Response(200, json=inside[offset : offset + int(params["limit"])])

    @property
    def listing_requests(self):
        return [r for r in self.requests if r.url.host == "gamma-api.polymarket.com"]

    @property
    def history_requests(self):
        return [r for r in self.requests if r.url.host == "clob.polymarket.com"]


def test_backfill_lists_closed_events_and_fetches_history(tmp_path):
    in_season = datetime(2025, 12, 5, 1, 0, tzinfo=UTC)
    outside = datetime(2025, 10, 20, 1, 0, tzinfo=UTC)  # before the season: never asked for
    events = [
        event("in", [spread("sp1", in_season)], start=in_season),
        event("out", [spread("sp2", outside)], start=outside),
    ]
    api = FakeBoth(events)
    assert run_main(tmp_path, api, mode="backfill") == 0
    # 24 weekly windows, all small enough for one page each.
    assert len(api.listing_requests) == 24
    first = api.listing_requests[0].url.params
    assert first["closed"] == "true"
    assert first["series_id"] == "10470"
    assert first["start_time_min"] == "2025-11-01T00:00:00Z"
    assert api.listing_requests[-1].url.params["start_time_max"] == "2026-04-16T00:00:00Z"
    assert [r.url.params["market"] for r in api.history_requests] == ["sp1-a", "sp1-b"]
    saved = list((tmp_path / "polymarket" / "raw" / "closed").glob("*.json.gz"))
    assert len(saved) == 24

    # A second run makes no calls at all: listing pages and history are all cached.
    again = FakeBoth(events)
    assert run_main(tmp_path, again, mode="backfill") == 0
    assert again.requests == []


def test_backfill_pages_a_busy_week(tmp_path):
    day = datetime(2025, 11, 5, 1, 0, tzinfo=UTC)
    events = [event(f"e{i}", [], start=day) for i in range(130)]
    api = FakeBoth(events)
    run_main(tmp_path, api, "--max-calls", "30", mode="backfill")
    offsets = [r.url.params["offset"] for r in api.listing_requests[:2]]
    assert offsets == ["0", "100"]


def test_backfill_respects_max_calls_across_listing_and_history(tmp_path):
    api = FakeBoth([])
    assert run_main(tmp_path, api, "--max-calls", "5", mode="backfill") == 0
    assert len(api.requests) == 5
