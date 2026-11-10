# Team map

One `team_id` per D-I team (CBBD's numeric team id), and every name each source uses for it.

| File | What it is |
| --- | --- |
| `data/teams.csv` | One row per team: `team_id`, `name` (CBBD `school`), `conference` (latest), `first_season`, `last_season` (D-I seasons, inclusive) |
| `data/team_aliases.csv` | `source, alias, valid_from_season, valid_to_season, team_id`. Empty `valid_to_season` = still in use |
| `data/team_map_review.csv` | Names the build could not match with certainty, with a suggestion and reason, for Owen |
| `src/shac/teams/manual_aliases.csv` | Hand-checked name links. Edit this to resolve a review row |
| `src/shac/teams/manual_teams.csv` | D-I teams the saved CBBD `/teams` lists don't cover yet |
| `sql/checks/team_map.sql` | DuckDB checks; each returns zero rows when the map is good |

```
uv run python -m shac.teams.build --data-dir <private repo>
uv run python -m shac.teams.check --data-dir <private repo>
```

The public CSVs hold names, ids, conferences and seasons only. No scores, lines or ratings.

## Who counts as D-I

**Rule:** a team is D-I in a season if it is in CBBD's `/teams` list for that season.

Evidence (raw files fetched 2026-11-10):

- Team counts per season: 2019 353, 2020 353, 2021 356, 2022 358, 2023 363, 2024 362, 2025 364, 2026 365. That matches the known D-I size (about 365 in 2025-26).
- Year-to-year changes match real moves: Savannah State out (2020), Merrimack in (2020); Bellarmine, Tarleton State, Utah Tech, UC San Diego in (2021); Queens, Lindenwood, Southern Indiana, Stonehill, East Texas A&M in (2023); Hartford and St. Francis Brooklyn out, Le Moyne in (2024); Mercyhurst, West Georgia in (2025); New Haven in (2026).
- CBBD `games` has 2,521 distinct (season, opponent) pairs not on the list. Every one has an empty conference, as non-D-I opponents should. No game team on the list has an empty conference.
- Bethune-Cookman is missing from the 2021 list (it sat out the COVID season), so its seasons have a gap. `teams.csv` only has first and last season, so it shows 2019-2027.
- The 9 Ivy League teams are on the 2021 list but played no games that season.

**Newest season (2027).** No CBBD `/teams` list for 2027 is saved yet (and no CBBD calls were made for this task). For 2027, D-I = the teams in Torvik's 2027 `team_results` file, matched against the 2026 CBBD list plus `manual_teams.csv`. Result: 365 teams. St. Francis (PA) is not in Torvik 2027 (it left D-I after 2025-26), and West Florida is new (CBBD id 1073, already in CBBD games as a non-D-I opponent; Torvik lists it in the ASUN). When a CBBD 2027 `/teams` list is saved, the build uses it automatically and the West Florida row in `manual_teams.csv` can go.

## How names are matched

For each (source, name, season), in this order:

1. **Hand table** (`manual_aliases.csv`). Always wins.
2. **Exact key match.** Names are normalized (lower case, no accents, no periods or apostrophes, hyphens and brackets to spaces). Keys come from CBBD's own names for the teams that are D-I that season: `school` and `shortDisplayName`, plus spellings with "State" -> "St" and "Saint" -> "St". Odds API names carry the mascot, so its keys are school / short name / abbreviation / display name + mascot ("Kansas St Wildcats"). A key shared by two teams is never used.
3. **Nothing else is accepted.** A close spelling (Python `difflib`) is written to the review file as a suggestion only.

Season scope matters: a name only matches teams that are D-I that season, so a dropped school can't absorb a name after it leaves.

**Season ranges.** Each alias row covers the first to last season the name was seen for that team. A gap (Bethune-Cookman 2021, Ivy teams missing from Torvik 2021) is bridged unless another team used the name inside the gap. The row is open-ended (empty `valid_to_season`) when the team is D-I in the newest season and the name was seen in that source's newest data.

**CBBD names** in `games` match `school` exactly for every D-I team. CBBD applies today's names to past seasons (Utah Tech in 2019, not Dixie State), and so does Torvik (IU Indy in 2019), so no rename rows were needed so far. The range logic handles renames if a source starts using a new name.

**Torvik `super_sked`** also lists non-D-I opponents. Names that are not in that season's `team_results` are treated as non-D-I (381 today) and get no alias. If one of them exactly fits a D-I team, it goes to the review file instead.

**Torvik `team_results` header.** The saved 2027 copies start with a header row (`rank, team, conf, ...`); the reader skips a row whose name is `team`.

## The hardest cases

| Name | Resolved to | How |
| --- | --- | --- |
| Torvik `Miami FL` / `Miami OH` | Miami (168) / Miami (OH) (167) | hand row / exact ("Miami (OH)" normalizes to "miami oh") |
| Torvik `Saint Francis` | St. Francis (PA) (278) | hand row; fuzzy suggested San Francisco, which was wrong |
| Torvik `St. Francis NY` | St. Francis Brooklyn (364), 2019-2023 | hand row; fuzzy suggested St. Francis (PA), wrong |
| Torvik `USC Upstate` | South Carolina Upstate (268) | hand row; not USC (323) or South Carolina (266) |
| Torvik `Loyola MD` / `Loyola Chicago` / `Loyola Marymount` | 152 / 151 / 153 | exact (CBBD short name "Loyola MD") |
| Torvik `Tennessee Martin`, Odds `Tenn-Martin Skyhawks` | UT Martin (325) | hand rows |
| Torvik `Texas A&M Corpus Chris` | Texas A&M-Corpus Christi (294) | hand row (Torvik truncates) |
| Odds `Arkansas-Little Rock Trojans` | Little Rock (144) | hand row |
| Odds `Army Knights` | Army (15) | hand row; CBBD mascot is "Black Knights" |
| West Florida | new team 1073, 2027 | `manual_teams.csv` |

## Adding a new name

When the build puts a name in `team_map_review.csv`, check it, add a row to `src/shac/teams/manual_aliases.csv` (with a short note), and run the build and check again. New Odds API names will keep appearing as books post more games; most match on their own, ones with an unusual mascot or school spelling will land in review.
