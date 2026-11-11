"""Find each D-I team's men's basketball news feed on its athletics site (task 1.11).

Run as (slow on purpose; about 30 to 45 minutes):
    uv run python -m shac.news.team_sites --data-dir <private repo> [--map-dir data]

Writes `data/team_sites.csv` (public: web addresses only) and
`data/team_sites_review.csv` (teams with no site or no feed found, for a hand look).

How:
1. Each team's athletics site comes from Wikidata (public domain, CC0): the "official
   website" of its men's basketball team entry. Wikidata entries are matched to CBBD teams
   by school + mascot ("Siena Saints men's basketball" = CBBD "Siena Saints").
2. For each site, robots.txt is read first. Every URL tried, and the final URL after any
   redirect, must be allowed for our client, and the site's Crawl-delay is honored
   (SIDEARM sites ask for 30 seconds).
3. Candidate feed URLs are tried until one returns RSS with items: the sport code from the
   Wikidata link if it has one, then the usual SIDEARM codes. Sites are visited in turn,
   so no site sees two requests closer than its Crawl-delay.

Owen chose SIDEARM team sites as the slow secondary news source on 2026-11-11
(DECISIONS.md). Nothing from the feeds is stored here; this only finds the addresses.
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import re
import sys
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from shac.snapshots.common import iso_z, utc_now

USER_AGENT = "shac-sb-index/0.1 (+https://github.com/ohektner/shac-sb-index)"
ROBOTS_AGENT = "shac-sb-index"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
# Wikidata item for the "NCAA Division I men's basketball" league.
DI_MBB_LEAGUE = "Q94861615"
SPORT_CODES = ["mbball", "m-baskbl", "mbb", "mbasketball", "basketball-men"]
DEFAULT_DELAY = 30.0  # seconds, if a site gives no Crawl-delay
MIN_GLOBAL_GAP = 2.0  # between any two requests, to any site
SITE_COLUMNS = [
    "team_id",
    "school",
    "wikidata_id",
    "site_url",
    "domain",
    "feed_url",
    "crawl_delay",
    "checked_at",
]
REVIEW_COLUMNS = ["team_id", "school", "site_url", "reason"]


def norm(text: str) -> str:
    """Lowercase, '&' -> 'and', punctuation to spaces, single spaces."""
    text = text.lower().replace("&", "and").replace("é", "e")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def label_base(label: str) -> str:
    """'Siena Saints men's basketball' -> 'siena saints'."""
    base = re.sub(r"\s+men'?s basketball.*$", "", label, flags=re.IGNORECASE)
    return norm(re.sub(r"\s+basketball.*$", "", base, flags=re.IGNORECASE))


def match_teams(teams: list[dict], entries: list[tuple[str, str, str]]) -> dict[int, tuple]:
    """CBBD team id -> (wikidata id, label, site) for each team we can match.

    `teams` are CBBD /teams records; `entries` are (wikidata id, label, site). An exact
    school + mascot match wins; otherwise a single entry whose name ends with the mascot
    and contains the school's first word. Ambiguous or missing teams are left out.
    """
    bases = [
        (label_base(lbl), qid, lbl, site)
        for qid, lbl, site in entries
        if "basketball" in lbl.lower() and "women" not in lbl.lower()
    ]
    out: dict[int, tuple] = {}
    for team in teams:
        key = norm(team["displayName"])
        exact = [b for b in bases if b[0] == key]
        if exact:
            out[team["id"]] = exact[0][1:]
            continue
        mascot, school = norm(team.get("mascot") or ""), norm(team["school"])
        cands = [
            b
            for b in bases
            if mascot and b[0].endswith(mascot) and school.split()[0] in b[0].split()
        ]
        if len(cands) == 1:
            out[team["id"]] = cands[0][1:]
    return out


def wikidata_entries(client: httpx.Client) -> list[tuple[str, str, str]]:
    """(wikidata id, English label, official website) for D-I men's basketball entries."""
    query = f"""SELECT ?team ?teamLabel ?site WHERE {{
      ?team wdt:P118 wd:{DI_MBB_LEAGUE} ; wdt:P856 ?site .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    response = client.get(
        WIKIDATA_SPARQL,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=120,
    )
    response.raise_for_status()
    return [
        (b["team"]["value"].rsplit("/", 1)[1], b["teamLabel"]["value"], b["site"]["value"])
        for b in response.json()["results"]["bindings"]
    ]


def sport_code_from_url(url: str) -> str | None:
    """The SIDEARM sport code in a team URL, if it has one ('path=mbball', '/sports/m-baskbl/')."""
    parsed = urlparse(url)
    path_param = parse_qs(parsed.query).get("path")
    match = None if path_param else re.search(r"/sports/([^/]+)", parsed.path)
    if path_param:
        code = path_param[0]
    elif match and match.group(1) not in ("mens-basketball", "basketball"):
        code = match.group(1)
    else:
        code = None
    # A few Wikidata entries point at the women's team page; never use a women's code.
    if code and (code.startswith("w") or "women" in code):
        return None
    return code


def candidate_feeds(site_url: str) -> list[str]:
    """Feed URLs to try, most likely first."""
    parsed = urlparse(site_url)
    root = f"https://{parsed.netloc}"
    codes = []
    from_url = sport_code_from_url(site_url)
    if from_url:
        codes.append(from_url)
    codes += [c for c in SPORT_CODES if c not in codes]
    return [f"{root}/rss?path={c}" for c in codes] + [f"{root}/rss.aspx?path={codes[0]}"]


def feed_item_count(text: str) -> int:
    """Number of items if `text` is an RSS feed, else -1."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return -1
    if root.tag != "rss" or root.find("channel") is None:
        return -1
    return len(root.findall("./channel/item"))


