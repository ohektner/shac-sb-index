# Architecture

Shac SB Index is a set of plain Python jobs that run on GitHub Actions and write files. There is no server and no hosted database. DuckDB reads the files directly when a query needs them.

Two repos:

- **`shac-sb-index` (public):** code, docs, our own team-name map, the predictions ledger, scorecard outputs and the site.
- **`shac-sb-index-data` (private):** raw copies of everything fetched from someone else (odds snapshots, CBBD pulls, Torvik files, news text) and the tables built straight from them. The sources' terms don't allow republishing their data as a dataset, so it stays here.

Jobs in the public repo check out the private repo with a token, read and write there, and publish only derived numbers.

## Data flow

```mermaid
flowchart LR
  subgraph Sources
    OA[The Odds API]
    CB[CBBD]
    TV[Barttorvik]
    NW[News and availability reports]
    KA[Kalshi: on hold]
  end

  subgraph Private[Private repo: shac-sb-index-data]
    OS[odds_snapshots]
    GM[games, results, player_box]
    RD[ratings_daily]
    NR[news_raw]
    KM[kalshi_markets, kalshi_prices]
  end

  subgraph Public[Public repo: shac-sb-index]
    TM[teams, team_aliases]
    M[Margin model]
    P[Pricing: cover_prob and tier]
    CL[closing_lines]
    AF[availability_flags]
    L[predictions_ledger, append-only]
    SC[Scorecard: DuckDB SQL]
    B[Board and scorecard on Cloudflare Pages]
  end

  OA --> OS
  CB --> GM
  TV --> RD
  NW --> NR
  KA -. if permitted .-> KM

  TM --> M
  GM --> M
  RD --> M
  M --> P
  OS --> P
  OS --> CL
  NR -. Phase 2 .-> AF
  AF -. Phase 2 .-> P
  KM -. if permitted .-> P
  P --> L
  L --> SC
  CL --> SC
  GM --> SC
  SC --> B
  L --> B
```

## Conventions

- **Time:** every timestamp is stored in UTC as ISO 8601 with a `Z`. Column names end in `_at`. The site converts to local time for display only.
- **Season:** an integer, the year the season ends. 2026-27 is `2027`. Torvik uses the same convention.
- **Margin:** always the home margin, home points minus away points. Positive means the home team won. Neutral-site games still have a listed home team; `neutral_site` marks them.
- **Lines:** stored as the home spread the way a book shows it (`home_spread = -6.5` means the home team is favored by 6.5). The market's implied home margin is `-home_spread`.
- **Edge:** `model_margin - market_implied_home_margin`. Positive means the model likes the home side.
- **Team IDs:** one canonical `team_id` per team, equal to CBBD's numeric team `id`. Every other source's name maps to it through `team_aliases`.
- **Game IDs:** `game_id` is CBBD's game `id`.

## The no-leakage contract

1. Every feature row carries `as_of`, the moment the information was available.
2. A feature may be used for a game only if `as_of < tip_at`.
3. Ratings used for a game on date D come from the file dated D-1, until a test shows the file dated D excludes games played on D.
4. In backtests, a season's games are predicted by a model trained only on earlier seasons. Calibration is fit only on those earlier seasons too.
5. Nothing computed from a game's own result, or from any later game, can reach that game's features. This includes season-end ratings, final rosters and closing lines.

Tests enforce this. `tests/test_no_leakage.py` (G1) fails if any feature row has `as_of >= tip_at`, and if the feature builder's output for a game changes when later games are removed from its input.

## Data contracts

"Writer" is the only job allowed to write the table. Tables are parquet unless noted. Keys are marked **PK**.

### Public repo

**`teams`** (`data/teams.csv`). Writer: `build_teams`, then hand-reviewed.

| Column | Type | Notes |
| --- | --- | --- |
| `team_id` **PK** | int | CBBD team id |
| `name` | text | Our display name |
| `conference` | text | Current conference |
| `first_season`, `last_season` | int | D-I seasons covered, inclusive |

