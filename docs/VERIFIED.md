# Source verification

Checked 2026-11-09 by five Sonnet research subagents; the Kalshi Developer Agreement quotes were re-read by Fable from the PDF. Most pages were read through a summarizing fetch tool, so re-check any quote before relying on it.

Status key: **Confirmed** = matches what the plan assumed. **Changed** = differs from the plan. **Unknown** = could not be verified.

## The Odds API

| Item | Finding | Status |
| --- | --- | --- |
| Free tier | 500 credits a month | Confirmed |
| Cost per call | markets × regions. Spreads + `us` = 1 credit. Empty responses cost 0. `/sports` and `/events` are free. | Confirmed |
| One call returns all games | First real call, 2026-11-10: one call cost 1 credit and returned 56 games, each with a spread from at least one book. The feed lists games once books post lines, about a day ahead. Books seen: betonlineag, lowvig, betrivers, draftkings, fanduel, bovada, betmgm, betus. No sign of paging. | Confirmed by test |
| Paid tiers | $30/mo = 20,000 credits; $59 = 100,000 | Confirmed |
| US books | betonlineag, betmgm, betrivers, betus, bovada, williamhill_us, draftkings, fanatics, fanduel, lowvig, mybookieag. ESPN BET, Hard Rock and others are in a separate `us2` region. | Confirmed |
| Pinnacle | `eu` region only, so adding it doubles the cost per call | Confirmed |
| Historical odds | Paid plans only, 10× cost, NCAAB back to 2020-11-16 | Confirmed |
| Terms | Storing data and "calculating and displaying values you derive from our data" are allowed. "Do not resell, repackage, or redistribute our data as a standalone data product." | Confirmed |
| Timestamps | Use the per-market `last_update`. The bookmaker-level one is deprecated. `x-requests-remaining` header still present. | Changed |

Sources: https://the-odds-api.com/ · https://the-odds-api.com/liveapi/guides/v4/ · https://the-odds-api.com/sports-odds-data/bookmaker-apis.html · https://the-odds-api.com/historical-odds-data/ · https://the-odds-api.com/terms-and-conditions.html

## GitHub Actions

| Item | Finding | Status |
| --- | --- | --- |
| Free on public repos | "The use of standard GitHub-hosted runners is free: In public repositories." | Confirmed |
| Cron drift | 5-minute minimum interval. Delays documented at the start of every hour. User reports: 5–30 min common, occasional dropped runs. No official numbers. | Confirmed (size of drift unknown) |
| 60-day rule | Scheduled workflows in a public repo are disabled after 60 days of no repository activity. Whether a workflow's own commits count is not documented. | Unknown |
| Push to the private repo | Needs a PAT or GitHub App token stored as a secret. Fine-grained permission details not fetched. | Partly confirmed |
| Size limits | Repo under 1 GB ideal, under 5 GB strongly recommended. Files warn at 50 MiB, block at 100 MiB. Max 3,000 entries per directory. | Confirmed |

Sources: https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions · https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows · https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows · https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits · https://github.com/orgs/community/discussions/201738

## CollegeBasketballData (CBBD)

| Item | Finding | Status |
| --- | --- | --- |
| Free quota | 1,000 calls a month, shared with the football API. Paid: $1 = 5,000, $5 = 30,000, $10 = 75,000. | Confirmed |
| Python client | `cbbd` 1.30.3 (2026-11-03), bearer token | Confirmed |
| Lines fields | Per provider: `spread`, `over_under`, moneylines, `spread_open`, `over_under_open` | Confirmed |
| Open vs. close, sign, books | Discovery run 2026-11-10 (Jan 10, 2026, 142 games): every game had lines, from Draft Kings (142) and Bovada (128); 6 providers are listed in total. Fields: `spread`, `spreadOpen`, `overUnder`, `overUnderOpen`, moneylines. Sign is the home team's spread: games with a negative spread were won by the home team 177 of 270 times (66%), as expected for favorites. No timestamp on a line, so whether `spread` is the close is still untested. | Partly confirmed |
| Line coverage | 2013 to present; "A returned game can have an empty lines list." | Confirmed (completeness unknown) |
| Row caps | Games, lines, team box: 3,000 per call. Player box: 1,000 per call, and it returns one row per team per game. A month of games (1,552 in Jan 2026) fits in one call. Player box is fetched in 4-day blocks. | Changed |
| Backfill size | About 130–300 calls for 8 seasons; about 100 a month for daily updates | Estimate |
| Player box | Per player: `athleteId`, `name`, `minutes`, `starter`, `position`, box stats and ratings | Confirmed by test |
| Team ids | Numeric `id` plus `source_id` (not confirmed to be the ESPN id). No D-I flag. | Partly confirmed |
| Ratings | No date parameter, so not point-in-time. Games carry Elo at start and end. | Changed |
| Terms | No publishing API data "as a standalone dataset or bulk download". Attribution appreciated, not required. | Confirmed |
| Freshness | Not documented | Unknown |
| Usage header | No remaining-calls header or endpoint is documented. We count calls ourselves in the manifest. | Changed |

