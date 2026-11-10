"""Tests for the team-name map. No network and no private data: small invented fixtures."""

from pathlib import Path

import pytest

from shac.teams.build import format_seasons
from shac.teams.check import split_checks
from shac.teams.matching import (
    MANUAL_ALIASES_PATH,
    AliasRow,
    ManualAlias,
    Resolver,
    Team,
    alias_rows,
    find_overlapping_aliases,
    load_manual_aliases,
)
from shac.teams.normalize import (
    contiguous_runs,
    name_variants,
    normalize_name,
    ranges_overlap,
)

# Invented ids; names and mascots follow CBBD's style.
TEAMS = [
    Team(168, "Miami", "Miami Hurricanes", "Miami", "MIA", "Hurricanes"),
    Team(167, "Miami (OH)", "Miami (OH) RedHawks", "Miami OH", "M-OH", "RedHawks"),
    Team(151, "Loyola Chicago", "Loyola Chicago Ramblers", "Loyola Chicago", "LUC", "Ramblers"),
    Team(152, "Loyola Maryland", "Loyola Maryland Greyhounds", "Loyola MD", "L-MD", "Greyhounds"),
    Team(153, "Loyola Marymount", "Loyola Marymount Lions", "LMU", "LMU", "Lions"),
    Team(323, "USC", "USC Trojans", "USC", "USC", "Trojans"),
    Team(266, "South Carolina", "South Carolina Gamecocks", "South Carolina", "SC", "Gamecocks"),
    Team(268, "South Carolina Upstate", None, "SC Upstate", "UPST", "Spartans"),
    Team(279, "St. John's", "St. John's Red Storm", "St John's", "SJU", "Red Storm"),
    Team(251, "Saint Joseph's", "Saint Joseph's Hawks", "Saint Joseph's", "JOES", "Hawks"),
    Team(183, "Mount St. Mary's", None, "Mount St Marys", "MSM", "Mountaineers"),
    Team(325, "UT Martin", "UT Martin Skyhawks", "UT Martin", "UTM", "Skyhawks"),
    Team(132, "Kansas State", "Kansas State Wildcats", "Kansas St", "KSU", "Wildcats"),
    Team(135, "Kentucky", "Kentucky Wildcats", "Kentucky", "UK", "Wildcats"),
    Team(278, "St. Francis (PA)", None, "St Francis PA", "SFPA", "Red Flash"),
    Team(364, "St. Francis Brooklyn", None, "St Francis BK", "SFBK", "Terriers"),
    Team(260, "San José State", "San José State Spartans", "San José St", "SJSU", "Spartans"),
    Team(72, "Duke", "Duke Blue Devils", "Duke", "DUKE", "Blue Devils"),
]
MANUAL = [
    ManualAlias("torvik", "Miami FL", None, None, 168),
    ManualAlias("torvik", "Tennessee Martin", None, None, 325),
    ManualAlias("torvik", "St. Francis NY", None, 2023, 364),
    ManualAlias("odds_api", "Tenn-Martin Skyhawks", None, None, 325),
]


@pytest.fixture
def resolver():
    by_season = {2023: TEAMS, 2024: [t for t in TEAMS if t.team_id != 364]}
    return Resolver(by_season, MANUAL)


# --- normalization -------------------------------------------------------------------


def test_normalize_drops_punctuation_case_and_accents():
    assert normalize_name("St. John's") == "st johns"
    assert normalize_name("  ST JOHN'S ") == "st johns"
    assert normalize_name("Miami (OH)") == "miami oh"
    assert normalize_name("Arkansas-Pine Bluff") == "arkansas pine bluff"
    assert normalize_name("San José St.") == "san jose st"
    assert normalize_name("Texas A&M-Corpus Christi") == "texas a&m corpus christi"


def test_variants_expand_state_and_saint_only_from_our_names():
    assert name_variants("Kansas State") == {"kansas state", "kansas st"}
    assert "st josephs" in name_variants("Saint Joseph's")
    # A source's "St." is never expanded, so "St. Joseph's" only matches via our Saint name.
    assert name_variants("St. Joseph's") == {"st josephs"}


# --- trap cases ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "alias", "expected"),
    [
        ("torvik", "Miami FL", 168),  # hand table
        ("torvik", "Miami OH", 167),  # exact after normalizing "Miami (OH)"
        ("torvik", "Loyola Chicago", 151),
        ("torvik", "Loyola MD", 152),
        ("torvik", "Loyola Marymount", 153),
        ("torvik", "USC", 323),
        ("torvik", "South Carolina", 266),
        ("torvik", "St. John's", 279),
        ("torvik", "Saint Joseph's", 251),
        ("torvik", "Mount St. Mary's", 183),
        ("torvik", "Tennessee Martin", 325),
        ("torvik", "Kansas St.", 132),
        ("torvik", "San Jose St.", 260),
        ("odds_api", "Duke Blue Devils", 72),
        ("odds_api", "Miami Hurricanes", 168),
        ("odds_api", "Miami (OH) RedHawks", 167),
        ("odds_api", "Kansas St Wildcats", 132),
        ("odds_api", "Kentucky Wildcats", 135),
        ("odds_api", "Tenn-Martin Skyhawks", 325),
        ("odds_api", "USC Trojans", 323),
    ],
)
def test_trap_names_resolve_to_the_right_team(resolver, source, alias, expected):
    assert resolver.resolve(source, alias, 2023).team_id == expected