**`team_aliases`** (`data/team_aliases.csv`). Writer: hand-maintained, checked by a test.

| Column | Type | Notes |
| --- | --- | --- |
| `source` **PK** | text | `cbbd`, `torvik`, `odds_api`, `kalshi`, `news` |
| `alias` **PK** | text | The exact string the source uses |
| `valid_from_season` **PK** | int | First season the alias applies |
| `valid_to_season` | int, nullable | Null = still in use |
| `team_id` | int | Must exist in `teams` |

Test: every team name in every source for every season resolves to exactly one `team_id`, and no two teams share an alias in the same season.

**`closing_lines`** (`data/closing_lines.parquet`). Writer: `build_closing_lines`, nightly.

| Column | Type | Notes |
| --- | --- | --- |
| `game_id` **PK** | int | |
| `source` **PK** | text | `own_snapshots` or `cbbd` |
| `close_home_spread` | float | Median across books in the last snapshot before tip |
| `close_snapshot_at` | timestamp | Fetch time of that snapshot |
| `minutes_before_tip` | int | How stale our "close" is |
| `n_books` | int | Books in the median |
| `open_home_spread` | float, nullable | Median in the first snapshot that had the game |
| `open_snapshot_at` | timestamp, nullable | |

Only a consensus number is published, never a per-book table. For backtest seasons the source is `cbbd`, and its meaning (true close or last captured) is recorded in `docs/VERIFIED.md` once tested.

**`predictions_ledger`** (`ledger/season=YYYY/ledger.csv`). Writer: the pricing job. Append-only.

| Column | Type | Notes |
| --- | --- | --- |
| `game_id` **PK** | int | |
| `run` **PK** | text | `morning` or `late` |
| `priced_at` **PK** | timestamp | When the row was computed; must be before `tip_at` |
| `tip_at` | timestamp | Scheduled tip when priced |
| `home_team_id`, `away_team_id` | int | |
| `model_version` | text | Git tag or short commit of the model |
| `model_margin` | float | Predicted home margin |
| `line` | float | Home spread used for pricing |
| `book` | text | `consensus` or a book key |
| `line_snapshot_at` | timestamp | Fetch time of the snapshot the line came from |
| `kalshi_implied_margin` | float, nullable | Null while Kalshi is on hold |
| `cover_prob` | float | Calibrated probability that the model's side covers |
| `side` | text | `home` or `away` |
| `tier` | text | `pick`, `lean` or `none` |
| `flags` | text | Semicolon-separated: `questionable_player`, `stale_line`, `no_ratings`, `kalshi_unexplained_move`, ... |

Integrity rules:

- Rows are only ever added. `tests/test_ledger_append_only.py` compares the ledger with the version on `main` and fails if any existing row is changed, reordered or missing. It runs in CI on every PR and at the start of every pricing job.
- A row is written only if `priced_at < tip_at`. The job refuses to price a game that has started.
- The commit that adds a row is the public timestamp. Commits go straight to `main`.
- If the morning and late runs disagree, the last row with `priced_at < tip_at` is the one scored. Both stay on record.
- Results are never written into the ledger. The scorecard joins results at query time.

**`availability_flags`** (Phase 2; `data/availability_flags.parquet`). Writer: the extraction job.

| Column | Type | Notes |
| --- | --- | --- |
| `flag_id` **PK** | text | Hash of `news_id` + `athlete_id` |
| `news_id` | text | The `news_raw` item it came from |
| `game_id` | int, nullable | Game it applies to, if known |
| `team_id` | int | |
| `athlete_id` | int, nullable | CBBD athlete id, null if not matched |
| `player_name` | text | As written in the source |
| `status` | text | `out`, `doubtful`, `questionable`, `probable`, `available` |
| `is_change` | bool | True if this is new information, not a long-running absence |
| `source_type` | text | `official_report` or `news` |
| `as_of` | timestamp | The item's `fetched_at` |
| `extracted_at` | timestamp | |
| `model` | text | LLM model id |
| `prompt_version` | text | |
| `confidence` | float | Model-reported, 0 to 1 |

