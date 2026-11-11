# Torvik ratings files

What's in Bart Torvik's team ratings files and when each daily file is known (task 1.4). The decisions that follow from this are in [DECISIONS.md](DECISIONS.md) (2026-11-11). The table built from these files, `ratings_daily`, is described in [TABLES.md](TABLES.md).

## Rebuild and re-check

```
uv run python -m shac.tables.build             --data-dir <private repo>   # builds ratings_daily with the rest
uv run python -m shac.tables.check             --data-dir <private repo>   # includes the ratings_daily checks
uv run python -m shac.tables.timemachine_check --data-dir <private repo>   # the as-of test below
```

| Code | What it does |
| --- | --- |
| `src/shac/tables/torvik.py` | Column names, file readers, `ratings_daily` |
| `src/shac/tables/timemachine_check.py` | Loads every final CBBD game (any opponent) and runs the SQL below |
| `sql/checks/timemachine_asof.sql` | The window test and the leak check (DuckDB) |

## Columns

A Time Machine file (`YYYYMMDD_team_results.json.gz`) is a JSON list with one row per D-I team and no header. Each row has 45 positions in the same order as Torvik's `<season>_team_results.csv`, whose header names them. `ratings_daily` keeps the ones in **bold**; the raw files keep everything.

**How the order was checked.** For 2023-24 and 2025-26, the last Time Machine file of the season matches the season-end CSV on every one of the 45 columns for every team in both files. In the other six seasons, records, conference stats and projections still match, but the efficiency numbers don't. Torvik has re-run those season-end files since (his methods change between years), while the Time Machine files keep what was published at the time. That is what a point-in-time backtest needs. Separately, `barthag` equals `adj_o^11.5 / (adj_o^11.5 + adj_d^11.5)` to rounding error on all 424,729 rows, and `rank` always equals `barthag_rank`.

| Pos | Name in `torvik.py` | CSV header | Meaning |
| ---: | --- | --- | --- |
| 0 | **`rank`** | rank | Rank by barthag (same as position 9) |
| 1 | `team` | team | Torvik's team name (mapped to `team_id` through `team_aliases`) |
| 2 | `conf` | conf | Conference |
| 3 | `record` → **`wins`, `losses`** | record | "W-L" to date. Counts games against non-D-I teams from 2019-20 on, but not in 2018-19 |
| 4 | **`adj_o`** | adjoe | Adjusted offensive efficiency: points scored per 100 possessions against an average D-I defense |
| 5 | `adj_o_rank` | oe Rank | |
| 6 | **`adj_d`** | adjde | Adjusted defensive efficiency: points allowed per 100 possessions (lower is better) |
| 7 | `adj_d_rank` | de Rank | |
| 8 | **`barthag`** | barthag | Chance of beating an average D-I team on a neutral floor |
| 9 | `barthag_rank` | rank | |
| 10-11 | `proj_w`, `proj_l` | proj. W, Proj. L | Projected final record |
| 12-13 | `proj_conf_w`, `proj_conf_l` | Pro Con W, Pro Con L | Projected conference record |
| 14 | `conf_record` | Con Rec. | Conference record to date |
| 15-17 | `sos`, `nc_sos`, `conf_sos` | sos, ncsos, consos | Strength of schedule to date: all, non-conference, conference |
| 18-20 | `proj_sos`, `proj_nc_sos`, `proj_conf_sos` | Proj. SOS, ... | Same, full projected schedule |
| 21-22 | `elite_sos`, `elite_nc_sos` | elite SOS, elite noncon SOS | Schedule strength as seen by an elite team |
| 23-26 | `opp_oe`, `opp_de`, `opp_proj_oe`, `opp_proj_de` | Opp OE, Opp DE, ... | Average opponent efficiency, to date and projected |
| 27-28 | `conf_adj_oe`, `conf_adj_de` | Con Adj OE, Con Adj DE | Adjusted efficiency in conference games only |
| 29-32 | `qual_o`, `qual_d`, `qual_barthag`, `qual_games` | Qual O, Qual D, Qual Barthag, Qual Games | Efficiency in "quality" games, and how many |
| 33 | `fun` | FUN | Torvik's fun rating |
| 34-38 | `conf_pf`, `conf_pa`, `conf_poss`, `conf_oe`, `conf_de` | ConPF, ConPA, ConPoss, ConOE, ConDE | Raw conference-game totals and efficiencies |
| 39 | `conf_sos_remain` | ConSOSRemain | Remaining conference schedule strength |
| 40 | `conf_win_pct` | Conf Win% | |
| 41-42 | `wab`, `wab_rank` | WAB, WAB Rk | Wins above bubble |
| 43 | `fun_rank` | Fun Rk | |
| 44 | **`adj_tempo`** | adjt | Adjusted possessions per 40 minutes |

