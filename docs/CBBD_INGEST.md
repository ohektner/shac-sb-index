# CBBD raw ingestion

Saves raw CollegeBasketballData responses into the private data repo (`cbbd/raw/`). No parsing into tables yet. Code: `src/shac/ingest/cbbd.py` (calls, manifest, budgets) and `src/shac/ingest/windows.py` (date windows, row caps). Workflow: `.github/workflows/cbbd-ingest.yml` (manual only).

## What the docs confirmed (2026-11-10)

Base URL `https://api.collegebasketballdata.com`. Auth: `Authorization: Bearer <key>`.

| Endpoint | Query parameters | Cap (from the docs) | Status |
| --- | --- | --- | --- |
| `GET /games` | `startDateRange`, `endDateRange`, `season`, `seasonType`, `team`, `conference`, `tournament`, `status` | 3,000 games | Confirmed |
| `GET /games/teams` | same as `/games` without `status` | 3,000 games | Confirmed |
| `GET /games/players` | same as `/games/teams` | 1,000 games | Confirmed |
| `GET /lines` | `startDateRange`, `endDateRange`, `season`, `team`, `conference` | 3,000 games, ordered by start date | Confirmed |
| `GET /lines/providers` | none | none stated | Confirmed |
| `GET /teams` | `season`, `conference` (both optional) | none stated | Confirmed |
| `GET /conferences` | none | none stated | Confirmed |

- Wire names are camelCase. The docs pages list snake_case Python names, so this was confirmed in the generated client source (`cbbd/api/games_api.py`, `lines_api.py`, `teams_api.py`), which sends `startDateRange`, `endDateRange`, `seasonType`.
- Date filters: `startDateRange` is "at or after", `endDateRange` is "at or before". The client sends datetimes like `%Y-%m-%dT%H:%M:%S.%f%z`. We send `2025-11-01T00:00:00Z`.
- Other endpoints seen but not used: `/games/media`, `/scoreboard` (paid tier), `/teams/directory`, `/teams/roster`, `/conferences/history`.

### Unconfirmed

- **Usage header or endpoint:** `usage-and-access.md` names none. It only says playground requests count toward usage and points to the API tiers page. So we count calls ourselves in the manifest. Not confirmed that the real monthly count matches ours (the quota is shared with the football API, so calls made elsewhere will not show).
- **Whether 200 responses are truncated at the cap** or return an error: not documented. We treat `n_rows >= cap` as "possibly truncated" and split.
- **Unit of the player cap:** the docs say 1,000 games. We count rows in the response. If a row is one player-game, the cap would be hit by about 40 games. Check with discover mode and edit `CAP_ROWS` in `windows.py`.
- **Response shapes** (field names, whether the top level is a list): not read yet. Discover mode prints them.
- Which status code the API uses when the quota is used up (we stop on 401, 403 and 429 anyway).
- The JSON spec URL was not found (`/openapi.json`, `/api-docs` and `/json` all returned 404).

## How it works

- **One call function**, `Ingestor.fetch(endpoint, params)`. If the manifest already has a row with the same endpoint and params that is `ok` or `capped`, it returns the cached result and makes no call. Error and retry rows are not cached, so failed calls are tried again on the next run.
- **Budgets.** A run stops cleanly (exit 0, message printed) before a call if it has made `--max-calls` calls, or if the manifest shows `--monthly-budget` calls (default 800) already this UTC month. Every attempt that got an HTTP response counts, including errors and retries. A network failure with no response is not counted.
- **Defaults.** `--max-calls` defaults to 8 in discover mode and 300 in backfill.
- **Retries.** Network errors and 5xx: up to 3 attempts, backoff 2 s then 4 s. 4xx is never retried. 401, 403 and 429 stop the whole run with exit 1. Other 4xx writes an error row and the run continues.
- **Files.** `cbbd/raw/<endpoint>/<params>_<UTC stamp>.json.gz` holding `fetched_at, source, endpoint, params, n_rows, data`. Never overwritten. The key is never saved. `games/teams` becomes `games-teams`.
- **Manifest** `cbbd/raw/manifest.csv`: `fetched_at_utc, endpoint, params_json, status, http_status, n_rows, file, sha256, bytes`. Status is `ok`, `capped`, `error`, or `retry` (a 5xx that was then retried; added so retries still count toward the budget).
- **Cap handling.** If `n_rows >= CAP_ROWS[endpoint]` the row is `capped`, the file is kept, and the window is split in two whole-day halves, recursively, down to one day. A day still capped is reported in the summary. Splitting is off in discover mode so it stays within 8 calls.
- **Windows.** Season S runs Nov 1 of S-1 to Apr 15 of S (end sent as Apr 16 00:00 UTC). Half-open, no gaps, boundaries may overlap. Games, lines and team box: calendar months. Player box: 10-day blocks.
- **Logs are public-safe.** Only endpoint names, dates, counts, statuses, call counts, and (discover) field names with JSON types. Errors print an HTTP code or exception class name only.
- Exit code is 1 if any `error` row was written this run or the API refused us.

## Modes

- `discover` (8 calls): `/conferences`, `/lines/providers`, `/teams?season=2026`, then for 2026-01-10 `/games`, `/lines`, `/games/teams`, `/games/players`, then `/games` for January 2026.
- `backfill`: per season, newest first: `/teams`, then games, lines, team box, player box across the windows.

Run locally: `CBBD_API_KEY=... uv run python -m shac.ingest.cbbd --data-dir <private repo checkout> --mode discover`.

## Call estimate for a full 8-season backfill

Per season: 1 teams + 6 months x 3 endpoints + 17 player blocks = 36 calls, so 288 for 8 seasons before any splitting. Splits add roughly 20 to 60 if only the busiest player blocks are capped. If the player cap turns out to count rows (players), most blocks would split down to days and the total could pass 1,000 per season. Run discover first.

## The season in progress (`--mode current`)

Runs daily on GitHub Actions (`cbbd-ingest.yml`, 10:07 UTC, 3:07 a.m. Arizona) and is the workflow's default when started by hand. The season comes from today's date (July onward counts as next season). Each run:

- `/teams` for the season, once.
- **Settled windows**, those that ended at least 7 days ago: the same monthly windows (`games`, `lines`, `games/teams`) and 4-day blocks (`games/players`) as backfill, fetched once and cached for good.
- **One live window** for `games` and `lines`: from the end of the last settled month to 9 days past today. Its end date changes daily, so it is a new call each day. The table build keeps the most recently fetched copy of each game, so new scores and lines replace old ones.

About 2 to 4 calls a day. The monthly budget guard (800) still applies.