Player names and statuses are short facts, not source text, so this table is public. The text it was read from stays private.

### Private repo

**`odds_snapshots`** (`odds/raw/YYYY/MM/DD/odds_<stamp>.json.gz`). Writer: `shac.snapshots.odds`, 12 times a day. One file per fetch: the API response unchanged, wrapped with `fetched_at`, the request parameters and the credit counts. Files are never overwritten. A flattened view is built on read:

| Column | Type | Notes |
| --- | --- | --- |
| `snapshot_at` **PK** | timestamp | `fetched_at` of the file |
| `odds_event_id` **PK** | text | The Odds API event id |
| `book` **PK** | text | Bookmaker key |
| `commence_at` | timestamp | Tip time per the feed |
| `home_team`, `away_team` | text | Source names; mapped through `team_aliases` |
| `home_spread` | float | |
| `home_price`, `away_price` | int | American odds |
| `market_last_update_at` | timestamp | When the book's spread last changed, per the feed |
| `game_id` | int, nullable | Filled after matching to `games` |

Each run also appends one row to `data/status/odds_snapshot_log.csv` in the public repo: fetch time, game count, book count, credits left and the file's SHA-256. That log is the public proof the snapshot existed at that time, without exposing the data.

**`games`**. Writer: `ingest_cbbd`.

| Column | Type | Notes |
| --- | --- | --- |
| `game_id` **PK** | int | |
| `season` | int | |
| `season_type` | text | Regular, postseason, ... |
| `tip_at` | timestamp | |
| `tip_tbd` | bool | |
| `home_team_id`, `away_team_id` | int | |
| `neutral_site`, `conference_game` | bool | |
| `status` | text | Scheduled, final, cancelled, ... |
| `fetched_at` | timestamp | |

**`results`**. Writer: `ingest_cbbd`.

| Column | Type | Notes |
| --- | --- | --- |
| `game_id` **PK** | int | |
| `home_points`, `away_points` | int | |
| `home_margin` | int | `home_points - away_points` |
| `overtimes` | int | |
| `fetched_at` | timestamp | |

**`player_box`**. Writer: `ingest_cbbd`. Ground truth for who played.

| Column | Type | Notes |
| --- | --- | --- |
| `game_id` **PK** | int | |
| `team_id` **PK** | int | |
| `athlete_id` **PK** | int | |
| `player_name` | text | |
| `minutes` | float | 0 or missing = did not play |
| `starter` | bool | |
| `fetched_at` | timestamp | |

**`ratings_daily`**. Writer: `ingest_torvik`. Point-in-time.

| Column | Type | Notes |
| --- | --- | --- |
| `rating_date` **PK** | date | The date in the file name |
| `team_id` **PK** | int | |
| `source` **PK** | text | `torvik_time_machine` (backfill) or `torvik_daily` (our own daily pull) |
| `adj_o`, `adj_d`, `adj_tempo` | float | Adjusted offense, defense, tempo |
| `barthag` | float | Torvik's power rating |
| `rank` | int | |
| `as_of` | timestamp | When these ratings were known. For our daily pulls, the fetch time. For backfill, end of `rating_date` until tested (see no-leakage rule 3). |
| `fetched_at` | timestamp | |

**`news_raw`** (`news/raw/YYYY/MM/DD/*.json.gz`). Writer: `collect_news`.

| Column | Type | Notes |
| --- | --- | --- |
| `news_id` **PK** | text | Hash of source + URL + content |
| `source` | text | Feed or report name |
| `url` | text | |
| `title`, `text` | text | As fetched |
| `published_at` | timestamp, nullable | Per the source |
| `fetched_at` | timestamp | Ours; this is the `as_of` |

**`kalshi_markets`, `kalshi_prices`** (on hold; nothing is collected until Kalshi gives written permission).

| Table | Columns |
| --- | --- |
| `kalshi_markets` | `market_ticker` **PK**, `event_ticker`, `game_id`, `team_id` (the team "yes" refers to), `is_home`, `strike` (yes = team wins by more than this), `open_at`, `close_at`, `fetched_at` |
| `kalshi_prices` | `market_ticker` **PK**, `price_at` **PK**, `yes_bid`, `yes_ask`, `last_price`, `volume`, `open_interest`, `fetched_at` |