Positions 10 to 43 are named by the CSV header and match it in the two exact-match seasons; their meanings are Torvik's, and we haven't checked them further because nothing uses them yet.

## When is a file known?

**Short answer:** the file dated D already counts every game played on day D, including West Coast and Hawaii games that end after midnight Eastern. It is treated as known at **06:00 US Eastern on D+1**, and a game may use only a file whose `as_of` is before its tip. This replaces the old reading of rule 3 ("file dated D−1, by Eastern date"), which let late-night games see their own result.

**How it was tested.** Torvik's W-L record works as a clock: when a file's game count goes up, the new game is in it. For every team and every pair of consecutive files (413,000+ team-days over eight seasons), the count's growth was compared with the number of games CBBD says the team tipped in the window between the two files' `as_of` times. Games against non-D-I opponents count here, read straight from the raw CBBD files, because Torvik counts them too.

| Season | Team-days | Exact match, 06:00 cutoff | Exact match, midnight cutoff |
| --- | ---: | ---: | ---: |
| 2018-19 | 54,362 | 99.16% | 99.14% |
| 2019-20 | 46,243 | 99.91% | 99.86% |
| 2020-21 | 45,804 | 99.86% | 99.84% |
| 2021-22 | 52,626 | 99.86% | 99.82% |
| 2022-23 | 53,724 | 99.87% | 99.83% |
| 2023-24 | 56,110 | 99.93% | 99.88% |
| 2024-25 | 56,420 | 99.92% | 99.89% |
| 2025-26 | 56,573 | 99.93% | 99.89% |

2018-19 is lower because that season's records skip non-D-I games while our count includes them. The midnight cutoff is worse in every season, by about 15 extra mismatches a season, which are the late-night games.

**What the rule-3 test found, game by game.** Each team-game was joined to the latest file with `as_of` before its tip (DuckDB `ASOF JOIN`). A game is flagged if that file counts more games than the team had tipped before it.

- Old rule (Eastern date minus one, that is, a midnight cutoff): 174 flags in 90,423 team-games. 52 are games tipping between midnight and 3 a.m. Eastern, mostly in Hawaii, California and Nevada. Torvik counts those in the file for the local date, so they would have used ratings that already included their own result.
- New rule (06:00): 102 flags in 90,423 team-games. 101 come from six team-seasons where Torvik's count runs one game ahead of CBBD from mid-season on: CBBD is missing a game that Torvik counted (a record offset, not a leak). The remaining one is a real early count: Youngstown State's 11 a.m. Eastern home game against Penn St.-Shenango (non-D-I) on 2025-01-24 is already in the file dated 2025-01-23. We never predict non-D-I games.
- Moving the cutoff to noon would remove that one flag but give 3,518 team-games (3.9%) a file two days old. 2 p.m. would affect 15,007. Not worth it.
- **Pre-season files.** Each season's first file, dated the day before the first game, should show every team at 0-0. In 2025-26 it shows Winthrop and Queens at 1-0 and 0-1, from their 8 a.m. Eastern opener the next morning. The build drops any row in a season's first file with a game on its record (2 rows dropped).

**Fit with what Bart Torvik said.** He wrote on 2026-11-10 that the files are "frozen in the morning." The data agree if "morning" means the morning after the file's date: the file dated D is frozen early on D+1, after D's games and before almost all of D+1's.

## Our own daily pulls

`torvik_daily` rows come from our scheduled 5 a.m. Phoenix pull of the live season's `team_results.csv` (`torvik/raw/YYYY/MM/DD/`). Their `as_of` is the fetch time, and `rating_date` is the US Eastern date of the pull. If a date has more than one pull, the first is kept. Season-end files for past seasons in `torvik/raw/seasons/` are never used as point-in-time ratings.

## Our daily pulls match the Time Machine (checked 2026-11-11)

The 2026-27 Time Machine files from Oct 31 to Nov 10 were fetched on 2026-11-11 (11 files; `--seasons 2027` stops at the latest file known to be final, so it can be re-run as the season goes on). Each of our 5 a.m. Phoenix pulls on day D was compared with the Time Machine file dated D−1:

| Our pull | Time Machine file | Teams | Largest `adj_o` / `adj_d` gap | Largest tempo gap | Records that differ |
| --- | --- | ---: | --- | --- | ---: |
| 2026-11-10, 9:56 a.m. ET | 2026-11-09 | 365 | 4e-14 / 3e-14 | 0.0003 | 0 |
| 2026-11-11, 9:33 a.m. ET | 2026-11-10 | 365 | 2e-12 / 1e-12 | 0.0001 | 1 |

The ratings are the same numbers, so the live ratings are the same data the backtest uses. The one record difference is one more win in our pull, with ratings unchanged and no D-I game that day: a game against a non-D-I team that Torvik added after the file was frozen (those games don't move ratings).
