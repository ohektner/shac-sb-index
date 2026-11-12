# Roster continuity: a candidate input, designed but not run

Written 2026-11-12 by the lead. **On hold by Owen's decision until after the first real scorecard run.** This page only fixes the design, so that when it is tried nothing is chosen after seeing a result. The DECISIONS.md entry of 2026-11-12 makes it part of the record.

## The idea

Early in a season Torvik's ratings lean on his preseason projection. A team that brings back most of last season's minutes may be better (or worse) than that projection says, and the gap should fade as real games replace the projection. The candidate asks whether that is worth anything on top of the two-input model.

## Definition

For team `t`, season `S`, before game `g`:

- `k` = the team's games in season `S` that tipped at least one minute before `g` and have a box score.
- `s_prev(a)` = player `a`'s share of team `t`'s total minutes in season `S − 1` (all of that season's games; it is over before `S` starts).
- `s_now(a)` = player `a`'s share of team `t`'s total minutes over those `k` earlier games.
- `continuity = sum over players of min(s_prev(a), s_now(a))`. Between 0 and 1. A player counts only for the same team in both seasons (`athlete_id` and `team_id` from `player_box`).
- `fade(k) = (10 − k) / 9` for `k` from 1 to 10, and 0 after. So 1 after one game, 0 from the tenth on.
- `f_t = (continuity − mu) × fade(k)`, and `f_t = 0` when `k = 0` or the team has no `S − 1` minutes (new to D-I). `mu` is the mean `continuity` over all team-games with `k` from 1 to 10 in the seasons being fit on. It is fit with the model, on training seasons only, and stored with the weights.
- **The input:** `cont_diff = f_home − f_away`. It flips sign when the teams swap, like every other input.

Why this form and not "share of last season's minutes that returned": that share climbs as more players appear, so a team with three games played would look more continuous than one with one. The min-of-shares form (the usual minutes-continuity measure) doesn't drift with `k`.

**Nothing is known before a team's first game,** so the input is 0 there. Rosters would fill that gap, but CBBD's roster endpoint has no date and is not point-in-time. It is not used.

## Leakage rules

1. `s_now` reads only box scores of the team's games that tipped strictly earlier. Never the game itself, a same-tip game or a later one. Test: rewriting the box of `g` or any later game leaves `cont_diff` for `g` unchanged.
2. `s_prev` reads season `S − 1` only.
3. `mu` comes from training seasons only. Test: rewriting season S or later leaves the `mu` used for S unchanged.
4. **Live:** CBBD box scores are fetched only once a window is 7 days old (`docs/CBBD_INGEST.md`). The input can't ship live until recent box scores are fetched daily and a test on 2026-27 games shows the live value equals the backtest value.

## Data needed first

2018-19 has no season before it in our data. Backfill 2017-18 `games` and `games/players` from CBBD (about 25 calls). 2017-18 is used only for `s_prev`; it is never trained on or scored.

## How it is judged

The existing rule, unchanged (`docs/MODEL.md`): add `cont_diff` to `core` as feature set `continuity`; compare by leave-one-season-out error against final scores on 2019, 2020 and 2022 with `compare_feature_sets`; **keep it only if the gain is at least 2 standard errors and it wins in all three seasons.** No line is involved, and the held-out seasons are not looked at. One run. `fade`'s length (10 games) and shape are fixed here and are not tuned; a second variant would be a new dated entry before it is run.

If it passes: a dated DECISIONS.md entry, model `v2` parameters file, used only for games priced after that, and only once leakage rule 4 is met. If it fails: recorded in MODEL.md's table, and the model stays as it is.
