# Polymarket price capture

Polymarket (the international platform) gave Owen written permission on 2026-11-10 to read its
public API for this non-commercial project, store prices privately, and publish derived numbers.
Raw responses go ONLY to the private repo `ohektner/shac-sb-index-data`. This public repo gets
status counts only (no team names, no prices). Terms and the permission are in `VERIFIED.md`.

Checked against the live API on 2026-11-10 (about 20 read-only GET requests, from a home
Arizona IP). No API key is needed for either API.

## The two jobs

| Job | Command | Schedule | Output (in the private repo) |
| --- | --- | --- | --- |
| Snapshot | `python -m shac.snapshots.polymarket --data-dir D --log L` | 6 runs a day, `41 2,14,17,19,21,23 * * *` UTC | `polymarket/raw/YYYY/MM/DD/events_<stamp>.json.gz` |
| History | `python -m shac.ingest.polymarket_history --data-dir D --mode recent\|backfill [--max-calls N]` | daily `29 10 * * *` (recent) | `polymarket/history/YYYY/MM/DD/<token_id>_<stamp>.json.gz` (YYYY/MM/DD = the game start date, UTC; keeps folders small), `polymarket/history/manifest.csv`, and in backfill `polymarket/raw/closed/*.json.gz` |

Public status log (this repo): `data/status/polymarket_snapshot_log.csv` with columns
`fetched_at_utc, status (ok|blocked|error), http_status, n_pages, n_events, n_spread_markets, file, sha256, bytes`.
The workflow adds a `push_failed` row if the file never reached the data repo.

Why the snapshot exists: spread markets are created only about 24 hours before tip-off, and a
game is listed about a week ahead with a moneyline only. A snapshot at least every 12 hours
therefore sees every spread market at least once while it is open. History is then pulled after
the game from the CLOB, which keeps the full price path.

## Gamma API (listings)

Base `https://gamma-api.polymarket.com`.

- Live games: `GET /events?series_id=10470&active=true&closed=false&limit=100&offset=N`.
  Series 10470 is college basketball. On 2026-11-10 this returned 368 events (4 pages: 100, 100,
  100, 68) holding 125 spread markets. A full page is about 1.7 MB uncompressed; one saved
  snapshot is about 300 KB gzipped.
- Closed games: `GET /events?series_id=10470&closed=true&limit=100&offset=N&order=startTime&ascending=true&start_time_min=<ISO>&start_time_max=<ISO>`.
- Paging: `limit` up to 100 works; we page until a page has fewer than 100 events. Plain `offset`
  paging has a ceiling: offset 1000 worked, offset 3000 answered HTTP 422
  `"offset too large, use /events/keyset for deeper pagination"`. We never get near it because
  the backfill lists one week at a time.
- Event fields (names only): `id, slug, title, ticker, startTime, startDate, eventDate, endDate,
  active, closed, archived, live, ended, score, period, elapsed, teams, tags, series, seriesSlug,
  sport, gameId, markets, volume, openInterest, createdAt, updatedAt, ...`.
- Market fields (names only): `id, question, slug, conditionId, sportsMarketType, line, spread,
  outcomes, outcomePrices, clobTokenIds, bestBid, bestAsk, lastTradePrice, volumeNum, volume24hr,
  liquidityNum, gameStartTime, startDate, createdAt, endDate, active, closed, feeSchedule,
  umaResolutionStatuses, ...`.
- `outcomes`, `outcomePrices` and `clobTokenIds` are JSON-encoded STRINGS, not arrays
  (for example `'["North Carolina State Wolfpack", "Presbyterian Blue Hose"]'`). The code parses
  them with `json_list`. `clobTokenIds[i]` goes with `outcomes[i]`.
- `gameStartTime` looks like `2026-11-11 00:00:00+00` (space, and `+00` with no minutes).
  Event `startTime` looks like `2026-11-11T00:00:00Z`. `parse_when` handles both.
- Spread markets are the ones with `sportsMarketType == "spreads"`. One game can have several
  spread markets at different lines. Non-D-I games are mixed in; filtering happens later.
- Closed events still carry their markets, including `clobTokenIds`, so history can be fetched
  for them.

### How far back closed events go

