"""Tests for the team-site feed finder and the SIDEARM news collector. No network."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd

from shac.news import sidearm, team_sites
from shac.snapshots.common import read_log_rows

NOW = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
SECRET = "SENTINEL_HEADLINE_XYZ"


def rss(n: int) -> str:
    items = "".join(
        f"<item><title>{SECRET} {i}</title><link>https://x.edu/news/{i}</link>"
        f"<guid>x{i}</guid><description>Preview {i}</description>"
        f"<pubDate>Thu, 19 Nov 2026 20:00:00 GMT</pubDate></item>"
        for i in range(n)
    )
    head = '<?xml version="1.0"?><rss version="2.0"><channel><title>MBB</title>'
    return f"{head}{items}</channel></rss>"


# ---------- team_sites ----------


def test_match_exact_then_mascot_and_school_word():
    teams = [
        {"id": 1, "displayName": "Siena Saints", "school": "Siena", "mascot": "Saints"},
        {
            "id": 2,
            "displayName": "Loyola Chicago Ramblers",
            "school": "Loyola Chicago",
            "mascot": "Ramblers",
        },
        {"id": 3, "displayName": "Nowhere Owls", "school": "Nowhere", "mascot": "Owls"},
    ]
    entries = [
        ("Q1", "Siena Saints men's basketball", "https://sienasaints.com/sports/mens-basketball"),
        (
            "Q2",
            "Loyola Ramblers men's basketball",
            "https://loyolaramblers.com/index.aspx?path=mbball",
        ),
        ("Q3", "Siena Saints women's basketball", "https://w.example"),
        ("Q4", "Some Player", "https://player.example"),
    ]
    out = team_sites.match_teams(teams, entries)
    assert out[1][0] == "Q1" and out[2][0] == "Q2" and 3 not in out


def test_candidate_feeds_use_the_url_sport_code_first():
    assert team_sites.candidate_feeds("http://www.rolltide.com/index.aspx?path=mbball")[0] == (
        "https://www.rolltide.com/rss?path=mbball"
    )
    first = team_sites.candidate_feeds("https://ucsbgauchos.com/sports/m-baskbl/index")[0]
    assert first == "https://ucsbgauchos.com/rss?path=m-baskbl"
    plain = team_sites.candidate_feeds("https://cyclones.com/sports/mens-basketball")
    assert plain[0] == "https://cyclones.com/rss?path=mbball"
    # A women's page listed for the men's team: its code is ignored.
    women = team_sites.candidate_feeds("https://friars.com/sports/womens-basketball")
    assert women[0] == "https://friars.com/rss?path=mbball"
    assert not any("women" in w or "wbb" in w for w in women)


def test_throttle_keeps_crawl_delay_per_site_and_a_global_gap():
    clock = [0.0]
    slept = []

    def sleep(s):
        slept.append(round(s, 3))
        clock[0] += s

    throttle = team_sites.Throttle(clock=lambda: clock[0], sleep=sleep)
    a = team_sites.Site(1, "A", "Q", "https://a.edu", "a.edu", delay=30)
    b = team_sites.Site(2, "B", "Q", "https://b.edu", "b.edu", delay=30)
    throttle.wait(a)  # first ever request: no wait
    throttle.wait(b)  # other site: only the 2-second global gap
    throttle.wait(a)  # same site again: up to 30 seconds after its last request
    assert slept == [2.0, 28.0]


def make_site(url="https://a.edu/sports/mens-basketball"):
    return team_sites.Site(1, "A", "Q", url, "a.edu", delay=0)


def test_discover_respects_robots_and_finds_the_first_feed_with_items():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /rss.aspx\nCrawl-delay: 30\n")
        if request.url.params.get("path") == "mbball":
            return httpx.Response(200, text=rss(0))  # a feed, but empty: keep trying
        if request.url.params.get("path") == "m-baskbl":
            return httpx.Response(200, text=rss(3))
        return httpx.Response(404)

    site = make_site()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    team_sites.discover(
        client, [site], team_sites.Throttle(clock=lambda: 0.0, sleep=lambda s: None)
    )
    assert site.feed_url == "https://a.edu/rss?path=m-baskbl" and site.delay == 30
    assert not any("rss.aspx" in c for c in calls)


def test_disallowed_redirect_target_is_not_used():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")
        if request.url.path == "/rss":
            return httpx.Response(302, headers={"Location": "https://a.edu/api/v2/rss/xml"})
        return httpx.Response(200, text=rss(2))

    site = make_site()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    team_sites.discover(
        client, [site], team_sites.Throttle(clock=lambda: 0.0, sleep=lambda s: None)
    )
    # Every /rss?path=... redirects into /api/, which robots.txt disallows, so only the
    # old-style rss.aspx URL (served directly) is accepted.
    assert site.feed_url == "https://a.edu/rss.aspx?path=mbball"


# ---------- sidearm collector ----------

TEAMS = pd.DataFrame({"team_id": [1, 2, 3], "name": ["A", "B", "C"]})
SITES = pd.DataFrame(
    {"team_id": [1, 2, 3], "feed_url": [f"https://t{i}.edu/rss?path=mbball" for i in (1, 2, 3)]}
)


def write_games(data_dir, pairs):
    folder = data_dir / "cbbd" / "raw" / "games"
    folder.mkdir(parents=True, exist_ok=True)
    games = [
        {
            "id": i,
            "homeTeamId": h,
            "awayTeamId": a,
            "startDate": (NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "status": "scheduled",
        }
        for i, (h, a) in enumerate(pairs)
    ]
    wrapped = {"fetched_at": "2026-11-20T10:07:00Z", "data": games}
    (folder / "g.json.gz").write_bytes(gzip.compress(json.dumps(wrapped).encode()))


def collect(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    gaps = []
    ok = sidearm.run(
        client,
        tmp_path,
        TEAMS,
        SITES,
        tmp_path / "log.csv",
        now=lambda: NOW,
        sleep=gaps.append,
        gap=lambda: 11.0,
    )
    return ok, gaps


def test_collect_reads_one_feed_per_team_and_logs_counts_only(tmp_path, capsys):
    write_games(tmp_path, [(1, 2)])
    ok, gaps = collect(tmp_path, lambda r: httpx.Response(200, text=rss(3)))
    assert ok and gaps == [11.0]
    (row,) = read_log_rows(tmp_path / "log.csv")
    assert row["n_teams"] == "2" and row["n_feeds_read"] == "2" and row["n_items"] == "6"
    saved = json.loads(gzip.decompress((tmp_path / row["file"]).read_bytes()))
    assert saved["data"][0]["items"][0]["summary"] == "Preview 0"
    assert SECRET not in (tmp_path / "log.csv").read_text()
    assert SECRET not in capsys.readouterr().out


def test_refusals_skip_the_site_and_three_stop_the_run(tmp_path):
    write_games(tmp_path, [(1, 2), (3, 99)])
    ok, _ = collect(tmp_path, lambda r: httpx.Response(429))
    assert not ok
    (row,) = read_log_rows(tmp_path / "log.csv")
    assert row["status"] == "stopped" and row["n_refused"] == "3" and row["file"] == ""
