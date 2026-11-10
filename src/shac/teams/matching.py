"""Decide which team a source's name means, and turn sightings into alias rows.

Order of trust when resolving a name for a season:

1. `manual_aliases.csv` (hand-written, checked by a person). Always wins.
2. An exact match of the normalized name against keys made from CBBD's own names for
   the teams that are D-I that season. A key shared by two teams counts as no match.
3. Nothing else is accepted. A fuzzy (closest-spelling) match is only offered as a
   suggestion in the review file for Owen to confirm or reject.
"""

from __future__ import annotations

import csv
import difflib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shac.teams.normalize import contiguous_runs, name_variants, normalize_name, ranges_overlap

PACKAGE_DIR = Path(__file__).parent
MANUAL_ALIASES_PATH = PACKAGE_DIR / "manual_aliases.csv"
MANUAL_TEAMS_PATH = PACKAGE_DIR / "manual_teams.csv"

# Sources whose names carry the mascot ("Duke Blue Devils").
MASCOT_SOURCES = {"odds_api"}


@dataclass(frozen=True)
class Team:
    """One team as CBBD describes it (or as `manual_teams.csv` does)."""

    team_id: int
    school: str
    display_name: str | None = None
    short_name: str | None = None
    abbreviation: str | None = None
    mascot: str | None = None


@dataclass(frozen=True)
class ManualAlias:
    """One row of `manual_aliases.csv`. Empty season ends mean 'any season'."""

    source: str
    alias: str
    valid_from: int | None
    valid_to: int | None
    team_id: int
    note: str = ""

    def applies(self, source: str, alias: str, season: int) -> bool:
        if self.source != source or self.alias != alias:
            return False
        lo = self.valid_from if self.valid_from is not None else 0
        return ranges_overlap(lo, self.valid_to, season, season)


@dataclass(frozen=True)
class Resolution:
    """The answer for one (source, name, season)."""

    team_id: int | None
    method: str  # "manual", "exact", or "unresolved"
    reason: str = ""
    suggested_team_id: int | None = None
    suggested_name: str | None = None