The earliest closed event in series 10470 is 2025-11-03 (the 2025-26 season opener window), so
the whole 2025-26 season is available. Whether anything older exists in this series was not
tested; an ascending listing with no date filter starts on 2025-11-03.

### Date filtering

The server filter works with `start_time_min` / `start_time_max` (ISO timestamps) and filters on
the event `startTime`. (`start_date_min/max` also exist but filter on a different field, the
event's creation-time `startDate`; they returned games on the wrong days, so they are not used.)
The backfill uses one-week windows of 100-event pages (about 4 pages for a busy week) and ALSO
re-checks each event's `startTime` on the client, so a stray event never costs history calls.

## CLOB API (price history)

Base `https://clob.polymarket.com`.

`GET /prices-history?market=<clobTokenId>&startTs=<unix>&endTs=<unix>&fidelity=<minutes>`
returns `{"history": [{"t": <unix>, "p": <price 0-1>}, ...]}`.

- For RESOLVED markets `interval=max` returns nothing, so explicit `startTs`/`endTs` are always
  used.
- We request `fidelity=5` (5-minute steps). A resolved Jan 2026 spread token over a window of about
  36 hours returned 347 points.
- Window per token: from the later of (market `createdAt`, tip-off minus 48 h) to tip-off plus 4 h.
  Both outcome tokens of every spread market are fetched, only after tip-off plus 6 h.
- Each saved file holds `fetched_at, source, params, market{event_slug, market_id, line, question,
  outcomes, token_id, game_start}, data`.

## Rate limits and politeness

- At most 2 requests a second (0.5 s pause between requests). The documented limit is far higher
  (Gamma `/events` 500 per 10 s).
- User-Agent `shac-sb-index/0.1 (+https://github.com/ohektner/shac-sb-index)`.
- Retries: network errors and 5xx up to 3 attempts (snapshot backoff 5 s then 20 s; history
  10 s then 20 s). 4xx is never retried. An HTML / Cloudflare page (any HTTP status) is status
  `blocked`; the snapshot exits 1 and the history job stops the whole run. HTTP 401, 403 and 429
  also stop the history run.
- The snapshot is all-or-nothing: if any page fails, nothing is saved and one `error` row is logged.
- History: `--max-calls` (default 1500) counts every request including retries. The manifest
  (`polymarket/history/manifest.csv`, same layout as the CBBD manifest) skips anything already
  saved, so re-runs and resumed backfills never repeat a call.

## Backfill size estimate (2025-26 season)

About 24 weekly windows (2025-11-01 to 2026-04-15), roughly 4 listing pages each, so about 100
listing calls. Closed events had about 290 spread markets in a mid-November week and about 650 in
a mid-January week; assuming about 500 a week on average gives about 11,500 spread markets, which
is about 23,000 history calls (two tokens each). At 1,500 calls per run that is about 16 runs;
each run takes roughly 15 to 20 minutes at 2 requests a second, inside the 45-minute limit. A
run with `--max-calls 4000` would take about 45 minutes, so keep it at 2,500 or less on Actions.
This is an estimate from two sample weeks.

History files are sharded by game start date (YYYY/MM/DD), so a day holds a few hundred files at most. The closed-listing pages are only about 100 files for the season, so they stay in one folder.

## Unconfirmed

- Whether Polymarket answers GitHub Actions servers. Only a home Arizona IP was tested. The job
  records `blocked` if it does not.
- `line` sign: in one closed-market sample `line` was `36.5` while the question read
  `(-36.5)`; in 36 of 36 markets from a different two-day sample it matched the question. The
  raw `question` and `outcomes` are saved so the sign can be rebuilt later. Parse the question's
  number for the team named in `outcomes[0]` when building tables.
- Paging "truncation": the brief mentioned a 100-row page truncating. Every page seen here was
  a full 100 until the last one. If a mid-list page ever comes back short, the snapshot would
  stop early; compare `n_events` between runs to notice.
- Whether event listings older than 2025-11-03 exist in this series.
- How Polymarket handles postponed games and markets (`gameStartTime` changes between snapshots;
  the newest snapshot's value is used).
- History is empty for some tokens (illiquid lines). Empty answers are saved with 0 points so the
  call is not repeated.