@dataclass
class Site:
    team_id: int
    school: str
    wikidata_id: str
    site_url: str
    domain: str
    todo: list[str] = field(default_factory=list)
    robots: urllib.robotparser.RobotFileParser | None = None
    robots_done: bool = False
    delay: float = DEFAULT_DELAY
    last_request: float = -1e9
    feed_url: str = ""
    reason: str = ""


class Throttle:
    """Keeps every site's Crawl-delay and a small gap between any two requests."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.last_any = -1e9

    def wait(self, site: Site) -> None:
        now = self.clock()
        ready = max(site.last_request + site.delay, self.last_any + MIN_GLOBAL_GAP)
        if ready > now:
            self.sleep(ready - now)
        site.last_request = self.last_any = self.clock()


def step(client: httpx.Client, site: Site, throttle: Throttle) -> bool:
    """Make one request for a site. Returns True when the site is finished."""
    if not site.robots_done:
        throttle.wait(site)
        site.robots_done = True
        try:
            response = client.get(f"https://{site.domain}/robots.txt")
        except httpx.HTTPError:
            site.reason = "robots.txt not reachable"
            return True
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        site.robots = parser
        delay = parser.crawl_delay(ROBOTS_AGENT)
        site.delay = max(float(delay), DEFAULT_DELAY) if delay else DEFAULT_DELAY
        candidates = candidate_feeds(site.site_url)
        site.todo = [u for u in candidates if parser.can_fetch(ROBOTS_AGENT, u)]
        if not site.todo:
            site.reason = "robots.txt disallows every feed URL"
            return True
        return False
    url = site.todo.pop(0)
    throttle.wait(site)
    try:
        response = client.get(url)
    except httpx.HTTPError:
        response = None
    if response is not None and response.status_code == 200:
        final = str(response.url)
        if site.robots is not None and not site.robots.can_fetch(ROBOTS_AGENT, final):
            site.reason = "feed redirects to a disallowed URL"
        elif feed_item_count(response.text) > 0:
            site.feed_url = url
            return True
    if response is not None and response.status_code in (403, 429):
        site.reason = f"HTTP {response.status_code}"
        return True
    if not site.todo:
        site.reason = site.reason or "no feed with items found"
        return True
    return False


def discover(client: httpx.Client, sites: list[Site], throttle: Throttle) -> None:
    """Visit the sites in turn until each one has a feed or has run out of candidates."""
    active = list(sites)
    while active:
        active.sort(key=lambda s: s.last_request + s.delay)
        site = active[0]
        if step(client, site, throttle):
            active.remove(site)


def latest_teams(data_dir: Path) -> list[dict]:
    """The newest saved CBBD /teams list (current D-I teams)."""
    files = sorted(glob.glob(str(data_dir / "cbbd" / "raw" / "teams" / "season-*.json.gz")))
    return json.loads(gzip.decompress(Path(files[-1]).read_bytes()))["data"]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=Path("data"))
    parser.add_argument("--limit", type=int, default=None, help="only the first N teams")
    args = parser.parse_args(argv)

    teams = latest_teams(args.data_dir)
    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        matched = match_teams(teams, wikidata_entries(client))
        by_id = {t["id"]: t for t in teams}
        sites = [
            Site(tid, by_id[tid]["school"], qid, url, urlparse(url).netloc)
            for tid, (qid, _label, url) in sorted(matched.items())
        ]
        if args.limit:
            sites = sites[: args.limit]
        print(f"{len(teams)} teams, {len(matched)} with a site; checking {len(sites)}.")
        discover(client, sites, Throttle())

    checked = iso_z(utc_now())
    found = [s for s in sites if s.feed_url]
    write_csv(
        args.map_dir / "team_sites.csv",
        SITE_COLUMNS,
        [
            {
                "team_id": s.team_id,
                "school": s.school,
                "wikidata_id": s.wikidata_id,
                "site_url": s.site_url,
                "domain": s.domain,
                "feed_url": s.feed_url,
                "crawl_delay": int(s.delay),
                "checked_at": checked,
            }
            for s in found
        ],
    )
    review = [
        {"team_id": s.team_id, "school": s.school, "site_url": s.site_url, "reason": s.reason}
        for s in sites
        if not s.feed_url
    ]
    if not args.limit:
        review += [
            {"team_id": t["id"], "school": t["school"], "site_url": "", "reason": "no site"}
            for t in teams
            if t["id"] not in matched
        ]
    review.sort(key=lambda r: r["team_id"])
    write_csv(args.map_dir / "team_sites_review.csv", REVIEW_COLUMNS, review)
    print(f"feeds found: {len(found)}; to review: {len(review)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
