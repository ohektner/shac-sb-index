"""Build the team map from the raw files in the private data repo.

Run as:
    uv run python -m shac.teams.build --data-dir <private repo> [--out-dir data]

Writes three public CSVs (names, ids, conferences and seasons only):
- data/teams.csv            one row per D-I team (team_id = CBBD team id)
- data/team_aliases.csv     every name each source uses, with the seasons it applies to
- data/team_map_review.csv  names that could not be matched with certainty, for Owen

Who counts as D-I: CBBD's per-season `/teams` list. For a season newer than the last
saved CBBD list (2027 today), the D-I teams are the ones in that season's Torvik
team_results file, matched against the last CBBD list plus `manual_teams.csv`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from shac.teams import sources
from shac.teams.matching import (
    Resolution,
    Resolver,
    Team,
    alias_rows,
    find_overlapping_aliases,
    load_manual_aliases,
    load_manual_teams,
)
from shac.teams.normalize import contiguous_runs

REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEW_COLUMNS = ["source", "alias", "seasons", "suggested_team_id", "suggested_name", "reason"]


def _clean(value):
    return None if value is None or (isinstance(value, float) and pd.isna(value)) else value


def _team_from_row(row) -> Team:
    return Team(
        team_id=int(row.team_id),
        school=row.school,
        display_name=_clean(row.display_name),
        short_name=_clean(row.short_name),
        abbreviation=_clean(row.abbreviation),
        mascot=_clean(row.mascot),
    )


def format_seasons(seasons: set[int]) -> str:
    """{2019, 2020, 2021, 2023} -> '2019-2021;2023'."""
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in contiguous_runs(seasons)]
    return ";".join(parts)


def build(data_dir: Path, out_dir: Path) -> dict:
    """Build all three CSVs. Returns counts for the summary printout."""
    cbbd_teams = sources.load_cbbd_teams(data_dir)
    games = sources.load_cbbd_games(data_dir)
    torvik = sources.load_torvik_names(data_dir)
    odds = sources.load_odds_names(data_dir)
    manual_aliases = load_manual_aliases()
    manual_teams = load_manual_teams()

    cbbd_seasons = sorted(cbbd_teams["season"].unique().tolist())
    last_cbbd = cbbd_seasons[-1]
    teams_by_season: dict[int, list[Team]] = {
        s: [_team_from_row(r) for r in cbbd_teams[cbbd_teams["season"] == s].itertuples()]
        for s in cbbd_seasons
    }
    di_ids: dict[int, set[int]] = {s: {t.team_id for t in ts} for s, ts in teams_by_season.items()}

    # Seasons newer than the last CBBD /teams list: candidates = last list + manual teams.
    seen_seasons = {int(s) for s in torvik["season"]} | {int(s) for s in odds["season"]}
    extra_seasons = sorted(s for s in seen_seasons if s > last_cbbd)
    for season in extra_seasons:
        candidates = list(teams_by_season[last_cbbd])
        for m in manual_teams:
            if m["first_season"] <= season:
                candidates.append(Team(m["team_id"], m["school"], mascot=m["mascot"]))
        teams_by_season[season] = candidates

    resolver = Resolver(teams_by_season, manual_aliases)
    sightings: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    unresolved: dict[tuple, set[int]] = defaultdict(set)
    counts: dict[str, int] = {}

    def note_unresolved(source: str, alias: str, season: int, res) -> None:
        key = (source, alias, res.suggested_team_id, res.suggested_name, res.reason)
        unresolved[key].add(season)

    # Torvik team_results: the D-I list on Torvik's side.
    results = torvik[torvik["file_kind"] == "team_results"]
    for season in sorted(results["season"].unique()):
        resolved_ids = set()
        for name in sorted(results.loc[results["season"] == season, "name"]):
            res = resolver.resolve("torvik", name, int(season))
            if res.team_id is None:
                note_unresolved("torvik", name, int(season), res)
            else:
                sightings[("torvik", name, res.team_id)].add(int(season))
                resolved_ids.add(res.team_id)
        if int(season) in extra_seasons:
            di_ids[int(season)] = resolved_ids
    for season in extra_seasons:
        if season not in di_ids:  # no Torvik team list for that season: keep all candidates
            di_ids[season] = {t.team_id for t in teams_by_season[season]}
        teams_by_season[season] = [
            t for t in teams_by_season[season] if t.team_id in di_ids[season]
        ]
    resolver = Resolver(teams_by_season, manual_aliases)

    # Torvik super_sked: names also in that season's team_results are covered above.
    # Others are non-D-I opponents, unless one exactly fits a D-I team (then: review).
    result_names = set(zip(results["season"], results["name"], strict=True))
    sked = torvik[torvik["file_kind"] == "super_sked"]
    skipped_sked = 0
    for season, name in zip(sked["season"], sked["name"], strict=True):
        if (season, name) in result_names:
            continue
        res = resolver.resolve("torvik", name, int(season))
        if res.method in ("manual", "exact"):
            res2 = Resolution(
                None,
                "unresolved",
                "super_sked name not in team_results but fits a D-I team: confirm",
                res.team_id,
                resolver.names.get(res.team_id),
            )
            note_unresolved("torvik", name, int(season), res2)
        else:
            skipped_sked += 1
    counts["torvik_sked_non_di_names_skipped"] = skipped_sked

    # CBBD: the school name plus every game name used for a D-I team that season.
    for season, ts in teams_by_season.items():
        if season > last_cbbd:
            continue
        for t in ts:
            sightings[("cbbd", t.school, t.team_id)].add(season)
    for side in ("home", "away"):
        for season, tid, name in zip(
            games["season"], games[f"{side}_team_id"], games[f"{side}_team"], strict=True
        ):
            if tid in di_ids.get(int(season), set()) and int(season) <= last_cbbd:
                sightings[("cbbd", name, int(tid))].add(int(season))

    # The Odds API.
    for season, name in zip(odds["season"], odds["name"], strict=True):
        res = resolver.resolve("odds_api", name, int(season))
        if res.team_id is None:
            note_unresolved("odds_api", name, int(season), res)
        else:
            sightings[("odds_api", name, res.team_id)].add(int(season))

    # Season ranges: a row is open-ended if the team is D-I now and the name was seen in
    # that source's latest season.
    current = max(di_ids)
    latest = {
        "cbbd": last_cbbd,
        "torvik": int(torvik["season"].max()) if len(torvik) else last_cbbd,
        "odds_api": int(odds["season"].max()) if len(odds) else current,
    }
    open_end = {(src, tid): latest[src] for src in latest for tid in di_ids[current]}
    rows = alias_rows(sightings, open_end)
    clashes = find_overlapping_aliases(rows)

    # teams.csv
    manual_by_id = {m["team_id"]: m for m in manual_teams}
    seasons_by_team: dict[int, set[int]] = defaultdict(set)
    for season, ids in di_ids.items():
        for tid in ids:
            seasons_by_team[tid].add(season)
    latest_cbbd = cbbd_teams.sort_values("season").groupby("team_id").last()
    team_rows = []
    for tid, seasons in sorted(seasons_by_team.items()):
        if tid in latest_cbbd.index:
            name = latest_cbbd.loc[tid, "school"]
            conference = latest_cbbd.loc[tid, "conference"]
        else:
            name = manual_by_id[tid]["school"]
            conference = manual_by_id[tid]["conference"]
        team_rows.append(
            {
                "team_id": tid,
                "name": name,
                "conference": conference,
                "first_season": min(seasons),
                "last_season": max(seasons),
            }
        )

    review_rows = [
        {
            "source": src,
            "alias": alias,
            "seasons": format_seasons(seasons),
            "suggested_team_id": sug_id if sug_id is not None else "",
            "suggested_name": sug_name or "",
            "reason": reason,
        }
        for (src, alias, sug_id, sug_name, reason), seasons in sorted(
            unresolved.items(), key=lambda kv: (kv[0][0], kv[0][1])
        )
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(team_rows).to_csv(out_dir / "teams.csv", index=False, lineterminator="\n")
    alias_df = pd.DataFrame([r.__dict__ for r in rows])
    alias_df["valid_to_season"] = alias_df["valid_to_season"].astype("Int64")
    alias_df.to_csv(out_dir / "team_aliases.csv", index=False, lineterminator="\n")
    with open(out_dir / "team_map_review.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(review_rows)

    counts.update(
        {
            "teams": len(team_rows),
            "alias_rows": len(rows),
            "review_rows": len(review_rows),
            "overlapping_alias_pairs": len(clashes),
        }
    )
    for src in ("cbbd", "torvik", "odds_api"):
        counts[f"alias_rows_{src}"] = sum(r.source == src for r in rows)
    for season in sorted(di_ids):
        counts[f"di_teams_{season}"] = len(di_ids[season])
    counts["_clashes"] = clashes
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)
    counts = build(args.data_dir, args.out_dir)
    clashes = counts.pop("_clashes")
    for key, value in counts.items():
        print(f"{key}: {value}")
    for a, b in clashes:
        print(f"CLASH: {a.source} '{a.alias}' -> {a.team_id} and {b.team_id}")
    return 1 if clashes else 0


if __name__ == "__main__":
    sys.exit(main())
