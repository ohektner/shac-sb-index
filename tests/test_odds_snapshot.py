"""Tests for the odds snapshot job. No network: httpx.MockTransport fakes the API."""

import csv
import gzip
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from shac.snapshots import odds

KEY = "SECRET-KEY-123"
FIXED_TIME = datetime(2026, 11, 9, 13, 17, 4, tzinfo=UTC)

# Invented games: fake teams, made-up numbers.
FAKE_GAMES = [
    {
        "id": "g1",
        "sport_key": "basketball_ncaab",
        "commence_time": "2026-11-10T01:00:00Z",
        "home_team": "Test State Bobcats",
        "away_team": "Sample Tech Owls",
        "bookmakers": [
            {
                "key": "bookone",
                "title": "Book One",
                "markets": [
                    {
                        "key": "spreads",
                        "last_update": "2026-11-09T13:00:00Z",
                        "outcomes": [
                            {"name": "Test State Bobcats", "price": -110, "point": -3.5},
                            {"name": "Sample Tech Owls", "price": -110, "point": 3.5},
                        ],
                    }
                ],
            },
            {"key": "booktwo", "title": "Book Two", "markets": []},
        ],
    },
    {
        "id": "g2",
        "sport_key": "basketball_ncaab",
        "commence_time": "2026-11-10T02:00:00Z",
        "home_team": "Example A&M Herons",
        "away_team": "Demo College Lynx",
        "bookmakers": [{"key": "bookone", "title": "Book One", "markets": []}],
    },
]

CREDIT_HEADERS = {
    "x-requests-remaining": "480",
    "x-requests-used": "20",
    "x-requests-last": "1",
}


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok_handler(body=None):
    def handler(request):
        return httpx.Response(
            200, json=FAKE_GAMES if body is None else body, headers=CREDIT_HEADERS
        )

    return handler