Sources: https://collegefootballdata.com/api-tiers · https://api.collegebasketballdata.com/usage-and-access.md · https://api.collegebasketballdata.com/data-availability.md · https://pypi.org/project/cbbd/ · https://github.com/CFBD/cbbd-python (docs/GameLineInfo.md, GameInfo.md, GamesApi.md, TeamInfo.md, RatingsApi.md) · https://collegefootballdata.com/terms

## Barttorvik

| Item | Finding | Status |
| --- | --- | --- |
| Bulk files | `https://barttorvik.com/YYYY_team_results.csv` (and `.json`), `YYYY_super_sked.csv` (55 columns, no header), `YYYY_results.csv`. Year = season's ending year. | Confirmed |
| Time Machine | `https://barttorvik.com/timemachine/team_results/YYYYMMDD_team_results.json.gz`. Data seen for 2015-01-15, 2018-12-01, 2026-01-01. 45 fields, no headers, in the same order as `team_results.csv` (exact match on all 45 for 2023-24 and 2025-26; see `docs/TORVIK.md`). | Confirmed |
| Time Machine timing | Bart Torvik (2026-11-10): the files are "frozen in the morning." Tested on all eight seasons (task 1.4, `docs/TORVIK.md`): the file dated D **includes** D's games, late-night West Coast and Hawaii games too, so "morning" means the morning of D+1. Known at 06:00 Eastern on D+1. One early count found in 90,423 team-games (an 11 a.m. game against a non-D-I team). | Confirmed (2026-11-11) |
| Game predictions | `super_sked` has a `prediction` text column. Bart Torvik (2026-11-10): the prediction is kept as of when the game went into his system, "so technically not at tip, but pretty close." How long before tip that is hasn't been measured; our daily `super_sked` snapshots can show it. | Owner says; lead time unmeasured (still not used, see DECISIONS.md) |
| Player stats | `getadvstats.php?year=YYYY&csv=1`, `YYYY_all_advgames.json.gz` (per game). No point-in-time snapshot, but per-game data can rebuild one. | Confirmed |
| cbbdata API | Domain did not resolve | Unknown (likely dead) |
| robots.txt | Disallows all `.json` files and several pages, `Crawl-delay: 10`, blocks named AI bots. | Changed |
| Owner's stance | For bulk, "please get in touch with me." Pulling a file "occasionally--even like once an hour--is certainly fine." Asked on X 2026-11-10 about the Time Machine backfill; he replied that doing it slowly "should be fine," with a warning that it might get blocked by the CDN. | Confirmed (reply on file with Owen) |
| Cloudflare | Tested 2026-11-10: GitHub Actions gets HTTP 403 with a browser check on both CSV files. The same request from Owen's home connection works (366 and 6,009 lines). | Changed: can't fetch from Actions |
| Team names | Free-text names, no numeric id | Confirmed |

Sources: https://adamcwisports.blogspot.com/p/data.html · https://barttorvik.com/robots.txt · https://github.com/andreweatherman/cbbdata · https://github.com/andreweatherman/toRvik

## Kalshi

