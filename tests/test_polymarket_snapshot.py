"""Tests for the Polymarket snapshot job. No network: httpx.MockTransport, invented data."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime

import httpx

from shac.snapshots import polymarket

SENTINEL = "SENTINEL_TEAM_XYZ"
NOW = datetime(2026, 11, 10, 15, 30, 0, tzinfo=UTC)


def fixed_now() -> datetime:
    return NOW


def no_sleep(_seconds: float) -> None:
    pass


def make_event(i: int, spreads: int = 1) -> dict:
    """An invented event: one moneyline market plus `spreads` spread markets."""
    markets = [{"id": f"ml{i}", "sportsMarketType": "moneyline", "question": SENTINEL}]
    for k in range(spreads):
        markets.append(
            {
                "id": f"sp{i}_{k}",
                "sportsMarketType": "spreads",
                "question": f"Spread: {SENTINEL} (-{k + 1}.5)",
                "line": -(k + 1.5),
                "outcomes": json.dumps([SENTINEL, "Other"]),
                "outcomePrices": json.dumps(["0.5", "0.5"]),
                "clobTokenIds": json.dumps([f"tok{i}_{k}a", f"tok{i}_{k}b"]),
                "gameStartTime": "2026-11-11 00:00:00+00",
            }
        )
    return {"slug": f"cbb-test-{i}", "startTime": "2026-11-11T00:00:00Z", "markets": markets}


def paged_handler(total_events: int, requests: list | None = None):
    """A fake Gamma API that serves `total_events` events in pages of the requested size."""
    events = [make_event(i) for i in range(total_events)]

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=events[offset : offset + limit])

    return handler


def run_job(handler, tmp_path, sleep=no_sleep):
    client = polymarket.make_client(httpx.MockTransport(handler))
    log = tmp_path / "log.csv"
    result = polymarket.run(client, tmp_path / "data", log, now=fixed_now, sleep=sleep)
    return result, log


def read_log(log):
    with open(log, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_paging_stops_on_short_page(tmp_path):
    seen: list[httpx.Request] = []
    (status, summary), log = run_job(paged_handler(230, seen), tmp_path)
    assert status == "ok"
    # 100 + 100 + 30: three requests, the third was short, so no fourth.
    assert [r.url.params["offset"] for r in seen] == ["0", "100", "200"]
    row = read_log(log)[0]
    assert (row["n_pages"], row["n_events"], row["n_spread_markets"]) == ("3", "230", "230")
    assert "pages=3" in summary


def test_exactly_full_last_page_needs_one_more_request(tmp_path):
    seen: list[httpx.Request] = []
    (status, _), log = run_job(paged_handler(100, seen), tmp_path)
    assert status == "ok"
    assert len(seen) == 2  # the second page is empty, which is short
    assert read_log(log)[0]["n_pages"] == "2"


def test_request_params(tmp_path):
    seen: list[httpx.Request] = []
    run_job(paged_handler(5, seen), tmp_path)
    params = seen[0].url.params
    assert params["series_id"] == "10470"
    assert params["active"] == "true"
    assert params["closed"] == "false"
    assert seen[0].headers["user-agent"].startswith("shac-sb-index/")


def test_file_shape_and_path(tmp_path):
    (status, _), log = run_job(paged_handler(130), tmp_path)
    assert status == "ok"
    row = read_log(log)[0]
    assert row["file"] == "polymarket/raw/2026/11/10/events_20261110T153000Z.json.gz"
    path = tmp_path / "data" / row["file"]
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        saved = json.load(handle)
    assert set(saved) == {"fetched_at", "source", "request", "pages"}
    assert saved["fetched_at"] == "2026-11-10T15:30:00Z"
    assert saved["source"] == "polymarket-gamma"
    assert [len(p) for p in saved["pages"]] == [100, 30]
    # Raw responses are kept unchanged, including the JSON-string fields.
    assert saved["pages"][0][0] == make_event(0)
    assert int(row["bytes"]) == path.stat().st_size


def test_never_overwrites(tmp_path):
    run_job(paged_handler(5), tmp_path)
    # Same fake clock, so the same file name: the second run must fail, not overwrite.
    (status, _), log = run_job(paged_handler(5), tmp_path)
    assert status == "error"
    assert read_log(log)[-1]["status"] == "error"


def test_log_and_summary_hold_counts_only(tmp_path, capsys):
    data_dir = tmp_path / "data"
    log = tmp_path / "log.csv"
    client = polymarket.make_client(httpx.MockTransport(paged_handler(7)))
    status, summary = polymarket.run(client, data_dir, log, now=fixed_now, sleep=no_sleep)
    print(summary)
    assert status == "ok"
    assert SENTINEL not in log.read_text(encoding="utf-8")
    assert SENTINEL not in capsys.readouterr().out
    assert list(read_log(log)[0]) == polymarket.LOG_COLUMNS


def test_blocked_html_page(tmp_path):
    def handler(request):
        return httpx.Response(403, text="<!DOCTYPE html><html>Just a moment...</html>")

    (status, summary), log = run_job(handler, tmp_path)
    assert status == "blocked"
    row = read_log(log)[0]
    assert row["status"] == "blocked"
    assert row["http_status"] == "403"
    assert not (tmp_path / "data").exists()
    assert "blocked" in summary


def test_blocked_html_with_200(tmp_path):
    def handler(request):
        return httpx.Response(200, text="  <html><body>check your browser</body></html>")

    (status, _), _ = run_job(handler, tmp_path)
    assert status == "blocked"


def test_retries_5xx_then_succeeds(tmp_path):
    calls = {"n": 0}
    sleeps: list[float] = []
    ok = paged_handler(3)

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, text="try later")
        return ok(request)

    (status, _), log = run_job(handler, tmp_path, sleep=sleeps.append)
    assert status == "ok"
    assert calls["n"] == 3
    assert sleeps == polymarket.BACKOFF_SECONDS
    assert read_log(log)[0]["n_events"] == "3"


def test_gives_up_after_three_5xx(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(502, text="bad gateway")

    (status, _), log = run_job(handler, tmp_path)
    assert status == "error"
    assert calls["n"] == 3
    assert read_log(log)[0]["http_status"] == "502"


def test_4xx_is_not_retried(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(422, json={"error": "nope"})

    (status, _), log = run_job(handler, tmp_path)
    assert status == "error"
    assert calls["n"] == 1
    assert read_log(log)[0]["http_status"] == "422"


def test_network_error_is_retried_and_message_is_scrubbed(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("boom https://secret.example/?x=1", request=request)

    (status, summary), _ = run_job(handler, tmp_path)
    assert status == "error"
    assert calls["n"] == 3
    assert "ConnectError" in summary
    assert "secret.example" not in summary


def test_non_list_response_is_an_error(tmp_path):
    (status, _), _ = run_job(lambda request: httpx.Response(200, json={"oops": 1}), tmp_path)
    assert status == "error"


def test_pauses_between_pages(tmp_path):
    sleeps: list[float] = []
    run_job(paged_handler(150), tmp_path, sleep=sleeps.append)
    assert sleeps == [polymarket.REQUEST_GAP_SECONDS]


def test_json_string_fields_are_parsed():
    market = make_event(1)["markets"][1]
    assert polymarket.json_list(market["outcomes"]) == [SENTINEL, "Other"]
    assert polymarket.json_list(market["clobTokenIds"]) == ["tok1_0a", "tok1_0b"]
    assert polymarket.json_list(["already", "a list"]) == ["already", "a list"]
    assert polymarket.json_list("not json") == []
    assert polymarket.json_list(None) == []


def test_parse_when_handles_both_formats():
    expected = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    assert polymarket.parse_when("2026-11-11 00:00:00+00") == expected
    assert polymarket.parse_when("2026-11-11T00:00:00Z") == expected
    assert polymarket.parse_when("2026-11-11T00:00:00+00:00") == expected
    assert polymarket.parse_when("nonsense") is None
    assert polymarket.parse_when(None) is None


def test_main_exit_codes(tmp_path, monkeypatch):
    def ok_client(transport=None):
        return httpx.Client(transport=httpx.MockTransport(paged_handler(2)))

    monkeypatch.setattr(polymarket, "make_client", ok_client)
    args = ["--data-dir", str(tmp_path / "d"), "--log", str(tmp_path / "l.csv")]
    assert polymarket.main(args) == 0

    def blocked_client(transport=None):
        return httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(403, text="<html>no"))
        )

    monkeypatch.setattr(polymarket, "make_client", blocked_client)
    assert polymarket.main(args) == 1
