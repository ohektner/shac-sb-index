# Private tables

Built from the raw files in the private repo `shac-sb-index-data` and written back there as
parquet, under `tables/`. Nothing here is published: the files hold scores, lines and
player minutes from CBBD and The Odds API, whose terms forbid republishing them as a
dataset. Column rules (UTC, season = ending year, home margin, home spread sign, CBBD ids)
follow `docs/ARCHITECTURE.md`.

## Rebuild and check

```
uv run python -m shac.tables.build    --data-dir <private repo>   # rebuilds every table from raw
uv run python -m shac.tables.check    --data-dir <private repo>   # sql/checks/tables.sql, exit 1 on failure
uv run python -m shac.tables.findings --data-dir <private repo>   # sql/tables/findings.sql, for this page
```

The build reads only local files (no network), overwrites the parquet files each time, and
prints counts only, so its output is safe for public CI logs. The `findings` output is
aggregates (shares, counts) meant for a local run when this page is refreshed.

| Code | What it does |
| --- | --- |
| `src/shac/tables/raw.py` | Reads the wrapped `.json.gz` files; de-duplicates records that appear in several files |
| `src/shac/tables/cbbd.py` | `games`, `results`, `player_box`, `cbbd_lines` |
| `src/shac/tables/odds.py` | `odds_snapshots` (flattened) and `odds_event_matches` |
| `src/shac/tables/torvik.py` | `ratings_daily` (Torvik ratings; columns and timing in `docs/TORVIK.md`) |
| `sql/tables/game_history.sql`, `src/shac/tables/history.py` | `game_history` (DuckDB), and which columns are features, labels and benchmarks |
| `sql/tables/closing_lines.sql` | `closing_lines` (DuckDB) |
| `sql/checks/tables.sql` | Checks; each returns zero rows when the tables are good |
| `sql/tables/findings.sql` | The numbers in "Findings" below |

## Tables (`<private repo>/tables/*.parquet`)

All timestamps are `TIMESTAMP WITH TIME ZONE` (UTC). Keys are marked **PK**.

| Table | One row per | Notes |
| --- | --- | --- |
| `games` | game (**PK** `game_id`) | Per the ARCHITECTURE.md contract. D-I vs D-I only: both teams inside their `first_season`..`last_season` range in `data/teams.csv`. |
| `results` | final game with both scores (**PK** `game_id`) | `home_margin = home_points - away_points`. `overtimes` = number of periods minus 2 (two halves); empty if the period lists are missing or disagree. |
| `player_box` | player per team per game (**PK** `game_id, team_id, athlete_id`) | Contract columns plus `played` (bool): the did-not-play rule, false when `minutes` is 0 or missing. `minutes` is kept as CBBD gives it. Only games in `games`. |
| `cbbd_lines` | game per CBBD line provider (**PK** `game_id, provider`) | `home_spread` (CBBD `spread`), `home_spread_open`, `total`, `total_open`, `home_moneyline`, `away_moneyline`, `suspect_sign` (spread favorite and moneyline favorite are different teams), `fetched_at`. Every provider is kept, `consensus` included. A lines record whose home/away teams differ from `games` is skipped and counted (0 so far). |
| `odds_snapshots` | snapshot x event x book (**PK** `snapshot_at, odds_event_id, book`) | Per the contract. Spreads market only; every book in the file, US and `eu`. `home_spread` is the point on the outcome named like the feed's home team. |
| `odds_event_matches` | Odds API event (**PK** `odds_event_id`) | Team ids the names map to (via `team_aliases`, source `odds_api`), and `match_type`: `matched` (same home and away, tip dates within a day; sets `game_id`), `swapped` (only a game with home/away reversed exists; `game_id` stays empty, the game is in `candidate_game_id`), `no_game`, `unmapped_team`. |
| `closing_lines` | game per source (**PK** `game_id, source`) | Contract columns plus `basis`. See below. |
| `game_history` | game (**PK** `game_id`) | Every game in `games` with point-in-time ratings for both teams, the result and the closes. Column groups in ARCHITECTURE.md. Leakage tests: `tests/test_no_leakage.py`. |
| `ratings_daily` | rating date x team x source (**PK** `rating_date, team_id, source`) | Contract columns: `season`, `adj_o`, `adj_d`, `adj_tempo`, `barthag`, `rank`, `wins`, `losses`, `as_of`, `fetched_at`. `torvik_time_machine` rows: `as_of` = 06:00 US Eastern the day after `rating_date`. `torvik_daily` rows: our own pulls, `as_of` = fetch time, first pull per Eastern date. See `docs/TORVIK.md`. |