def read_log(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_job(client, tmp_path, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return odds.run(
        client,
        KEY,
        tmp_path / "data",
        tmp_path / "log.csv",
        now=lambda: FIXED_TIME,
        sleep=sleeps.append,
    )


def test_happy_path(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return ok_handler()(request)

    status, _ = run_job(make_client(handler), tmp_path)
    assert status == "ok"
    assert len(seen) == 1
    assert seen[0].url.params["apiKey"] == KEY
    assert seen[0].url.params["markets"] == "spreads"

    relative = "odds/raw/2026/11/09/odds_20261109T131704Z.json.gz"
    path = tmp_path / "data" / relative
    assert path.exists()
    raw = path.read_bytes()
    assert KEY.encode() not in raw and KEY.encode() not in gzip.decompress(raw)
    content = json.loads(gzip.decompress(raw))
    assert content["fetched_at"] == "2026-11-09T13:17:04Z"
    assert content["source"] == "the-odds-api"
    assert content["request"]["markets"] == "spreads"
    assert content["credits"] == {"remaining": 480, "used": 20, "last": 1}
    assert content["data"] == FAKE_GAMES

    (row,) = read_log(tmp_path / "log.csv")
    assert row["status"] == "ok"
    assert row["http_status"] == "200"
    assert row["n_games"] == "2"
    assert row["n_books"] == "2"
    assert row["credits_used"] == "20"
    assert row["credits_remaining"] == "480"
    assert row["file"] == relative
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["bytes"] == str(len(raw))
    log_text = (tmp_path / "log.csv").read_text(encoding="utf-8")
    assert "Bobcats" not in log_text and KEY not in log_text


def test_log_header_order(tmp_path):
    run_job(make_client(ok_handler()), tmp_path)
    header = (tmp_path / "log.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(odds.LOG_COLUMNS)


def test_empty_list_is_saved(tmp_path):
    status, _ = run_job(make_client(ok_handler([])), tmp_path)
    assert status == "ok"
    (row,) = read_log(tmp_path / "log.csv")
    assert row["n_games"] == "0"
    assert row["n_books"] == "0"
    path = tmp_path / "data" / row["file"]
    assert json.loads(gzip.decompress(path.read_bytes()))["data"] == []


def test_credit_guard_skips_without_calling_api(tmp_path):
    log = tmp_path / "log.csv"
    log.write_text(
        ",".join(odds.LOG_COLUMNS) + "\n2026-11-09T12:00:00Z,ok,200,1,1,495,5,f,h,1\n",
        encoding="utf-8",
    )
    calls = []

    def handler(request):
        calls.append(request)
        return ok_handler()(request)

    status, _ = run_job(make_client(handler), tmp_path)
    assert status == "skipped_low_credits"
    assert calls == []
    rows = read_log(log)
    assert rows[-1]["status"] == "skipped_low_credits"
    assert not (tmp_path / "data").exists()


def _log_row(stamp, status, http_status="200", remaining=""):
    return {
        "fetched_at_utc": stamp,
        "status": status,
        "http_status": http_status,
        "credits_remaining": remaining,
    }


def test_credit_guard_rules():
    recent = "2026-11-09T12:00:00Z"  # about an hour before FIXED_TIME
    old = "2026-11-08T12:00:00Z"  # more than 24 hours before FIXED_TIME
    skip = odds.should_skip_for_credits

    assert not skip([], FIXED_TIME)
    assert not skip([_log_row(recent, "ok", remaining="480")], FIXED_TIME)
    assert skip([_log_row(recent, "ok", remaining="5")], FIXED_TIME)
    # The API saying the quota is used up counts as low credits too.
    assert skip([_log_row(recent, "error", http_status="429")], FIXED_TIME)
    # Earlier skip rows don't hide the last real run...
    rows = [_log_row(recent, "ok", remaining="3"), _log_row(recent, "skipped_low_credits", "")]
    assert skip(rows, FIXED_TIME)
    # ...and after 24 hours the job tries again, so it recovers when credits reset.
    assert not skip([_log_row(old, "ok", remaining="3")], FIXED_TIME)
    assert not skip([_log_row(old, "error", http_status="429")], FIXED_TIME)
    # A later error with no credit count (a network failure, a failed push) doesn't
    # reset the guard: the last row that knew the credit count still decides.
    rows = [
        _log_row(recent, "ok", remaining="3"),
        _log_row(recent, "error", http_status="500"),
        _log_row(recent, "push_failed", http_status=""),
    ]
    assert skip(rows, FIXED_TIME)
    # Counts written like "3.0" are still read.
    assert skip([_log_row(recent, "ok", remaining="3.0")], FIXED_TIME)


def test_credit_headers_written_as_floats_are_read(tmp_path):
    def handler(request):
        return httpx.Response(
            200, json=[], headers={"x-requests-remaining": "480.0", "x-requests-used": "20.0"}
        )

    run_job(make_client(handler), tmp_path)
    (row,) = read_log(tmp_path / "log.csv")
    assert row["credits_remaining"] == "480"
    assert row["credits_used"] == "20"


def test_odd_shaped_games_are_still_saved_and_logged(tmp_path):
    # A bookmaker with no "key" and a game that is not a dict: save the raw data anyway.
    body = [{"id": "g1", "bookmakers": [{"title": "No Key"}]}, "not-a-game"]
    status, _ = run_job(make_client(ok_handler(body)), tmp_path)
    assert status == "ok"
    (row,) = read_log(tmp_path / "log.csv")
    assert row["n_games"] == "2"
    assert row["n_books"] == ""
    path = tmp_path / "data" / row["file"]
    assert json.loads(gzip.decompress(path.read_bytes()))["data"] == body


def test_stamp_is_taken_after_the_response_arrives(tmp_path):
    ticks = iter(datetime(2026, 11, 9, 13, m, 0, tzinfo=UTC) for m in (17, 18))
    odds.run(
        make_client(ok_handler()),
        KEY,
        tmp_path / "data",
        tmp_path / "log.csv",
        now=lambda: next(ticks),
    )
    (row,) = read_log(tmp_path / "log.csv")
    assert row["fetched_at_utc"] == "2026-11-09T13:18:00Z"
    assert "20261109T131800Z" in row["file"]


@pytest.mark.parametrize(
    ("handler_status", "expected_exit"),
    [(200, 0), (401, 1)],
)
def test_main_exit_codes(tmp_path, monkeypatch, handler_status, expected_exit):
    def handler(request):
        return httpx.Response(handler_status, json=[], headers=CREDIT_HEADERS)

    real_client = httpx.Client
    monkeypatch.setenv("ODDS_API_KEY", KEY)
    monkeypatch.setattr(
        odds.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler))
    )
    args = ["--data-dir", str(tmp_path / "data"), "--log", str(tmp_path / "log.csv")]
    assert odds.main(args) == expected_exit


def test_retries_5xx_then_succeeds(tmp_path):
    responses = [httpx.Response(503), httpx.Response(500)]

    def handler(request):
        if responses:
            return responses.pop(0)
        return ok_handler()(request)

    sleeps = []
    status, _ = run_job(make_client(handler), tmp_path, sleeps)
    assert status == "ok"
    assert sleeps == [5, 20]


def test_network_error_then_success(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom", request=request)
        return ok_handler()(request)

    status, _ = run_job(make_client(handler), tmp_path)
    assert status == "ok"
    assert len(attempts) == 2


@pytest.mark.parametrize("code", [401, 403, 422, 429])
def test_client_errors_are_not_retried(tmp_path, code):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(code, json={"message": "nope"})

    sleeps = []
    status, _ = run_job(make_client(handler), tmp_path, sleeps)
    assert status == "error"
    assert len(calls) == 1
    assert sleeps == []
    (row,) = read_log(tmp_path / "log.csv")
    assert row["status"] == "error"
    assert row["http_status"] == str(code)


def test_5xx_every_time_gives_error_after_three_attempts(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502)

    status, _ = run_job(make_client(handler), tmp_path)
    assert status == "error"
    assert len(calls) == 3


def test_key_is_scrubbed_from_error_output(tmp_path):
    def handler(request):
        # Real httpx errors can carry the full URL, query string included.
        raise httpx.ConnectError(f"failed for {request.url}", request=request)

    status, summary = run_job(make_client(handler), tmp_path)
    assert status == "error"
    assert KEY not in summary
    assert KEY not in (tmp_path / "log.csv").read_text(encoding="utf-8")
    with pytest.raises(odds.FetchError) as info:
        odds.fetch_odds(make_client(handler), KEY, sleep=lambda s: None)
    assert KEY not in str(info.value)
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_refuses_to_overwrite(tmp_path):
    run_job(make_client(ok_handler()), tmp_path)
    existing = tmp_path / "data" / "odds/raw/2026/11/09/odds_20261109T131704Z.json.gz"
    before = existing.read_bytes()
    status, _ = run_job(make_client(ok_handler([])), tmp_path)
    assert status == "error"
    assert existing.read_bytes() == before
    assert read_log(tmp_path / "log.csv")[-1]["status"] == "error"


def test_main_without_key_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    code = odds.main(["--data-dir", str(tmp_path / "d"), "--log", str(tmp_path / "log.csv")])
    assert code == 1
    assert "ODDS_API_KEY is not set" in capsys.readouterr().err
    assert not (tmp_path / "log.csv").exists()
