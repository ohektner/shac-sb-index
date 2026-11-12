# Availability labeling guide (task 2.1)

How Owen hand-labels 240 news items so the AI layer's reading accuracy can be measured (G2 bar: 95% or higher on 200 test items). Rewritten 2026-11-12 by the lead after looking at the first two days of collected items; what changed and why is at the bottom. The pipeline around it is in [EXTRACTION.md](EXTRACTION.md).

The labels are the answer key, so they follow what the item **says**, not what happened later. Don't look up the game, the box score or the full article.

## What Owen gets and what he fills in

`labels/labeling_sheet.csv` in the private repo (it holds source text), one line per item:

| Column | What it is |
| --- | --- |
| `set`, `pick_no`, `news_id` | Which item |
| `outlet`, `published_at`, `fetched_at`, `item_text` | The item, exactly as the model sees it |
| `players` | The numbered men's basketball players of the teams involved, exactly as the model sees them |
| `labels` | **Owen fills this in** |
| `note` | Optional: why, for a hard call |

The `labels` cell is either the word `none`, or one entry per player, separated by `;`. An entry is the player's number, a status and a scope, separated by spaces:

```
none
12 out game
12 out extended; 15 questionable unclear
```

A script (`shac.extraction.labels`) checks every cell and writes the public file `data/labels/availability_labels.csv` (`set`, `news_id`, `team_id`, `athlete_id`, `player_name`, `status`, `scope`, `labeler_note`; a `none` item is one row with no player and `status = none`). No source text goes in the public file.

<!-- rules:start -->
## Rules

### Who gets an entry

1. Only a player on the numbered list can get an entry. Match by name: the item's name must be the listed player (a last name alone is enough when only one listed player on the teams in the item has it and the item is about men's basketball).
2. A player who is not on the list gets nothing, whatever the item says about him. This covers football players, women's players, NBA players, coaches, recruits and players who have not appeared in a game this season.
3. If the item is plainly about another sport or league, there are no entries, even if a last name matches a listed player.
4. A listed player gets an entry only if the item says something about whether he can play: an injury, an illness, a suspension, a status word, an absence, a return, leaving the team. A player who is only named (stats, a quote, a preview, an award) gets nothing.
5. If nobody gets an entry, the answer is none.
6. One entry per player per item. If the item says two things about the same player, use the later or more specific one; if you can't tell which, use `questionable`.

### Status

Use the status the words support. When two readings are possible, take the less certain one (`questionable` over `out`, unless the item says he is out).

| The item says (examples) | Status |
| --- | --- |
| "out", "ruled out", "will not play", "won't play", "will miss", "sidelined", "did not travel", "suspended", "out for the season", "season-ending", "out indefinitely", "out 4-6 weeks", "will redshirt", "dismissed", "left the team", "entered the transfer portal" | `out` |
| "doubtful", "unlikely to play", "expected to miss", "likely out", "not expected to play" | `doubtful` |
| "questionable", "game-time decision", "day-to-day", "status uncertain", "could miss", "may miss", "in question", "being evaluated", "awaiting test results", "limited in practice" | `questionable` |
| "probable", "expected to play", "likely to play", "should be available", "set to return", "expected back" (for a game) | `probable` |
| "available", "cleared", "will play", "returns", "back in the lineup", "off the injury report", "made his season debut" | `available` |
| An injury, illness or off-court trouble is reported with no word on playing ("dealing with an ankle sprain", "left the game with a hand injury", "injury update", "arrested") | `questionable` |
| A headline that only asks ("Will he play?") | `questionable` |
| A coach hedging ("we'll see how he feels") | `questionable` |
| Recovery going well, no game named ("progressing", "nearing a return") | `questionable` |

Official conference reports use their own words (Available, Probable, Questionable, Doubtful, Out; "Game-time decision" is `questionable`).

### Scope

| The item is about | Scope |
| --- | --- |
| One coming game, named or implied ("tonight", "Saturday", "vs. Duke", "game-time decision") | `game` |
| A stretch of time or more than one game ("4-6 weeks", "indefinitely", "the season", "three games", left the team) | `extended` |
| Only a game already played ("missed last night's game", "did not dress Tuesday", "returned in the win") | `past` |
| No time frame | `unclear` |

If an item covers a played game and also says something about a coming one ("left Tuesday's game, out Saturday"), label the coming one.

### Hard cases

- **Rumor or unnamed source** ("sources say he may miss time"): label what is claimed. Reading is judged, not whether the source was right.
- **Unnamed player** ("a starter is out", "transfer forward gets injury update"): no entry.
- **A report that names nobody** ("First availability report for UCLA-Illinois"): none.
- **Someone else's injury** ("Irving could see a bigger role after the Phillips injury"): an entry for Phillips (`questionable`, since nothing says whether he plays), nothing for Irving.
- **A past injury as background** ("overcame two knee surgeries, returns to the lineup"): `available`.
- **Played despite an injury** ("scored 20 on a sore ankle"): `available`, scope `past`.
- **A past absence only** ("missed last night's game with an illness"): `out`, scope `past`.
- **Future seasons** (a commitment, a signing, the draft): no entry.
- **Betting pages and game previews** that mention "injury report" without naming a listed player's status: none.
- **Two items that disagree:** each item is labeled on its own.
<!-- rules:end -->

