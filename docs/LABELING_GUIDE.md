# Availability labeling guide (task 2.1, draft)

How Owen hand-labels about 200 news items so the AI layer's reading accuracy can be measured (G2 bar: 95%+). Draft for Owen to edit before labeling starts. The labels are the answer key, so they follow what the item **says**, not what happened later.

## The label file

`data/labels/availability_labels.csv` (public: short facts, no source text), one row per player mentioned in an item. An item that mentions nobody's availability gets one row with an empty `player_name` and `status = none`.

| Column | What to write |
| --- | --- |
| `news_id` | The item's id from `news_raw` |
| `team_id` | The player's team (see `data/teams.csv`) |
| `player_name` | As written in the item |
| `status` | One of `out`, `doubtful`, `questionable`, `probable`, `available`, `none` (see below) |
| `game_scope` | `next_game`, `named_game` (a specific game is named), `multiple_games`, `season`, or `unclear` |
| `is_change` | `true` if the item reports something new (a new injury, a return, a status change); `false` if it repeats a known, long-running absence |
| `labeler_note` | Optional: why, for a hard call |

## Status rules

Use the most specific status the item supports. When two readings are possible, take the one with **less** certainty (questionable over out, unless "out" is said).

| The item says (examples) | `status` |
| --- | --- |
| "out", "will not play", "ruled out", "won't travel", "suspended", "out for the season", "transferred", "left the team" | `out` |
| "doubtful", "unlikely to play" | `doubtful` |
| "questionable", "game-time decision", "day-to-day", "status uncertain", "being evaluated" | `questionable` |
| "probable", "expected to play", "likely to play", "should be available" | `probable` |
| "available", "cleared", "will play", "returns", "back in the lineup" | `available` |
| An injury is mentioned with no word on playing ("dealing with an ankle sprain") | `questionable` |
| A player is mentioned with nothing about availability (stats, recruiting, a quote) | no row for that player |
| Nothing in the item is about anyone's availability | one `none` row |

Official conference reports use their own words (ACC: Available / Probable / Questionable / Doubtful / Out; Big 12: Available / Game-time decision / Out). Map "Game-time decision" to `questionable`.

## Hard cases

- **Rumor or unnamed source** ("sources say he may miss time"): label what's claimed, add a note. The model is judged on reading, not on whether the source was right.
- **Unnamed player** ("a starter is out"): no row; mention it in a note on a `none` row only if nobody else is named.
- **Past games** ("missed last night's game"): `out` with `game_scope = unclear`, unless the item also says something about the next game.
- **Coach-speak** ("we'll see how he feels"): `questionable`.
- **Two items saying different things:** label each item on its own.

## Before labeling

1. Owen labels 20 items, then we compare notes and fix this guide.
2. The remaining items are labeled with the fixed guide. The guide's final version is committed with the label file (the exit check for task 2.1).
3. Items are picked across conferences and months, not just the easy ones. The pick list is saved, so it can't be changed after the model runs.