| Item | Finding | Status |
| --- | --- | --- |
| Base URL | Docs name `https://external-api.kalshi.com/trade-api/v2`. `api.elections.kalshi.com` still works. Both answered without a key. | Changed |
| No key for market data | Series, events, markets, orderbook, trades, candlesticks all answered without a key | Confirmed |
| Unauthenticated rate limit | Not documented | Unknown |
| Series | `KXNCAAMBSPREAD` ("Men's College Basketball Spread"). Also `KXNCAAMBGAME`, `KXNCAAMBTOTAL`. | Confirmed |
| Tickers | Event: `KXNCAAMBSPREAD-26NOV09MRSHOSU`. Market: event + `-OSU48` (strike 47.5). | Confirmed |
| Several strikes per game | Yes, 8 to 20 markets per game across both teams, each "Team wins by over X points" with `floor_strike` | Confirmed |
| Home/away, tip time | No home/away field. `occurrence_datetime` looks like the scheduled start but is unverified. | Unknown |
| Price fields | Dollar strings (`yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`); integer cent fields are gone | Changed |
| Candlesticks | `GET /series/{series}/markets/{ticker}/candlesticks`, intervals 1, 60, 1440 minutes | Confirmed |
| Historical cutoff | Markets settled before the cutoff (2026-09-10 when checked) move to `/historical/*`, which answered without a key | Confirmed |
| History depth | Series looks to date from about Nov 2025. Only March 2026 onward was seen. | Unknown |
| Fees | Series shows `quadratic_with_maker_fees`, multiplier 1. Taker 0.07 × P × (1 − P) and maker 0.0175 × P × (1 − P) come from secondary sources; the official PDF was rate-limited. | Unverified |
| **Developer Agreement v1.1** | Applies to "any user" of the API; "Accessing or using Kalshi's API shall be considered a binding acceptance." §3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange; all other usages are disallowed." §3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API except for purposes of facilitating your own trading on Kalshi. You may not share such data or content with third parties in any manner without prior written authorization from Kalshi." §3.5 prohibits access "for any other benchmarking or competitive purposes." §4.1.1: "You must be a Member of Kalshi's Exchange." §6.4 requires written approval for public statements relating to Kalshi or its API. | **Changed: conflicts with the plan** |

Sources: https://docs.kalshi.com/getting_started/quick_start_market_data · https://docs.kalshi.com/getting_started/historical_data · https://docs.kalshi.com/getting_started/rate_limits · https://docs.kalshi.com/api-reference/market/get-market-candlesticks · https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf

## Polymarket

Written permission received by Owen on 2026-11-10 for reading the public API, storing prices privately and publishing derived numbers; Owen keeps the reply. Endpoints, paging and response shapes are in `docs/POLYMARKET.md`. Polymarket answers GitHub Actions' servers (tested 2026-11-10).

## Availability sources

| Item | Finding | Status |
| --- | --- | --- |
| ACC | Conference games only. Night before and 2 hours before tip. Available / Probable / Questionable / Doubtful / Out. | Confirmed |
| Big 12 | Conference games. Night before, final 90 minutes before tip. Available / Game-time decision / Out. Official URL not found. | Confirmed from press only |
| SEC | Conference games. Initial report 8pm ET the day before, plus a game-day update. Official URL not found. | Confirmed from press only |
| Big Ten | A report exists; no official basketball policy or URL found | Unknown |
| Others | Missouri Valley confirmed (questionable or out). Big East and A-10 appear in the vendor app only. | Partly confirmed |
| Host | All reports sit in one vendor's JavaScript app (HD Intelligence, `par.hdintelligence-app.com`). No public API or per-date URL found. No terms of use on hdintelligence.com or in the app (checked 2026-11-11); its privacy policy says report pages can be viewed without signing in. Permission email to HDI's men's basketball contact drafted 2026-11-11 for Owen to send. | Asked; waiting |
| NCAA.com | Tournament only; nothing for the regular season | Confirmed |
| ESPN | Its JSON has no college basketball injuries, and Disney's terms forbid automated access | Changed (ruled out) |
| RotoWire | Terms forbid scraping | Ruled out |
| Conference/team sites | robots.txt (checked 2026-11-11 on sienasaints.com and cyclones.com) blocks a list of named crawlers but allows other clients on the news feeds, with `Crawl-delay: 30` (not 5, as first noted). Men's basketball feed: `/rss?path=<sport code>` (often redirects to `/api/v2/rss/xml?path=...`), 10 recent items with title, link, summary, date. Note: the first manual test on 2026-11-11 sent 3 requests 6 seconds apart to each of those two sites, under the 30-second delay; the tools now enforce it. SIDEARM terms (2023-04-11): content is "for your personal, noncommercial use"; "Copying or storing of any Content for other than personal use is expressly prohibited." Owen reads this project as personal, non-commercial use (DECISIONS.md, 2026-11-11). | Secondary source, slowly (not built yet) |
| Google News RSS | **In use from 2026-11-11 by Owen's decision (DECISIONS.md).** Search feed `news.google.com/rss/search` returns RSS 2.0 (title, link, guid, pubDate, source; description is only a link). Tested 2026-11-11 from Owen's PC: 200, 22 items for one team. The feed's own copyright element: "made available solely for the purpose of rendering Google News results within a personal feed reader … Any other use of the feed is expressly prohibited." Google News terms also bar using a robot "to monitor or copy any content." | In use, Owen's decision |
| Bluesky | Developer guidelines allow apps on the AT Protocol (no spam; honor deletions; keep a public contact email). Unauthenticated search now returns HTTP 403, so a Bluesky account and app password (a secret Owen adds) would be needed. Coverage of college basketball injury news not tested. | Possible; needs Owen's decision |
| X API | Pay per read, no free tier (third-party sources) | Unknown |
| Injury APIs | DunkAPI, BALLDONTLIE, SportsDataIO: college coverage not verified | Unknown |