The block between the two markers is copied word for word into the model's prompt, and a test keeps them equal. Edit the rules here and nowhere else.

## The pick list

Built by `shac.extraction.pick` and committed as `data/labels/pick_list.csv` (`set`, `pick_no`, `news_id`, `stratum`, `month`) **before any labeling and before the model reads a test item.** It is never changed afterwards. The request text for each picked item (EXTRACTION.md, section 3) is saved next to the labeling sheet in the private repo at the same time, so the model later reads exactly what Owen read.

**Pool:** items in `news_items` fetched from 2026-11-11 up to the day before the pick, that would be sent to the model (not `skipped_old`, not `skipped_no_roster`). The pool must span at least 30 days; if not, wait.

**Strata.** Two yes/no tests on `item_text`, case ignored:

- `AVAIL`: it matches `\b(out|injur\w*|questionable|doubtful|probable|ruled|miss\w*|suspen\w*|return\w*|sidelined|surgery|availability|game-time|day-to-day|cleared|redshirt\w*|dress\w*|status|absence|illness|concussion|sprain\w*|torn|fracture\w*|leaves?|left)\b`.
- `NAME`: some listed player's last name appears as a whole word. Last name = the last word of the listed name after dropping `Jr.`, `Sr.`, `II`, `III`, `IV`; at least 4 letters; both sides compared with accents removed (Unicode NFKD, combining marks dropped).

| Stratum | Rule | Test items | Dev items |
| --- | --- | ---: | ---: |
| A | `AVAIL` and `NAME` | 120 | 24 |
| B | Exactly one of them | 50 | 10 |
| C | Neither | 30 | 6 |

**Order and picking, per stratum:**

1. Sort the stratum's items by the SHA-256 of `"shac-labels-v1|" + news_id`, as hex, ascending.
2. Drop near-duplicates, keeping the first in that order. Two items are near-duplicates when their text is equal after: removing the outlet (for `google_news`, everything from the last `" - "` on), lower-casing, and removing every character that is not a letter or digit.
3. Group by the US Eastern calendar month of `fetched_at`. Take one item from each month in turn (oldest month first, then round again), always the month's next item in the order of step 1, skipping an item if its first `query_team_id` already has 3 picks in this stratum.
4. Stop at the stratum's total (A 144, B 60, C 36). Number the picks in the order taken. Every sixth pick (6, 12, 18, ...) is `dev`; the rest are `test`.
5. If a stratum runs out, take the shortfall from the next stratum (A, then B, then C) by the same steps, and record it in the pick list's commit message.

`pick_no` runs 1 to 240 over A, then B, then C.

## Order of work

1. **Pilot.** Owen labels the first 20 dev items (lowest `pick_no`). The lead or the `ai-extraction` agent reads the labels against this guide, and unclear rules are fixed here. A rule change after the pilot is a dated line under "Changes" below.
2. **Label the rest:** the other 20 dev items and the 200 test items, with the fixed guide.
3. **Second look.** At least 3 days after finishing, Owen labels 20 test items again (the 20 lowest `pick_no` values ending in 5; every sixth pick is dev and those are even, so these are all test items) on a fresh sheet that doesn't show his first labels. Agreement = the share of the 20 where the set of (player, status) is identical. It is reported with reading accuracy. Each disagreement is settled by Owen with a note, and the settled label is the one used. **If 3 or more of the 20 disagree,** the rules are not clear enough: fix the guide, re-check every test label against the fix, and only then let the model read the test set.
4. The label file, the pick list and this guide's final text are committed together. That is task 2.1's exit check.

The model's first run on the test set happens only after step 4.

## Changes

**2026-11-12, rewrite before any labeling.** Was the first draft enough for the 200 items? No. What was missing, and the fix:

| Gap | Fix |
| --- | --- |
| Most collected items are about another sport, another league, women's teams or old stories, and the draft had no rule for them | "Who gets an entry", rules 1 to 3; items published more than 72 hours before they were fetched are never shown |
| A title often doesn't say which team or sport a player belongs to | The teams' players are listed with numbers, and only a listed player can get an entry |
| Free-text player names can't be scored exactly | Entries use the list number; the script turns it into the athlete id |
| "Correct" was not defined | An item is right when its set of (player, status) equals the label's (EXTRACTION.md, section 7) |
| The pick was "across conferences and months", with no rule; a random pick would be mostly `none` | The pick list above: fixed strata, order, quotas and seed |
| No items to write the prompt on | 40 dev items next to the 200 test items |
| `is_change` asked the labeler what was already known, which an item doesn't say | Dropped. "New" is decided from box scores (EXTRACTION.md, section 6) |
| `game_scope` had overlapping values (`next_game`, `named_game`) that need a schedule to tell apart | Four values that depend on the text alone |
| Common wordings had no row ("left the game", "awaiting test results", "expected to miss", "out 4-6 weeks", a question headline) | Added to the status table and hard cases |
| No check on the labels themselves | The second look |