## Pricing

1. **Margin:** the model predicts `model_margin` (home) from point-in-time ratings and game context.
2. **Edge:** `model_margin - (-line)`.
3. **Side:** home if the edge is positive, away if negative.
4. **Cover probability:** a calibration curve maps the size of the edge to how often that side actually covered. It is fit on training seasons only (see PREREGISTRATION.md). The raw normal formula, Φ(edge / σ), overstates confidence and is not used for tiers.
5. **Tier:** `pick`, `lean` or `none` from the cutoffs in PREREGISTRATION.md, then the caps (questionable rotation player caps at Lean; the Kalshi check applies to Picks when Kalshi data is allowed).

## Kalshi conversion (designed, switched off)

A Kalshi spread market is a yes/no contract: "Team T wins by more than k." Each game lists many strikes k for both teams.

Treat the margin as normal with mean μ and spread σ. For a market on team T at strike k with yes-probability p:

- If T is the home team: `p = 1 - Φ((k - μ) / σ)`, so `μ = k + σ·Φ⁻¹(p)`.
- If T is the away team, "away wins by more than k" means the home margin is below `-k`: `p = Φ((-k - μ) / σ)`, so `μ = -k - σ·Φ⁻¹(p)`.

μ is the Kalshi-implied home margin.

- **Probability used:** the mid price, `(yes_bid + yes_ask) / 2`, from markets with quotes on both sides and a spread no wider than 10¢.
- **Several strikes:** when a game has three or more usable strikes with p between 0.10 and 0.90, fit μ and σ together with a straight-line fit of `Φ⁻¹(p)` against k. That also gives the market's own σ for the game. With fewer, use the single strike nearest p = 0.50 and the pre-registered σ.
- **Fee-adjusted price for the Pick check:** the cost to take a side is `ask + fee`, with `fee = 0.07 × ask × (1 − ask)` per contract. A Pick must have `cover_prob` above that cost for the matching side and strike. The 0.07 constant is unverified against Kalshi's official fee schedule; confirm it before this check goes live.
- **Home/away:** Kalshi has no home/away field. `is_home` comes from matching the market's team to our `games` table through `team_aliases`.

## Secrets

All in GitHub Actions secrets on `shac-sb-index`. None are ever committed. Local runs read a gitignored `.env`.

| Secret | Used by | Notes |
| --- | --- | --- |
| `ODDS_API_KEY` | Odds snapshot job | The Odds API, free tier |
| `CBBD_API_KEY` | CBBD ingestion | Free tier, 1,000 calls a month shared with the football API |
| `DATA_REPO_TOKEN` | Every job that reads or writes raw data | Fine-grained token: `shac-sb-index-data` only, Contents read and write |
| `ANTHROPIC_API_KEY` | Phase 2 extraction | Not needed until G2 |

Workflows never print secrets or raw data, and never run with shell tracing on. Public repo logs are public.

## Jobs

| Job | Schedule (UTC) | Reads | Writes |
| --- | --- | --- | --- |
| Odds snapshot | 12 a day at :17 (13, 16, 18, 20-23, 0-4) | The Odds API | `odds_snapshots`, public status log |
| Torvik daily snapshot | 11:43 | Barttorvik CSV files | Raw Torvik files, public status log |
| CBBD ingestion (G1) | Nightly | CBBD | `games`, `results`, `player_box` |
| Pricing, morning and late (G1) | Morning, and about 2 hours before the day's tips | Ratings, latest snapshot | `predictions_ledger` |
| Results and scorecard (G1) | Nightly | Ledger, results, closing lines | `closing_lines`, scorecard outputs, site data |
| News collection (G1) | A few times a day | Permitted feeds | `news_raw` |

GitHub cron can run 5 to 30 minutes late and sometimes skips a run, so no job depends on exact timing. Every file records its own fetch time.