def _int_or_none(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text else None


def load_manual_aliases(path: Path = MANUAL_ALIASES_PATH) -> list[ManualAlias]:
    """Read the hand-written alias table. Lines starting with '#' are comments."""
    with open(path, newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    return [
        ManualAlias(
            source=row["source"].strip(),
            alias=row["alias"],
            valid_from=_int_or_none(row["valid_from_season"]),
            valid_to=_int_or_none(row["valid_to_season"]),
            team_id=int(row["team_id"]),
            note=(row.get("note") or "").strip(),
        )
        for row in csv.DictReader(lines)
    ]


def load_manual_teams(path: Path = MANUAL_TEAMS_PATH) -> list[dict]:
    """Read `manual_teams.csv`: D-I teams CBBD's saved `/teams` lists don't cover yet."""
    with open(path, newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    rows = []
    for row in csv.DictReader(lines):
        rows.append(
            {
                "team_id": int(row["team_id"]),
                "school": row["school"],
                "mascot": row["mascot"] or None,
                "conference": row["conference"],
                "first_season": int(row["first_season"]),
                "note": row.get("note", ""),
            }
        )
    return rows


def team_keys(team: Team, with_mascot: bool) -> set[str]:
    """All normalized keys that may stand for this team.

    Without mascot (CBBD, Torvik): the school name and CBBD's short name, plus their
    "State"->"St" and "Saint"->"St" spellings.
    With mascot (Odds API): each of school, short name, abbreviation and the display
    name's school part, followed by the mascot, e.g. "arizona st sun devils".
    """
    bases = [team.school, team.short_name]
    if not with_mascot:
        return {key for base in bases if base for key in name_variants(base)}
    if not team.mascot:
        return set()
    if team.display_name and team.display_name.endswith(" " + team.mascot):
        bases.append(team.display_name[: -len(team.mascot) - 1])
    bases.append(team.abbreviation)
    keys = set()
    for base in bases:
        if base:
            keys |= name_variants(f"{base} {team.mascot}")
    return keys


class Resolver:
    """Resolves (source, name, season) to a team id, given who is D-I each season."""

    def __init__(self, teams_by_season: dict[int, list[Team]], manual: list[ManualAlias]):
        self.teams_by_season = teams_by_season
        self.manual = manual
        self._index: dict[tuple[int, bool], dict[str, set[int]]] = {}
        self.names = {t.team_id: t.school for ts in teams_by_season.values() for t in ts}

    def index(self, season: int, with_mascot: bool) -> dict[str, set[int]]:
        """key -> team ids, for teams that are D-I in `season`."""
        cache_key = (season, with_mascot)
        if cache_key not in self._index:
            idx: dict[str, set[int]] = defaultdict(set)
            for team in self.teams_by_season.get(season, []):
                for key in team_keys(team, with_mascot):
                    idx[key].add(team.team_id)
            self._index[cache_key] = dict(idx)
        return self._index[cache_key]

    def resolve(self, source: str, alias: str, season: int) -> Resolution:
        hits = [m for m in self.manual if m.applies(source, alias, season)]
        if len({m.team_id for m in hits}) > 1:
            ids = sorted({m.team_id for m in hits})
            return Resolution(None, "unresolved", f"manual table conflict: ids {ids}")
        if hits:
            return Resolution(hits[0].team_id, "manual")

        idx = self.index(season, source in MASCOT_SOURCES)
        ids = idx.get(normalize_name(alias), set())
        if len(ids) == 1:
            return Resolution(next(iter(ids)), "exact")
        if len(ids) > 1:
            first = min(ids)
            return Resolution(
                None,
                "unresolved",
                f"ambiguous: name fits team ids {sorted(ids)}",
                first,
                self.names.get(first),
            )
        return self._suggest(alias, idx)

    def _suggest(self, alias: str, idx: dict[str, set[int]]) -> Resolution:
        close = difflib.get_close_matches(normalize_name(alias), list(idx), n=1, cutoff=0.6)
        if close and len(idx[close[0]]) == 1:
            tid = next(iter(idx[close[0]]))
            reason = f"no exact match; closest spelling '{close[0]}' (suggestion only)"
            return Resolution(None, "unresolved", reason, tid, self.names.get(tid))
        return Resolution(None, "unresolved", "no exact match and no close spelling")


@dataclass(frozen=True)
class AliasRow:
    source: str
    alias: str
    valid_from_season: int
    valid_to_season: int | None
    team_id: int


def alias_rows(
    sightings: dict[tuple[str, str, int], set[int]],
    open_end_seasons: dict[tuple[str, int], int],
) -> list[AliasRow]:
    """Turn sightings into alias rows with season ranges.

    `sightings` maps (source, alias, team_id) to the seasons that name was seen for that
    team. Gaps are bridged (a team that skipped 2021 keeps one row) unless another team
    used the same name inside the gap, in which case the row is split.

    `open_end_seasons[(source, team_id)]` is the season from which an alias counts as
    'still in use': if a row's last season reaches it, `valid_to_season` is left empty.
    """
    by_name: dict[tuple[str, str], dict[int, set[int]]] = defaultdict(dict)
    for (source, alias, team_id), seasons in sightings.items():
        by_name[(source, alias)][team_id] = seasons

    rows: list[AliasRow] = []
    for (source, alias), per_team in by_name.items():
        for team_id, seasons in per_team.items():
            others = set().union(*(s for t, s in per_team.items() if t != team_id))
            runs: list[tuple[int, int]] = []
            for start, end in contiguous_runs(seasons):
                gap = set(range(runs[-1][1] + 1, start)) if runs else set()
                if runs and not (gap & others):
                    runs[-1] = (runs[-1][0], end)
                else:
                    runs.append((start, end))
            for i, (start, end) in enumerate(runs):
                open_from = open_end_seasons.get((source, team_id))
                is_last = i == len(runs) - 1
                valid_to = None if is_last and open_from is not None and end >= open_from else end
                rows.append(AliasRow(source, alias, start, valid_to, team_id))
    rows.sort(key=lambda r: (r.source, r.alias, r.valid_from_season))
    return rows


def find_overlapping_aliases(rows: list[AliasRow]) -> list[tuple[AliasRow, AliasRow]]:
    """Pairs of rows where one source's name points to two teams in a shared season."""
    by_name: dict[tuple[str, str], list[AliasRow]] = defaultdict(list)
    for row in rows:
        by_name[(row.source, row.alias)].append(row)
    clashes = []
    for group in by_name.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if a.team_id != b.team_id and ranges_overlap(
                    a.valid_from_season, a.valid_to_season, b.valid_from_season, b.valid_to_season
                ):
                    clashes.append((a, b))
    return clashes