Sources: https://theacc.com/sports/2025/12/29/availability-reporting-mbb.aspx · https://www.ncaa.com/di-mens-basketball-player-availability · https://mvc-sports.com/news/2026/1/1/mvc-to-implement-public-player-availability-reports-for-mens-and-womens-basketball.aspx · https://disneytermsofuse.com/english/ · https://www.rotowire.com/termsandconditions.php · https://sidearmsports.com/terms-of-service · https://www.ncaa.com/tos

## What changes the plan

1. **Kalshi:** the Developer Agreement restricts API use to a member's own trading and forbids storing or sharing the data without written permission. Hold all Kalshi collection until Kalshi answers a permission request.
2. **Torvik:** the Time Machine files fall under a robots.txt disallow. Bart Torvik OK'd a slow backfill on 2026-11-10. Daily pulls of the current CSV files are within what he says is fine.
3. **Torvik predictions:** per Bart Torvik, kept as of when the game went into his system, close to but not at tip. The baseline margin is still computed from prior-day ratings; using his predictions is a separate decision for Owen after task 1.4.
4. **CBBD lines:** no timestamp, so "closing line" must be tested before it's used as the G1 yardstick.
5. **CBBD paging:** 3,000-row caps mean date-window paging with a cap check.
6. **Odds budget:** the free tier allows about 16 calls a day. Pinnacle would halve that.
7. **Torvik from Actions:** blocked by a browser check (tested 2026-11-10). The daily snapshot runs from Owen's machine, and the Actions workflow is switched off.
8. **Availability:** official reports cover conference games in a handful of leagues, through one vendor app with no public feed. ESPN and RotoWire are out.

## Task 1.2 results: are CBBD's lines true closes? (run 2026-11-19)

Run with `uv run python -m shac.tables.backtest_close`, exactly as written in `docs/BACKTEST_CLOSE.md`. Counts and shares only. Our snapshots cover 2026-27 only.

| Test | Draft Kings | Bovada | Pass bar |
| --- | --- | --- | --- |
| Tested games | 169 (ok, needs 150) | 157 (ok) | at least 150 |
| Movers | 124 (ok, needs 40) | 93 (ok) | at least 40 |
| T1 sign, same sign when abs(close) >= 2 (n) | 99.3% (151) **pass** | 99.3% (147, below 150: WAIT) | at least 99% |
| T2 near close / near open | 92.7% / 5.6% **CLOSE** | 60.2% / 28.0% **MIXED** | CLOSE: 80% / 10% |
| T3 within half a point of close | 89.3% **pass** | 76.4% **fail** | at least 80% |
| T4(a) 3 or more points from close | 1.8% **fail** | 2.5% **fail** | at most 1% |
| T4(b) changed after final (games) | 0.0% of 716 pass | 0.0% of 607 pass | at most 1% |
| T5 opens within 1 point of our open (n) | 95.2% (168) pass | 94.8% (155) pass | at least 70% |

| Link | Result | Pass bar |
| --- | --- | --- |
| L1 Draft Kings 2026 | mean d +0.094, t = 2.63, n = 2853 | t at least 2.0 |
| L1 Bovada 2026 | mean d +0.102, t = 2.80, n = 2428 | t at least 2.0 |
| L2 ESPN BET vs Draft Kings | n = 10 (cannot reach 40); 30.0% near spread, 70.0% near open | at least 40 games, 80% / 10% |
| L3 ESPN BET 2025 | mean d +0.098, t = 2.15, n = 1092 (needs L2, which cannot pass) | t at least 2.0 |
| L4 2023 ESPN BET vs teamrankings | 85.5% within half a point, n = 5648 (needs L3) | at least 85% |

