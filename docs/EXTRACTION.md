# The availability layer: extraction and evaluation spec (tasks 2.0 to 2.4)

Written 2026-11-12 by the lead. No extraction has been run and no flag exists. This page fixes what the model is shown, the JSON it must return, how its output becomes flags, and how the three G2 bars are measured. The DECISIONS.md entry of 2026-11-12 makes it part of the record. It replaces the `availability_flags` table in ARCHITECTURE.md when task 2.2 merges.

**For the builder:** every choice is made here. If something is not covered, stop and ask Owen. Before writing any API code, load the `claude-api` skill and follow it; the request details in section 4 were checked against it on 2026-11-12 and the API changes.

Companion pages: [LABELING_GUIDE.md](LABELING_GUIDE.md) (the answer key, and the rules the prompt copies) and [PLAYER_IMPACT.md](PLAYER_IMPACT.md) (task 2.5).

## What the collected news looks like (checked 2026-11-12)

1,975 different Google News items were collected in the first two days. A keyword count on the titles: about a quarter name another sport or league (football, NBA, women's teams), about a tenth are more than ten days old, and most of the rest are previews, recaps and betting pages. Items that report a men's basketball player's availability are a small share. Three things follow, and the design is built on them:

1. **The model is shown the teams' players.** A title such as "Russell's status revealed for Vanderbilt game" doesn't say which sport it is. A list of the men's basketball players settles it for the model and for Owen alike.
2. **Only players on a shown list can get a row.** The lists come from this season's box scores, so every flagged player is one the box scores can check.
3. **The labeled set can't be a random sample.** Most random items would be "nothing here", and reading accuracy would pass without testing much. The pick is stratified (LABELING_GUIDE.md, "The pick list").

## 1. Task 2.0: box scores every day (a prerequisite)

Today `games/players` is fetched only once a 4-day block is 7 days old (`docs/CBBD_INGEST.md`), so rosters and rotations run up to 11 days behind. G2 needs them the next morning.

- In `--mode current`, also fetch every 4-day `games/players` block that is **not yet settled and has started** (at most 3 blocks), each day, with the existing split-on-cap logic. Settled blocks are still fetched once and kept. About 3 more calls a day (about 90 a month; the monthly guard of 800 stays).
- `player_box` gains one column, `box_first_fetched_at`: the earliest `fetched_at` of any raw file that holds a row for that (`game_id`, `team_id`). `fetched_at` stays the latest copy's.
- **A team-game's box is "in hand at T"** when `box_first_fetched_at < T`. Every point-in-time rule below uses this.
- Exit check: on made-up raw files, a game present in two fetches gets the earlier time in `box_first_fetched_at` and the later rows in the table; `uv run python -m shac.tables.check` passes. Owner: data-engineer.

This also meets leakage rule 4 of `docs/CONTINUITY.md`.

## 2. The items table

`tables/news_items.parquet` in the private repo. Writer: `shac.tables.news`, part of `shac.tables.build`. One row per item, from the raw files under `news/raw/google_news/` and `news/raw/sidearm/`.

| Column | Type | How it is made |
| --- | --- | --- |
| `news_id` **PK** | text | First 16 hex characters of the SHA-256 of `source + "\n" + guid + "\n" + title` (UTF-8) |
| `source` | text | `google_news` or `sidearm` |
| `guid`, `url`, `title` | text | As fetched |
| `outlet` | text | Google News `source_name`; `team site` for `sidearm` |
| `published_at` | timestamp, may be empty | Per the source |
| `fetched_at` | timestamp | `fetched_at` of the **earliest** raw file that holds the item. This is the item's `as_of` |
| `query_team_ids` | list of int | Teams whose search (or whose site feed) returned the item in that earliest file, sorted |
| `item_text` | text | The only text the model and the labeler see (below) |

`item_text`:
- `google_news`: the title, with every run of whitespace replaced by one space, trimmed.
- `sidearm`: the title, a newline, then the summary with HTML tags removed (replace the pattern `<[^>]+>` with a space), `html.unescape` applied, whitespace collapsed, cut to its first 600 characters.

Article pages are never fetched (DECISIONS.md, 2026-11-11).

## 3. What one extraction request contains

One request per item. An item is read once, the first time an extraction run sees it.

**Skipped without a request, in this order, each counted:**

| Reason | Rule |
| --- | --- |
| `skipped_old` | `published_at` is not empty and `fetched_at − published_at` is more than 72 hours. Old stories are not news about the next game |
| `skipped_no_roster` | No context team has a player on its list (below). No row could result |

**Context teams** for an item: its `query_team_ids`, plus each of those teams' opponents in games with `fetched_at < tip_at <= fetched_at + 48 hours` and a status that is not cancelled or postponed, read through `games_as_of(fetched_at)` (LEDGER.md, section 4). Sorted by `team_id`.

**A team's player list at time T = `fetched_at`:** every `athlete_id` with a `player_box` row for that team, in the item's season, in a game with `tip_at < T` whose box is in hand at T. The name is the one in the player's latest such row. Order within a team: total minutes in those games, most first, then lower `athlete_id`.

**Numbering:** players are numbered 1, 2, 3, ... across all context teams in order (teams by `team_id`, players as above). The number is the `roster_no` the model and the labeler use. The map from `roster_no` to (`team_id`, `athlete_id`, `player_name`) is saved with the request.

**The user message, exactly** (no other text; `<...>` are filled in; a team with no players is listed with the line `  (no players listed yet)`):

```
ITEM
outlet: <outlet>
published: <published_at as YYYY-MM-DDTHH:MM:SSZ, or "unknown">
fetched: <fetched_at as YYYY-MM-DDTHH:MM:SSZ>
text: <item_text>

MEN'S BASKETBALL PLAYERS
<team name from data/teams.csv>
  <roster_no>. <player_name>
  <roster_no>. <player_name>
<next team name>
  <roster_no>. <player_name>
```

**The system prompt** is the file `src/shac/extraction/prompt_v1.md`. It is made of three parts, in order:

1. This opening, word for word:

   > You read one news item about college sports and report what it says about the availability of men's college basketball players. You are given the item and a numbered list of players. Report only what the item's text says. Do not use anything you know from elsewhere, and do not guess what happened after the item was written. Follow the rules below exactly.

2. The rules block of LABELING_GUIDE.md: everything between the lines `<!-- rules:start -->` and `<!-- rules:end -->`, copied byte for byte. A test fails if the prompt file and the guide differ there. So the model and Owen work from the same rules, and a rule change is one edit.

3. This closing, word for word:

   > Return one entry in `mentions` for each listed player whose availability the item reports. Use the player's number from the list as `roster_no`. `evidence` must be a short exact quote from the item's text that supports the status. If the item reports no listed player's availability, return an empty `mentions` list.

`prompt_version` is `v1`. Any change to the prompt file is a new file (`prompt_v2.md`) and a new version, tuned on the dev set only (section 7).

## 4. The request and the output schema

- SDK: the official `anthropic` Python package. Model: `claude-haiku-5-5` (task 2.4 decides any move to `claude-sonnet-5-5`).
- Structured output: pass the schema below through `output_config.format` (the current structured-outputs parameter; the older `output_format` is deprecated). `output_config.effort` is `"low"`. `max_tokens` is 2000.
- Do **not** send `temperature`, `top_p` or `top_k` (current models reject them) and do not send a `thinking` parameter.
- Mark the system prompt for prompt caching. Log `usage` from every response.
- Check `stop_reason` before reading content. `refusal` or `max_tokens`: the item's result is `invalid_output` (no retry for a refusal; one retry for `max_tokens`).
- At most 4 requests at a time. A 429 or 5xx is retried by the SDK; after that the item is left unread and picked up by the next run.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mentions"],
  "properties": {
    "mentions": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["roster_no", "status", "scope", "evidence"],
        "properties": {
          "roster_no": {"type": "integer"},
          "status": {"type": "string", "enum": ["out", "doubtful", "questionable", "probable", "available"]},
          "scope": {"type": "string", "enum": ["game", "extended", "past", "unclear"]},
          "evidence": {"type": "string"}
        }
      }
    }
  }
}
```

There is no `none` status in the output: an empty list means none. There is no confidence score (a model's own confidence is not a measured number) and no "is this new" field (section 6 decides that from box scores).

**Checks in code after the response, in this order.** Each dropped mention is counted under its reason.

1. The response parses as JSON and matches the schema. If not: one retry with the same request; then `invalid_output`, no mentions.
2. `roster_no` is a number on the item's list. If not: drop the mention (`bad_roster_no`).
3. `evidence`, with whitespace collapsed and case folded, is found inside `item_text` treated the same way, and is at least 3 characters. If not: drop the mention (`bad_evidence`).
4. Two or more mentions with the same `roster_no`: if they agree on `status`, keep the first. If they disagree, keep one mention with `status = questionable` and `scope = unclear` (`merged_conflict`).

## 5. What is stored

**Private repo**, one file per run: `extraction/raw/YYYY/MM/DD/extraction_<stamp>.json.gz`. For each item: `news_id`, the user message, the `roster_no` map, the raw response text, `stop_reason`, `usage`, the kept mentions, the dropped mentions with reasons. Never overwritten.

**Public repo**, `data/availability/season=YYYY/flags.csv`, one row per kept mention, appended by the job (a direct commit to `main`, like the status logs). Short facts only; `evidence` and all source text stay private.

| Column | Type | Notes |
| --- | --- | --- |
| `flag_id` **PK** | text | First 16 hex of the SHA-256 of `news_id + "\n" + athlete_id + "\n" + model + "\n" + prompt_version` |
| `news_id` | text | |
| `team_id`, `athlete_id` | int | From the `roster_no` map. Never empty |
| `player_name` | text | The list's name, not the item's spelling |
| `status` | text | `out`, `doubtful`, `questionable`, `probable`, `available` |
| `scope` | text | `game`, `extended`, `past`, `unclear` |
| `source_type` | text | `news`. (`official_report` is reserved: if HD Intelligence grants access, those reports are tables and are parsed by code, not by the model, under their own spec) |
| `as_of` | timestamp | The item's `fetched_at`. Never the publish time |
| `extracted_at` | timestamp | When the response came back |
| `model`, `prompt_version` | text | |

**Public status log**, `data/status/extraction_log.csv`, one row per run: `run_at_utc`, `status` (`ok`, `ok_no_items`, `push_failed`, `error`), `model`, `prompt_version`, `n_items_new`, `n_skipped_old`, `n_skipped_no_roster`, `n_requests`, `n_invalid_output`, `n_mentions_kept`, `n_bad_roster_no`, `n_bad_evidence`, `n_merged_conflict`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `est_cost_usd`. Cost uses two constants in the code (input and output price per million tokens) with the date they were read from the pricing page. On 2026-11-12 the skill's table listed Haiku 5.5 at $0.10 in and $0.50 out per million tokens; at roughly 1,000 new items a day that is cents a day, to be confirmed by the log.

**Schedule.** Extraction is the last step of each news workflow. For flags to exist before pricing (14:37 and 21:47 UTC, LEDGER.md), the two Google News runs move from 14:53 and 21:53 to **13:07 and 20:17 UTC** when task 2.2 merges, and the team-site run from 20:13 to **19:23 UTC**. Update the backup trigger to match.

## 6. Reading the flags: which game, and which status

One function, `status_for`, is used by pricing, by the evaluation and by task 2.5. For team `t`, a game `g` of that team, an athlete `a` and a time `T` with `T <= g.tip_at`:

1. `g` must be the team's first game tipping after `T` that is not cancelled or postponed. Otherwise there is no status.
2. `L` = the tip time of the team's latest game that tipped before `T` and was not cancelled or postponed (the start of the season if none).
3. Candidates: flags for `a` with `L < as_of < T`, `scope` in (`game`, `extended`, `unclear`), and `g.tip_at − as_of <= 7 days`.
4. No candidates: no status. Otherwise the status of the candidate with the latest `as_of`. If several share that `as_of` and disagree: `questionable`.

Why `L`: once the team has played again, the box score says whether the player played, and older news is spent. `past` flags never apply to a game. A flag that names a later game than the next one is applied to the next one anyway; the labeled set will show how often that happens (scope is reported, section 7).

- **Pricing** calls it with `T = priced_at`, schedule from `games_as_of(priced_at)`.
- **Evaluation** calls it with `T = g.tip_at`.

**New absence.** A player's absence in game `g` is *new* when `games_sat_in_a_row_before = 0` in `rotation_players` (he played the team's previous game). This is decided from box scores, never by the model.

## 7. The three bars (task 2.3)

One command prints all three with intervals and writes the files:

```
uv run python -m shac.extraction.evaluate --data-dir <private repo>
```

### Reading accuracy (bar: 95% or higher)

Measured on the 200 **test** items of the labeled set, as shown to the model exactly as in section 3 (the request text saved with the pick list is reused, so it can't drift).

- For item `i`: `L_i` = the set of (`athlete_id`, `status`) pairs in the label file (empty for a `none` item). `M_i` = the same set from the model's kept mentions. **The item is read correctly when `L_i = M_i`.** An `invalid_output` item has an empty `M_i`.
- `reading_accuracy = correct items / all test items`, with a 95% Wilson interval (z = 1.96). **The bar is met when the point value is at least 0.95.** With 200 items that is at most 10 wrong.
- `scope` is not part of the bar. It is reported.

Also printed, no bar: accuracy per stratum; accuracy on items whose label has at least one row; over all test items, `row_precision = |L ∩ M| / |M|` and `row_recall = |L ∩ M| / |L|` (sums over items); the same two counting only the player, ignoring status; the share of matched rows whose `scope` agrees; a production-weighted accuracy, `sum over strata of (stratum's share of the eligible pool × stratum accuracy)`; and the second-look agreement from LABELING_GUIDE.md.

**Dev and test.** The 40 dev items are for writing and fixing the prompt. The 200 test items are never used for that. Every run on the test set appends a row to `scorecard/availability/test_runs.csv` (`run_at_utc`, `model`, `prompt_version`, `n_items`, `n_correct`, `reading_accuracy`), so the number of looks is on record. A prompt may be changed after a test run only by working on dev items.

**Task 2.4, fixed now:** if Haiku's first test run misses the bar, up to two more prompt versions may be tried (each tuned on dev, each test run logged). If the third also misses, run `claude-sonnet-5-5` with the latest prompt. The side-by-side table goes in DECISIONS.md. If Sonnet misses too, stop and report to Owen; the layer does not go live.

### Precision (bar: 95% for rotation players)

- Pairs: every (athlete, game) in 2026-27 where `status_for(..., T = tip_at)` is `out`, the athlete has `is_rotation = true` for that game in `rotation_players`, and the team-game has a box score.
- `precision = pairs with played = false / all pairs`, with a 95% Wilson interval.
- **The bar is read only with at least 100 pairs.** Below that the output says `WAIT: n pairs`, and nothing is decided. Met when the point value is at least 0.95.
- Printed twice: all flags, and only flags with `extracted_at < tip_at` (read before the game for real, not replayed later). The bar uses all flags; if the two differ by more than 2 points, say so in the report.

### Recall (reported, no bar)

- Base: every rotation player-game in 2026-27 with `played = false` and a box score.
- `recall = share of those with status_for(..., T = tip_at) = out`. Also the share with `out` or `doubtful`.
- Three rows: all absences, new absences, and long-running ones (`games_sat_in_a_row_before > 0`).

### Status sit rates (feeds task 2.5)

`scorecard/availability/status_sit_rates.csv`: for rotation players with `games_sat_in_a_row_before = 0`, grouped by `status_for(..., T = tip_at)` (the five statuses and `none`): `n`, `n_sat`, `sit_rate`, Wilson interval.

## 8. Build order and exit checks

| Task | What | Exit check (all on made-up data unless noted) |
| --- | --- | --- |
| 2.0 | Section 1 | As stated there |
| 2.1 | `news_items`, the pick list, the labeling sheet and the label parser (LABELING_GUIDE.md) | The pick is the same on a second run; quotas, the month rotation, the per-team cap and the duplicate rule each have a test; the parser refuses a bad cell and names the row. Then the label file is committed with the guide |
| 2.2 | Sections 3 to 6: request builder, checks, storage, `status_for`, the workflow step | Tests: the user message for a made-up item matches a stored text file byte for byte; the prompt's rules block equals the guide's; each skip reason and each drop reason fires on its case and is counted; `status_for` on hand-built flags (latest wins, tie gives `questionable`, a flag before the previous game is ignored, `past` is ignored, the 7-day limit, a second game after T gets no status); a fake API client is used, so tests make no request. Then one real run on the dev set prints its cost |
| 2.3 | Section 7 | A hand-built set of 10 items with known answers gives the hand-worked accuracy, row precision and recall, and the Wilson interval; 99 pairs prints `WAIT`, 100 prints a value; the test-run log gains exactly one row per run |
| 2.4 | The rule above | The table in DECISIONS.md |

Each PR's check: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`. The `auditor` reviews 2.2 and 2.3. They decide what reaches pricing, so Owen signs off before merge.

**Stop and report to Owen, changing nothing, if** more than 5% of requests in a run are `invalid_output`, more than 5% of mentions are dropped for `bad_evidence`, or a run's estimated cost is over $1.
