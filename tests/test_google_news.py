"""Tests for the Google News collector. No network: httpx.MockTransport with made-up feeds."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd

from shac.news import google_news as gn
from shac.snapshots.common import read_log_rows

NOW = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
SECRET_HEADLINE = "SENTINEL_HEADLINE_XYZ"
TEAMS = pd.DataFrame(
    {
        "team_id": [1, 2, 3, 4],
        "name": ["Alpha State", "Bravo", "Charlie Tech", "Delta"],
        "conference": "X",
        "first_season": 2019,
        "last_season": 2027,
    }
)


def feed(n: int, prefix: str = "g") -> str:
    items = "".join(
        f"<item><title>{SECRET_HEADLINE} {i} - Paper</title>"
        f"<link>https://news.google.com/rss/articles/{prefix}{i}</link>"
        f"<guid>{prefix}{i}</guid><pubDate>Thu, 19 Nov 2026 20:00:00 GMT</pubDate>"
        f'<description>x</description><source url="https://paper.example">Paper</source>'
        f"</item>"
        for i in range(n)
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'
    )


def write_games(data_dir, games):
    folder = data_dir / "cbbd" / "raw" / "games"
    folder.mkdir(parents=True, exist_ok=True)
    wrapped = {"fetched_at": "2026-11-20T10:07:00Z", "data": games}
    (folder / "games_live.json.gz").write_bytes(gzip.compress(json.dumps(wrapped).encode()))


def game(gid, home, away, hours_from_now, status="scheduled"):
    tip = NOW + timedelta(hours=hours_from_now)
    return {
        "id": gid,
        "homeTeamId": home,
        "awayTeamId": away,
        "startDate": tip.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": status,
    }


def run(tmp_path, handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    ok = gn.run(
        client,
        tmp_path,
        TEAMS,
        tmp_path / "log.csv",
        now=lambda: NOW,
        sleep=sleeps.append,
        gap=lambda: 5.5,
        **kwargs,
    )
    return ok, sleeps


def test_query_text():
    q = gn.build_query("Miami (OH)")
    assert q.startswith('"Miami (OH)" basketball (injury OR') and q.endswith("when:2d")


def test_only_teams_with_a_game_in_the_window(tmp_path):
    write_games(
        tmp_path,
        [
            game(1, 1, 2, 3),  # in window
            game(2, 3, 99, 30),  # team 99 is not D-I: only 3 counts
            game(3, 4, 1, 40),  # past the 36-hour window
            game(4, 4, 2, -5, status="final"),  # already played
        ],
    )
    assert gn.teams_playing_soon(tmp_path, TEAMS, NOW) == [
        (1, "Alpha State"),
        (2, "Bravo"),
        (3, "Charlie Tech"),
    ]


def test_run_saves_one_file_and_logs_counts_only(tmp_path, capsys):
    write_games(tmp_path, [game(1, 1, 2, 3)])
    seen_queries = []

    def handler(request):
        seen_queries.append(parse_qs(urlparse(str(request.url)).query)["q"][0])
        return httpx.Response(200, text=feed(3, prefix=f"t{len(seen_queries)}-"))

    ok, sleeps = run(tmp_path, handler)
    assert ok and sleeps == [5.5]  # one gap between the two requests
    assert seen_queries[0].startswith('"Alpha State" basketball')
    (row,) = read_log_rows(tmp_path / "log.csv")
    assert row["status"] == "ok" and row["n_queries"] == "2" and row["n_items"] == "6"
    assert row["n_new_items"] == "6"
    saved = json.loads(gzip.decompress((tmp_path / row["file"]).read_bytes()))
    assert [r["team_id"] for r in saved["data"]] == [1, 2]
    assert saved["data"][0]["items"][0]["published_at"] == "2026-11-19T20:00:00Z"
    assert "<rss" in saved["data"][0]["xml"]
    # Headlines stay in the private file: never in the public log or the output.
    assert SECRET_HEADLINE not in (tmp_path / "log.csv").read_text()
    assert SECRET_HEADLINE not in capsys.readouterr().out


def test_second_run_counts_only_new_items(tmp_path):
    write_games(tmp_path, [game(1, 1, 2, 10)])  # still ahead at the second run
    run(tmp_path, lambda r: httpx.Response(200, text=feed(3)))
    later = NOW + timedelta(hours=7)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=feed(4)))
    )
    gn.run(client, tmp_path, TEAMS, tmp_path / "log.csv", now=lambda: later, sleep=lambda s: None)
    rows = read_log_rows(tmp_path / "log.csv")
    assert rows[1]["n_items"] == "8" and rows[1]["n_new_items"] == "1"  # only g3 is new


def test_blocked_stops_the_run_keeps_what_it_got_and_fails(tmp_path):
    write_games(tmp_path, [game(1, 1, 2, 3), game(2, 3, 4, 5)])
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 2:
            return httpx.Response(429, text="Too many requests")
        return httpx.Response(200, text=feed(2))

    ok, _ = run(tmp_path, handler)
    assert not ok and len(calls) == 2  # stopped at the block, no third request
    (row,) = read_log_rows(tmp_path / "log.csv")
    assert row["status"] == "blocked" and row["http_status"] == "429"
    assert row["n_queries"] == "1" and row["file"]


def test_html_error_page_counts_as_blocked(tmp_path):
    write_games(tmp_path, [game(1, 1, 2, 3)])
    ok, _ = run(tmp_path, lambda r: httpx.Response(200, text="<html>unusual traffic</html>"))
    assert not ok
    (row,) = read_log_rows(tmp_path / "log.csv")
    assert row["status"] == "blocked" and row["file"] == ""


def test_max_queries_limits_the_run(tmp_path):
    write_games(tmp_path, [game(1, 1, 2, 3), game(2, 3, 4, 5)])
    calls = []
    run(tmp_path, lambda r: calls.append(1) or httpx.Response(200, text=feed(1)), max_queries=1)
    assert len(calls) == 1