@pytest.mark.parametrize(
    ("source", "alias"),
    [
        ("torvik", "Loyola"),  # which Loyola? must not guess
        ("torvik", "St. Francis"),  # PA or Brooklyn? must not guess
        ("torvik", "SC Upstat"),  # typo: fuzzy suggestion only
        ("odds_api", "Duke"),  # odds names need the mascot
        ("odds_api", "Wildcats"),
    ],
)
def test_uncertain_names_are_not_accepted(resolver, source, alias):
    res = resolver.resolve(source, alias, 2023)
    assert res.team_id is None
    assert res.method == "unresolved"


def test_fuzzy_match_is_only_a_suggestion(resolver):
    res = resolver.resolve("torvik", "SC Upstat", 2023)
    assert res.team_id is None
    assert res.suggested_team_id == 268
    assert "suggestion only" in res.reason


def test_ambiguous_key_is_not_accepted():
    twins = [Team(1, "Springfield"), Team(2, "Springfield")]
    res = Resolver({2025: twins}, []).resolve("torvik", "Springfield", 2025)
    assert res.team_id is None
    assert "ambiguous" in res.reason


def test_dropped_team_is_not_matched_after_it_leaves(resolver):
    # St. Francis Brooklyn is D-I in 2023 only in this fixture; the hand row ends in 2023.
    assert resolver.resolve("torvik", "St. Francis NY", 2023).team_id == 364
    assert resolver.resolve("torvik", "St. Francis NY", 2024).team_id is None


def test_manual_table_wins_and_conflicts_are_refused():
    manual = [
        ManualAlias("torvik", "X", None, None, 1),
        ManualAlias("torvik", "X", 2025, None, 2),
    ]
    r = Resolver({2024: [Team(1, "X"), Team(2, "Y")], 2025: [Team(1, "X"), Team(2, "Y")]}, manual)
    assert r.resolve("torvik", "X", 2024).team_id == 1
    assert r.resolve("torvik", "X", 2025).team_id is None


def test_rename_gets_separate_season_ranges():
    # East Texas A&M was Texas A&M-Commerce: same team, two names, back to back.
    sightings = {
        ("torvik", "Texas A&M Commerce", 76): {2023, 2024},
        ("torvik", "East Texas A&M", 76): {2025, 2026, 2027},
    }
    rows = alias_rows(sightings, {("torvik", 76): 2027})
    assert rows == [
        AliasRow("torvik", "East Texas A&M", 2025, None, 76),
        AliasRow("torvik", "Texas A&M Commerce", 2023, 2024, 76),
    ]


# --- season ranges -------------------------------------------------------------------


def test_contiguous_runs():
    assert contiguous_runs([2022, 2019, 2020, 2020]) == [(2019, 2020), (2022, 2022)]
    assert contiguous_runs([]) == []


def test_ranges_overlap_with_open_ends():
    assert ranges_overlap(2019, 2021, 2021, None)
    assert not ranges_overlap(2019, 2020, 2021, None)
    assert ranges_overlap(2019, None, 2030, 2031)


def test_alias_rows_bridge_a_gap_and_close_dropped_teams():
    sightings = {
        ("cbbd", "Bethune-Cookman", 23): {2019, 2020, 2022, 2023},  # sat out 2021
        ("cbbd", "Hartford", 363): {2019, 2020, 2021},  # left D-I
    }
    rows = alias_rows(sightings, {("cbbd", 23): 2023})
    assert AliasRow("cbbd", "Bethune-Cookman", 2019, None, 23) in rows
    assert AliasRow("cbbd", "Hartford", 2019, 2021, 363) in rows


def test_alias_rows_split_when_another_team_used_the_name_in_the_gap():
    sightings = {
        ("torvik", "Shared", 1): {2019, 2022},
        ("torvik", "Shared", 2): {2020, 2021},
    }
    rows = alias_rows(sightings, {})
    assert [(r.valid_from_season, r.valid_to_season, r.team_id) for r in rows] == [
        (2019, 2019, 1),
        (2020, 2021, 2),
        (2022, 2022, 1),
    ]
    assert find_overlapping_aliases(rows) == []


def test_duplicate_alias_detection():
    rows = [
        AliasRow("torvik", "Loyola", 2019, 2021, 151),
        AliasRow("torvik", "Loyola", 2021, None, 152),  # overlaps in 2021
        AliasRow("torvik", "Penn", 2019, 2020, 227),
        AliasRow("torvik", "Penn", 2021, None, 226),  # back to back: fine
        AliasRow("odds_api", "Loyola", 2019, None, 153),  # other source: fine
    ]
    clashes = find_overlapping_aliases(rows)
    assert len(clashes) == 1
    assert {clashes[0][0].team_id, clashes[0][1].team_id} == {151, 152}


def test_format_seasons():
    assert format_seasons({2019, 2020, 2021, 2023}) == "2019-2021;2023"


# --- the shipped hand table and SQL file ----------------------------------------------


def test_shipped_manual_table_has_no_conflicts():
    manual = load_manual_aliases(MANUAL_ALIASES_PATH)
    assert manual, "manual_aliases.csv should not be empty"
    keys = {}
    for m in manual:
        assert m.source in {"torvik", "odds_api", "kalshi", "news"}
        prev = keys.setdefault((m.source, m.alias), m.team_id)
        assert prev == m.team_id, f"{m.alias} maps to two teams"


def test_sql_file_has_all_checks():
    sql = (Path(__file__).parents[1] / "sql" / "checks" / "team_map.sql").read_text("utf-8")
    names = [name for name, _ in split_checks(sql)]
    for prefix in "abcde":
        assert any(n.startswith(prefix + "_") for n in names)
