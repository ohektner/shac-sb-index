"""Tests for the Torvik Time Machine backfill. No network: httpx.MockTransport fakes the site."""

import gzip
import json
from datetime import UTC, date, datetime

import httpx
import pandas as pd
import pytest

from shac.ingest import torvik_timemachine as tm
from shac.snapshots.common import read_log_rows
from shac.tables.build import write_parquet

FIXED_TIME = datetime(2026, 11, 10, 22, 0, 0, tzinfo=UTC)
# Made-up ratings rows: 360 teams, a few fields each.
RATINGS = json.dumps([[i, f"Team {i}", 0.5] for i in range(360)]).encode()
SKED_CSV = b"11/10/26,Test State,Sample Tech,70,65\n" * 1200


def write_games(data_dir, rows):
    """Write a tiny games table: (season, tip time in UTC)."""
    df = pd.DataFrame(rows, columns=["season", "tip_at"])
    df["tip_at"] = pd.to_datetime(df["tip_at"], utc=True)
    write_parquet(df, data_dir / "tables" / "games.parquet")


@pytest.fixture
def data_dir(tmp_path):
    # Season 2019: games on Nov 6 and Nov 8 (Eastern). The 01:00 UTC tip on Nov 9 is
    # still Nov 8 in Eastern time, so the last day is Nov 8.
    write_games(
        tmp_path,
        [(2019, "2018-11-06T23:00:00Z"), (2019, "2018-11-09T01:00:00Z")],
    )
    return tmp_path


def good_handler(request):
    if request.url.path.endswith("_super_sked.csv"):
        return httpx.Response(200, content=SKED_CSV)
    return httpx.Response(200, content=gzip.compress(RATINGS))


def run_job(handler, data_dir, **kwargs):
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok = tm.run(
        client,
        data_dir,
        [2019],
        now=lambda: FIXED_TIME,
        sleep=sleeps.append,
        gap=lambda: 21.0,
        **kwargs,
    )
    return ok, sleeps


def manifest(data_dir):
    return read_log_rows(data_dir / tm.MANIFEST)


def test_season_dates_start_the_day_before():
    days = tm.season_dates(date(2018, 11, 6), date(2018, 11, 8))
    assert days == [date(2018, 11, 5), date(2018, 11, 6), date(2018, 11, 7), date(2018, 11, 8)]


def test_happy_path(data_dir):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return good_handler(request)

    ok, sleeps = run_job(handler, data_dir)
    assert ok is True
    assert requests == [
        "/2019_super_sked.csv",
        "/timemachine/team_results/20181105_team_results.json.gz",
        "/timemachine/team_results/20181106_team_results.json.gz",
        "/timemachine/team_results/20181107_team_results.json.gz",
        "/timemachine/team_results/20181108_team_results.json.gz",
    ]
    # One wait before every request but the first, never shorter than the minimum.
    assert len(sleeps) == 4 and all(s >= tm.MIN_GAP_SECONDS for s in sleeps)

    saved = data_dir / "torvik/timemachine/2019/20181106_team_results.json.gz"
    assert json.loads(gzip.decompress(saved.read_bytes())) == json.loads(RATINGS)
    sked = data_dir / "torvik/raw/seasons/2019_super_sked_20261110T220000Z.csv.gz"
    assert gzip.decompress(sked.read_bytes()) == SKED_CSV

    rows = manifest(data_dir)
    assert [r["status"] for r in rows] == ["ok"] * 5
    assert rows[1]["n_rows"] == "360"


def test_unzipped_response_is_stored_gzipped(data_dir):
    def handler(request):
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, content=SKED_CSV)
        return httpx.Response(200, content=RATINGS)

    assert run_job(handler, data_dir)[0] is True
    saved = data_dir / "torvik/timemachine/2019/20181105_team_results.json.gz"
    assert gzip.decompress(saved.read_bytes()) == RATINGS


def test_rerun_makes_zero_requests(data_dir):
    run_job(good_handler, data_dir)
    requests = []

    def handler(request):
        requests.append(request)
        return good_handler(request)

    ok, sleeps = run_job(handler, data_dir)
    assert ok is True
    assert requests == [] and sleeps == []


def test_missing_day_is_recorded_and_not_asked_again(data_dir):
    def handler(request):
        if "20181107" in request.url.path:
            return httpx.Response(404, content=b"not found")
        return good_handler(request)

    assert run_job(handler, data_dir)[0] is True
    statuses = {r["source"]: r["status"] for r in manifest(data_dir)}
    assert statuses[tm.timemachine_source(date(2018, 11, 7))] == "missing"

    requests = []
    run_job(lambda r: requests.append(r) or good_handler(r), data_dir)
    assert requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, content=b"Forbidden"),
        httpx.Response(429, content=b"slow down"),
        httpx.Response(200, content=b"<!DOCTYPE html><title>Just a moment...</title>"),
    ],
)
def test_block_stops_the_run_and_resumes_later(data_dir, response):
    requests = []

    def blocking(request):
        requests.append(request.url.path)
        if "20181106" in request.url.path:
            return response
        return good_handler(request)

    ok, _ = run_job(blocking, data_dir)
    assert ok is False
    # Stopped right at the blocked file: nothing after it was asked for.
    assert requests[-1].endswith("20181106_team_results.json.gz")
    assert manifest(data_dir)[-1]["status"] == "blocked"

    # The next run starts at the blocked file, not before it.
    requests.clear()
    ok, _ = run_job(lambda r: requests.append(r.url.path) or good_handler(r), data_dir)
    assert ok is True
    assert requests[0].endswith("20181106_team_results.json.gz")
    assert len(requests) == 3


def test_bad_file_is_an_error_and_stops(data_dir):
    def handler(request):
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, content=SKED_CSV)
        return httpx.Response(200, content=b"[1, 2, 3]")  # far too few teams

    ok, _ = run_job(handler, data_dir)
    assert ok is False
    assert manifest(data_dir)[-1]["status"] == "error"
    assert not (data_dir / "torvik/timemachine/2019").exists()


def test_max_files_limits_the_run(data_dir):
    requests = []
    ok, _ = run_job(lambda r: requests.append(r) or good_handler(r), data_dir, max_files=2)
    assert ok is True
    assert len(requests) == 2


def test_unknown_season_is_refused(data_dir):
    client = httpx.Client(transport=httpx.MockTransport(good_handler))
    with pytest.raises(ValueError):
        tm.run(client, data_dir, [2030], sleep=lambda s: None)
