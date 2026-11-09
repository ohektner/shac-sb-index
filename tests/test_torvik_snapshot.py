"""Tests for the Torvik snapshot job. No network: httpx.MockTransport fakes the site."""

import csv
import gzip
import hashlib
from datetime import UTC, datetime

import httpx

from shac.snapshots import torvik

FIXED_TIME = datetime(2026, 11, 9, 11, 43, 0, tzinfo=UTC)
# Made-up headerless CSV rows, including a non-ASCII character to prove bytes are untouched.
TEAM_CSV = b"1,Test State,0.5,12.3\n2,Sample Tech,0.4,10.1\n3,Caf\xc3\xa9 U,0.3,9.9\n"
SKED_CSV = b"11/10/26,Test State,Sample Tech,70,65\n"


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def good_handler(request):
    body = TEAM_CSV if "team_results" in request.url.path else SKED_CSV
    return httpx.Response(200, content=body, headers={"content-type": "text/csv"})


def run_job(client, tmp_path, sleeps):
    return torvik.run(
        client,
        tmp_path / "data",
        tmp_path / "log.csv",
        season=2027,
        now=lambda: FIXED_TIME,
        sleep=sleeps.append,
    )


def read_log(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_happy_path(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return good_handler(request)

    sleeps = []
    assert run_job(make_client(handler), tmp_path, sleeps) is True

    assert [r.url.path for r in requests] == ["/2027_team_results.csv", "/2027_super_sked.csv"]
    assert all("shac-sb-index" in r.headers["user-agent"] for r in requests)
    assert any(s >= 10 for s in sleeps)

    folder = tmp_path / "data" / "torvik/raw/2026/11/09"
    team_file = folder / "2027_team_results_20261109T114300Z.csv.gz"
    sked_file = folder / "2027_super_sked_20261109T114300Z.csv.gz"
    assert gzip.decompress(team_file.read_bytes()) == TEAM_CSV
    assert gzip.decompress(sked_file.read_bytes()) == SKED_CSV

    rows = read_log(tmp_path / "log.csv")
    assert [r["status"] for r in rows] == ["ok", "ok"]
    assert rows[0]["source_file"] == "2027_team_results.csv"
    assert rows[0]["n_lines"] == "3"
    assert rows[0]["sha256"] == hashlib.sha256(team_file.read_bytes()).hexdigest()
    assert rows[0]["file"] == "torvik/raw/2026/11/09/2027_team_results_20261109T114300Z.csv.gz"


def test_cloudflare_page_is_blocked_and_nothing_saved(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            content=b"<!DOCTYPE html><html><body>Verifying your browser</body></html>",
            headers={"content-type": "text/html"},
        )

    assert run_job(make_client(handler), tmp_path, []) is False
    rows = read_log(tmp_path / "log.csv")
    assert [r["status"] for r in rows] == ["blocked", "blocked"]
    assert rows[0]["file"] == ""
    assert not (tmp_path / "data").exists()


def test_blocked_is_detected_from_the_body():
    assert torvik.looks_blocked(b"<html><head>")
    assert torvik.looks_blocked(b"  <!doctype html>")
    assert torvik.looks_blocked(b"Just a moment...")
    assert not torvik.looks_blocked(TEAM_CSV)


def test_browser_check_with_403_is_blocked_not_error(tmp_path):
    def handler(request):
        return httpx.Response(403, content=b"<html><title>Just a moment...</title></html>")

    assert run_job(make_client(handler), tmp_path, []) is False
    rows = read_log(tmp_path / "log.csv")
    assert [r["status"] for r in rows] == ["blocked", "blocked"]
    assert not (tmp_path / "data").exists()


def test_real_csv_labelled_as_html_is_still_saved(tmp_path):
    def handler(request):
        return httpx.Response(200, content=TEAM_CSV, headers={"content-type": "text/html"})

    assert run_job(make_client(handler), tmp_path, []) is True
    rows = read_log(tmp_path / "log.csv")
    assert [r["status"] for r in rows] == ["ok", "ok"]


def test_one_retry_after_30s_on_5xx(tmp_path):
    state = {"first": True}

    def handler(request):
        if "team_results" in request.url.path and state["first"]:
            state["first"] = False
            return httpx.Response(503)
        return good_handler(request)

    sleeps = []
    assert run_job(make_client(handler), tmp_path, sleeps) is True
    assert 30 in sleeps


def test_persistent_5xx_is_error(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    assert run_job(make_client(handler), tmp_path, []) is False
    assert len(calls) == 4  # two files, two attempts each
    assert [r["status"] for r in read_log(tmp_path / "log.csv")] == ["error", "error"]


def test_log_header_written_once_across_two_runs(tmp_path):
    run_job(make_client(good_handler), tmp_path, [])
    # Second run at a later time so file names do not collide.
    torvik.run(
        make_client(good_handler),
        tmp_path / "data",
        tmp_path / "log.csv",
        now=lambda: datetime(2026, 11, 10, 11, 43, 0, tzinfo=UTC),
        sleep=lambda s: None,
    )
    lines = (tmp_path / "log.csv").read_text(encoding="utf-8").splitlines()
    assert lines.count(",".join(torvik.LOG_COLUMNS)) == 1
    assert len(lines) == 5