### Closing lines

| `source` | Close | Open | `basis` |
| --- | --- | --- | --- |
| `own_snapshots` | Median `home_spread` across US books in the last snapshot taken strictly before tip (`snapshot_at < commence_at`; a snapshot exactly at tip is excluded) | Same median in the first snapshot that had the game | `median_us_books` |
| `pinnacle` | Pinnacle's spread in the last pre-tip snapshot that has Pinnacle for the game (`n_books` = 1) | Pinnacle's first appearance | `pinnacle` |
| `cbbd` | Draft Kings' `spread` when present, else the median across the other CBBD providers with a spread, **excluding `consensus`** (see Findings); a game whose only line is `consensus` gets no `cbbd` row | Same provider's `spreadOpen` (or the median of the providers' `spreadOpen`) | `draftkings` or `median_cbbd_providers` |

- US books are the keys listed in `docs/VERIFIED.md` (`US_BOOKS` in `build.py`). Other `eu` books stay in `odds_snapshots` but never enter a close.
- The tip used is the feed's `commence_at` from the event's most recent snapshot.
- A game gets an `own_snapshots` or `pinnacle` row only once it is closed: some snapshot file was fetched at or after its tip, so no later pre-tip snapshot can still arrive. Only events matched to a CBBD game are included (the table is keyed by `game_id`).
- CBBD lines carry no timestamp, so `close_snapshot_at`, `open_snapshot_at` and `minutes_before_tip` are empty for `cbbd` rows. Whether CBBD's `spread` is a true close is still untested (VERIFIED.md).

## Row counts (build of 2026-11-10)

| Season | games | results | player_box | cbbd_lines |
| --- | ---: | ---: | ---: | ---: |
| 2019 | 5,611 | 5,597 | 110,517 | 13,073 |
| 2020 | 5,399 | 5,328 | 104,667 | 15,666 |
| 2021 | 4,852 | 3,944 | 78,897 | 12,511 |
| 2022 | 5,862 | 5,475 | 108,266 | 10,846 |
| 2023 | 5,749 | 5,715 | 112,804 | 17,054 |
| 2024 | 5,731 | 5,726 | 113,216 | 5,306 |
| 2025 | 5,769 | 5,764 | 114,186 | 5,440 |
| 2026 | 5,767 | 5,752 | 113,868 | 10,713 |
| **Total** | 44,740 | 43,301 | 856,421 | 90,609 |

`closing_lines`: 42,815 rows, all `cbbd` (4,503 `draftkings`, 38,312 `median_cbbd_providers`; 100 games whose only line was `consensus` have none). No `own_snapshots` or `pinnacle` rows yet: the two saved snapshots (2026-11-10 14:55Z and 15:40Z) are both before that night's tips, and no 2026-27 CBBD games are ingested to match them to.

`odds_snapshots`: 1,314 rows from 2 files, 56 events. `odds_event_matches`: 56 events, all `no_game` (match rate 0%, expected until 2027 CBBD games are ingested). Every team name mapped; no `swapped` or `unmapped_team` events.

`ratings_daily` (build of 2026-11-11): 425,457 rows. Time Machine 424,727 (353 to 365 teams a day; 132 to 156 days a season; 2 pre-season rows dropped; 0 unmatched names). Daily pulls 730 (2026-27, two days so far).

`game_history` (build of 2026-11-11): 44,740 games. Ratings file dated the day before (US Eastern date): 44,627. A file two days old: 110 (81 games with no set tip time, whose placeholder is midnight Eastern, and the midnight-to-6 a.m. tips). No file: 3 (cancelled 2020-21 games dated before the season's first file). A team missing from the file: 1 (the 2025-26 opener whose pre-season rows were dropped). With a result: 43,301. With a `cbbd` close: 42,815. Ratings known at or after tip: 0.

Games not final: 2021 has 636 cancelled and 272 scheduled (COVID); every other season has under 400 non-final games. No final game lacks a result.

## Findings

**Spread sign.** CBBD's `spread` is the home team's spread in every season and for every provider. Across 19 season x provider groups, when the spread is negative the home team won 73-82% of games; when positive, 32-37%. The implied home margin (`-spread`) correlates 0.58-0.69 with the actual home margin in every group, and a negative spread goes with the shorter home moneyline 94-100% of the time. This confirms the discovery run's single-date finding on all eight seasons.

*Spread vs. moneyline misses (1,411 lines).* About half are pick'em-range spreads (2 points or less), which is expected noise. The rest point to a few bad rows, left as CBBD gives them:
- `consensus` (2019-2023): 384 lines with a spread over 2 points whose sign disagrees with the moneylines. The spread's favorite won only about 29% of them, so the *spread* sign looks wrong on these rows.
- `teamrankings`: 260 such lines, where the spread's favorite won about 69%, so the *moneylines* look wrong there and the spread is fine.
- Bovada, Draft Kings, ESPN BET, numberfire: under 50 such lines in all.

All 1,411 are flagged in `cbbd_lines.suspect_sign` (consensus 543, teamrankings 392, ESPN BET 180, Bovada 176, Draft Kings 92, numberfire 28). The flag is false when the spread is 0, the moneylines are equal, or either is missing. Rows stay as CBBD gives them.

**Consensus is left out of the `cbbd` close.** It is an aggregate of the other books anyway, and it carries the sign-flipped rows above. It stays in `cbbd_lines`.

**Open vs. current.** `spreadOpen` is empty for every provider before 2025. In 2025 ESPN BET has it for 1,810 of 5,440 lines; in 2026 nearly all Bovada (4,927 of 4,930) and Draft Kings (4,422 of 4,503) lines have it. When both are present, open equals `spread` in 20% (Bovada 2026) to 40% (ESPN BET 2025) of lines, so `spread` usually moved after the open: consistent with it being a later (possibly closing) number, but not proof. Backtest CLV (open to close) is only possible for 2025-26 and part of 2024-25 from CBBD.

**Provider coverage** (share of final D-I games with a spread):

| Season | Any | Providers |
| --- | ---: | --- |
| 2019 | 98.9% | numberfire 96.5%, teamrankings 96.7%, consensus 36.3% |
| 2020 | 99.9% | consensus 99.1%, teamrankings 98.6%, numberfire 91.2% |
| 2021 | 99.6% | numberfire 97.2%, consensus 96.5%, teamrankings 96.3% |
| 2022 | 99.6% | teamrankings 98.4%, consensus 92.2% |
| 2023 | 100.0% | ESPN BET 99.9%, teamrankings 98.8%, consensus 97.5% |
| 2024 | 92.6% | ESPN BET only |
| 2025 | 94.3% | ESPN BET only |
| 2026 | 99.9% | Bovada 85.6%, Draft Kings 78.2%, ESPN BET 21.8% |

Draft Kings appears only in 2026, so the `cbbd` close is a Draft Kings line for 4,503 games (all 2026) and a median of the non-consensus providers elsewhere (1.0 to 2.0 providers on average). Before 2023 the providers are aggregators (consensus, numberfire, teamrankings), not books.

**Duplicates.** Overlapping fetch windows: 441 duplicate game records (of 49,159) and 441 duplicate lines records, and 5,936 duplicate team-game player records (of 103,366), all dropped keeping the most recently fetched copy. After that there were no duplicate athletes within a team-game, no duplicate providers within a game, and no duplicate odds rows.

**Other.** Overtimes are known for every result (0 to 4 per game; 218-360 overtime games a season). One 2025 result has a team with 0 points (left as CBBD gives it). `minutes` is never missing in `player_box`; 0 minutes (did not play) is 0.6-1.1% of rows. 13 final games have no player box rows (2019: 5, 2023: 1, 2025: 6, 2026: 1).