**Reading off the rules (not a decision):** Draft Kings is CLOSE on T2 and passes T1 and T3, but fails T4(a) (1.8%, about 3 of 169 games, against a 1% bar). Per the tree, a T4 failure is branch D, and for Draft Kings branch D goes to branch C. L2 can't reach its sample, so branch A is out either way. Call counter for the four held-out seasons: 3,638 calls (36,380 credits) against the 1,900-call cap of one $30 month; 2026 + 2025 alone is 1,876 calls. Choosing the branch, the DECISIONS.md entry and the paid month are Owen's call.

## Task 1.2 results at 200 tested games: the one fixed read (run 2026-11-21)

Owen's decision of 2026-11-19 (issue #65): read once, when Draft Kings first reaches 200 tested games. Draft Kings has 209. Same command and tests as above, exactly as written in `docs/BACKTEST_CLOSE.md`. Counts and shares only. This is the only read; it is not repeated.

| Test | Draft Kings | Bovada | Pass bar |
| --- | --- | --- | --- |
| Tested games | 209 (ok) | 192 (ok) | at least 150 |
| Movers | 155 (ok) | 109 (ok) | at least 40 |
| T1 sign, same sign when abs(close) >= 2 (n) | 99.4% (181; 1 miss) **pass** | 100.0% (177) **pass** | at least 99% |
| T2 near close / near open | 91.0% / 7.7% **CLOSE** | 61.5% / 28.4% **MIXED** | CLOSE: 80% / 10% |
| T3 within half a point of close | 88.5% (185 of 209) **pass** | 78.1% (150 of 192) **fail** | at least 80% |
| T4(a) 3 or more points from close | 1.9% (4 of 209) **fail** | 1.6% (3 of 192) **fail** | at most 1% |
| T4(b) changed after final (games) | 0.0% of 805 pass | 0.0% of 688 pass | at most 1% |
| T5 opens within 1 point of our open (n) | 96.1% (207) pass | 95.3% (190) pass | at least 70% |

| Link | Result | Pass bar |
| --- | --- | --- |
| L1 Draft Kings 2026 | mean d +0.094, t = 2.63, n = 2853 | t at least 2.0 |
| L1 Bovada 2026 | mean d +0.102, t = 2.80, n = 2428 | t at least 2.0 |
| L2 ESPN BET vs Draft Kings | n = 10 (cannot reach 40); 30.0% near spread, 70.0% near open | at least 40 games, 80% / 10% |
| L3 ESPN BET 2025 | mean d +0.098, t = 2.15, n = 1092 (needs L2, which cannot pass) | t at least 2.0 |
| L4 2023 ESPN BET vs teamrankings | 85.5% within half a point, n = 5648 (needs L3) | at least 85% |

Draft Kings' 4 games 3 or more points from our close: 372790 (+1.5 vs our -1.5, a pick'em bouncing across zero), 375150 (+1.5 vs -1.5, the same pattern), 377998 (-44.5 vs -47.5, a ~45-point game), 375160 (CBBD +6.5 vs our -6.5; CBBD's own opening line was -5.5, so CBBD's number has the opposite sign here, and it is also the one T1 miss).

**Reading off the rules (not a decision):** Draft Kings passes T1 and T3, is CLOSE on T2, passes T4(b), but fails T4(a) (1.9%, 4 of 209; the bar allows 2). The doc says a T4 failure is branch D, and for Draft Kings branch D goes to **branch C**. Branch A is out either way (L2 cannot reach 40 games). Bovada fails T3 and T4(a) and is MIXED on T2. Call counter for the four held-out seasons (2023 to 2026): 3,638 calls (36,380 credits) against the 1,900-call cap of one $30 month; 2026 + 2025 alone is 1,876 calls. Choosing the branch, the DECISIONS.md entry and any paid month are Owen's call.

**Decided 2026-11-21 (DECISIONS.md):** branch C, no paid month. Bovada is dropped from the `cbbd` close (13 games in 2025-26 lose their close). The Draft Kings tests keep running only to refresh the scorecard's note on its lines (`shac.tables.backtest_close`, `lines_note`); the branch is not re-read.
