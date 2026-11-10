"""Tests for the private-table builder. No network and no private data: invented fixtures.

Team ids, game ids, names and lines below are made up.
"""

from pathlib import Path

import pandas as pd
import pytest

from shac.tables import cbbd, odds, raw
from shac.tables.build import build_closing_lines, write_parquet
from shac.tables.check import SQL_PATH, open_tables, run_checks

TEAMS = pd.DataFrame(
    {
        "team_id": [1, 2, 3, 4],
        "name": ["Alpha", "Bravo", "Charlie", "Delta"],
        "conference": ["X", "X", "Y", "Y"],
        "first_season": [2019, 2019, 2019, 2023],
        "last_season": [2027, 2027, 2027, 2027],
    }
)
ALIASES = pd.DataFrame(
    {
        "source": ["odds_api"] * 3,
        "alias": ["Alpha Ants", "Bravo Bees", "Charlie Cats"],
        "valid_from_season": [2019, 2019, 2019],
        "valid_to_season": pd.array([None, None, None], dtype="Int64"),
        "team_id": [1, 2, 3],
    }
)


def cbbd_game(game_id, home, away, season=2026, status="final", hp=70, ap=60, periods=2, **kw):
    """A CBBD /games record with only the fields the builder reads."""

    def split(points):
        if points is None:
            return []
        first = [points // periods] * (periods - 1)
        return first + [points - sum(first)]

    rec = {
        "id": game_id,
        "season": season,
        "seasonType": "regular",
        "startDate": "2026-01-10T01:00:00.000Z",
        "startTimeTbd": False,
        "neutralSite": False,
        "conferenceGame": True,
        "status": status,
        "homeTeamId": home,
        "awayTeamId": away,
        "homePoints": hp,
        "awayPoints": ap,
        "homePeriodPoints": split(hp),
        "awayPeriodPoints": split(ap),
    }
    rec.update(kw)
    return rec


def wrap(fetched_at, data):
    return {"fetched_at": fetched_at, "source": "test", "data": data}


# ---------- de-duplication ----------


def test_dedupe_keeps_most_recent_fetch_and_counts_duplicates():
    early = wrap("2026-11-10T15:00:00Z", [cbbd_game(10, 1, 2, hp=50), cbbd_game(11, 1, 3)])
    late = wrap("2026-11-10T16:00:00Z", [cbbd_game(10, 1, 2, hp=99)])
    # File order on disk must not matter: pass the later file first.
    out = raw.dedupe_files([("b.json.gz", late), ("a.json.gz", early)], key=lambda g: g["id"])
    assert out.n_raw == 3
    assert out.n_duplicates == 1
    by_id = {rec["id"]: (fetched, rec) for fetched, rec in out.records}
    assert by_id[10][1]["homePoints"] == 99
    assert by_id[10][0] == "2026-11-10T16:00:00Z"


# ---------- D-I filter, results, overtimes ----------


def _deduped(*records, fetched="2026-11-10T15:00:00Z"):
    return raw.dedupe_files([("f", wrap(fetched, list(records)))], key=lambda g: g["id"])


def test_games_keep_only_di_vs_di():
    deduped = _deduped(
        cbbd_game(1, 1, 2),  # both D-I
        cbbd_game(2, 1, 99),  # 99 is not in teams.csv
        cbbd_game(3, 4, 1, season=2022),  # team 4 is D-I only from 2023
        cbbd_game(4, 4, 1, season=2023),  # now it is
    )
    games = cbbd.build_games(deduped, TEAMS)
    assert sorted(games["game_id"].tolist()) == [1, 4]
    assert str(games["tip_at"].dt.tz) == "UTC"


def test_overtimes_from_period_points():
    assert cbbd.overtimes_from_periods([30, 40], [35, 30]) == 0
    assert cbbd.overtimes_from_periods([30, 35, 8], [35, 30, 5]) == 1
    assert cbbd.overtimes_from_periods([30, 35, 8, 9, 4], [35, 30, 8, 9, 2]) == 3
    assert cbbd.overtimes_from_periods([], []) is None
    assert cbbd.overtimes_from_periods(None, None) is None
    assert cbbd.overtimes_from_periods([70], [60]) is None
    assert cbbd.overtimes_from_periods([30, 40], [30, 35, 5]) is None


def test_results_only_for_final_games_with_margin_and_overtimes():
    deduped = _deduped(
        cbbd_game(1, 1, 2, hp=80, ap=75, periods=3),
        cbbd_game(2, 1, 3, status="cancelled"),
        cbbd_game(3, 2, 3, hp=None, ap=None),
    )
    games = cbbd.build_games(deduped, TEAMS)
    results = cbbd.build_results(deduped, games)
    assert results["game_id"].tolist() == [1]
    row = results.iloc[0]
    assert row["home_margin"] == 5
    assert row["overtimes"] == 1


# ---------- player box: did-not-play rule ----------


def test_player_box_did_not_play_rule_and_non_di_games_dropped():
    games = cbbd.build_games(_deduped(cbbd_game(1, 1, 2)), TEAMS)
    team_game = {
        "gameId": 1,
        "teamId": 1,
        "players": [
            {"athleteId": 100, "name": "A", "minutes": 31, "starter": True},
            {"athleteId": 101, "name": "B", "minutes": 0, "starter": False},
            {"athleteId": 102, "name": "C", "minutes": None, "starter": False},
        ],
    }
    other_game = {"gameId": 999, "teamId": 1, "players": [{"athleteId": 103, "minutes": 5}]}
    deduped = raw.dedupe_files(
        [("f", wrap("2026-11-10T15:00:00Z", [team_game, other_game]))],
        key=lambda r: (r["gameId"], r["teamId"]),
    )
    box, dropped = cbbd.build_player_box(deduped, games)
    played = dict(zip(box["athlete_id"], box["played"], strict=True))
    assert played == {100: True, 101: False, 102: False}
    assert dropped == {"no_athlete_id": 0, "duplicate_athlete": 0}


# ---------- odds snapshots and closing lines ----------

TIP = "2026-11-12T00:00:00Z"


def odds_event(event_id, books, home="Alpha Ants", away="Bravo Bees", tip=TIP):
    """An Odds API event. `books` maps bookmaker key -> home spread."""
    return {
        "id": event_id,
        "commence_time": tip,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": key,
                "markets": [
                    {
                        "key": "spreads",
                        "last_update": "2026-11-11T10:00:00Z",
                        "outcomes": [
                            {"name": away, "price": -110, "point": -spread},
                            {"name": home, "price": -110, "point": spread},
                        ],
                    }
                ],
            }
            for key, spread in books.items()
        ],
    }


def odds_fixture():
    """Four snapshots: two pre-tip, one exactly at tip, one after tip."""
    games = cbbd.build_games(
        _deduped(cbbd_game(500, 1, 2, startDate="2026-11-12T00:00:00.000Z", season=2027)), TEAMS
    )
    files = [
        (
            "odds_1.json.gz",
            wrap(
                "2026-11-11T13:17:00Z",
                [odds_event("ev1", {"draftkings": -3.0, "fanduel": -3.5})],
            ),
        ),
        (
            "odds_2.json.gz",
            wrap(
                "2026-11-11T23:17:00Z",
                [
                    odds_event(
                        "ev1",
                        {
                            "draftkings": -4.0,
                            "fanduel": -6.0,
                            "betmgm": -4.5,
                            "pinnacle": -5.0,
                            "unibet_se": -9.0,  # an eu book: never in the US median
                        },
                    )
                ],
            ),
        ),
        # Exactly at tip: must be excluded.
        ("odds_3.json.gz", wrap(TIP, [odds_event("ev1", {"draftkings": -10.0, "pinnacle": -10})])),
        ("odds_4.json.gz", wrap("2026-11-12T01:17:00Z", [])),
    ]
    flat, counts = odds.flatten_snapshots(files)
    matches = odds.match_events(flat, games, ALIASES)
    snaps = odds.attach_game_ids(flat, matches)
    times = cbbd.utc(pd.Series([w["fetched_at"] for _n, w in files]))
    return snaps, times, games, matches, counts


EMPTY_CBBD_LINES = pd.DataFrame(
    {
        "game_id": pd.array([], dtype="Int64"),
        "provider": pd.Series([], dtype=str),
        "home_spread": pd.array([], dtype="Float64"),
        "home_spread_open": pd.array([], dtype="Float64"),
    }
)


def test_flatten_reads_home_spread_from_home_outcome():
    snaps, _times, _games, matches, counts = odds_fixture()
    assert counts == {"no_home_outcome": 0, "duplicate_rows": 0}
    first = snaps[(snaps["book"] == "fanduel") & (snaps["home_spread"] == -3.5)]
    assert len(first) == 1
    assert matches.iloc[0]["match_type"] == "matched"
    assert (snaps["game_id"] == 500).all()


def test_own_snapshot_close_is_median_of_us_books_strictly_before_tip():
    snaps, times, *_ = odds_fixture()
    close = build_closing_lines(snaps, times, EMPTY_CBBD_LINES)
    own = close[close["source"] == "own_snapshots"].iloc[0]
    # Snapshot 3 sits exactly at tip and is skipped; snapshot 2 is the close.
    assert own["close_snapshot_at"] == pd.Timestamp("2026-11-11T23:17:00Z")
    assert own["minutes_before_tip"] == 43
    # Median of the US books -4.0, -6.0, -4.5 (unibet_se and pinnacle left out).
    assert own["close_home_spread"] == -4.5
    assert own["n_books"] == 3
    # Open = first snapshot with the game: median of -3.0 and -3.5.
    assert own["open_home_spread"] == -3.25
    assert own["open_snapshot_at"] == pd.Timestamp("2026-11-11T13:17:00Z")
    assert own["basis"] == "median_us_books"


def test_pinnacle_row_only_when_pinnacle_present():
    snaps, times, *_ = odds_fixture()
    close = build_closing_lines(snaps, times, EMPTY_CBBD_LINES)
    pin = close[close["source"] == "pinnacle"]
    assert len(pin) == 1
    assert pin.iloc[0]["close_home_spread"] == -5.0
    assert pin.iloc[0]["n_books"] == 1
    # Pinnacle's first appearance is snapshot 2, so its open is the same snapshot.
    assert pin.iloc[0]["open_snapshot_at"] == pd.Timestamp("2026-11-11T23:17:00Z")

    no_pinnacle = snaps[snaps["book"] != "pinnacle"]
    close2 = build_closing_lines(no_pinnacle, times, EMPTY_CBBD_LINES)
    assert set(close2["source"]) == {"own_snapshots"}


def test_no_close_until_a_snapshot_at_or_after_tip_exists():
    snaps, times, *_ = odds_fixture()
    early_times = times[times < pd.Timestamp(TIP)]
    close = build_closing_lines(snaps, early_times, EMPTY_CBBD_LINES)
    assert close.empty


def test_cbbd_close_prefers_draft_kings_else_median_without_consensus():
    lines = pd.DataFrame(
        {
            "game_id": pd.array([1, 1, 1, 2, 2, 2, 2, 3], dtype="Int64"),
            "provider": [
                "Draft Kings",
                "Bovada",
                "ESPN BET",
                "Bovada",
                "ESPN BET",
                "teamrankings",
                "consensus",  # sign-flipped on purpose: must not enter the median
                "consensus",  # the only line for game 3: no cbbd close at all
            ],
            "home_spread": pd.array([-2.0, -7.0, -8.0, 3.0, 4.0, 8.0, -4.0, 5.0], dtype="Float64"),
            "home_spread_open": pd.array(
                [-1.0, None, None, 2.0, None, 6.0, -9.0, None], dtype="Float64"
            ),
        }
    )
    snaps, times, *_ = odds_fixture()
    close = build_closing_lines(snaps.iloc[0:0], times, lines)
    by_game = close.set_index("game_id")
    assert by_game.loc[1, "basis"] == "draftkings"
    assert by_game.loc[1, "close_home_spread"] == -2.0
    assert by_game.loc[1, "open_home_spread"] == -1.0
    assert by_game.loc[2, "basis"] == "median_cbbd_providers"
    assert by_game.loc[2, "close_home_spread"] == 4.0
    assert by_game.loc[2, "n_books"] == 3
    assert by_game.loc[2, "open_home_spread"] == 4.0
    assert 3 not in by_game.index
    assert by_game["close_snapshot_at"].isna().all()


def test_suspect_sign_flags_spread_and_moneyline_disagreement():
    assert cbbd.suspect_sign(-6.5, -250, 200) is False  # home favored both ways
    assert cbbd.suspect_sign(6.5, -250, 200) is True  # spread says away, moneyline home
    assert cbbd.suspect_sign(-6.5, 200, -250) is True
    assert cbbd.suspect_sign(-6.5, None, 200) is False  # missing moneyline
    assert cbbd.suspect_sign(None, -250, 200) is False  # missing spread
    assert cbbd.suspect_sign(0.0, -250, 200) is False  # pick'em: no spread favorite
    assert cbbd.suspect_sign(-1.0, -110, -110) is False  # no moneyline favorite


def test_cbbd_lines_keep_consensus_rows_with_suspect_flag():
    games = cbbd.build_games(_deduped(cbbd_game(1, 1, 2)), TEAMS)
    rec = {
        "gameId": 1,
        "homeTeamId": 1,
        "awayTeamId": 2,
        "lines": [
            {"provider": "consensus", "spread": 7.0, "homeMoneyline": -300, "awayMoneyline": 250},
            {"provider": "Bovada", "spread": -7.0, "homeMoneyline": -300, "awayMoneyline": 250},
            {"provider": "ESPN BET", "spread": -7.0},
        ],
    }
    deduped = raw.dedupe_files(
        [("f", wrap("2026-11-10T15:00:00Z", [rec]))], key=lambda r: r["gameId"]
    )
    lines, _ = cbbd.build_cbbd_lines(deduped, games)
    flags = dict(zip(lines["provider"], lines["suspect_sign"], strict=True))
    assert flags == {"Bovada": False, "ESPN BET": False, "consensus": True}
    assert lines.loc[lines["provider"] == "consensus", "home_spread"].item() == 7.0


def test_home_away_disagreement_is_reported_not_flipped():
    # CBBD lists Bravo at home; the odds feed lists Alpha at home.
    games = cbbd.build_games(
        _deduped(cbbd_game(600, 2, 1, startDate="2026-11-12T00:00:00.000Z", season=2027)), TEAMS
    )
    files = [("odds_1", wrap("2026-11-11T13:17:00Z", [odds_event("ev9", {"draftkings": -3})]))]
    flat, _ = odds.flatten_snapshots(files)
    matches = odds.match_events(flat, games, ALIASES)
    row = matches.iloc[0]
    assert row["match_type"] == "swapped"
    assert pd.isna(row["game_id"])
    assert row["candidate_game_id"] == 600
    snaps = odds.attach_game_ids(flat, matches)
    assert snaps["game_id"].isna().all()
    # Home spread stays as the feed gave it, for the feed's home team.
    assert snaps.iloc[0]["home_spread"] == -3


def test_match_types_unmapped_and_no_game():
    games = cbbd.build_games(_deduped(cbbd_game(1, 1, 2)), TEAMS)  # far from the odds date
    files = [
        (
            "odds_1",
            wrap(
                "2026-11-11T13:17:00Z",
                [
                    odds_event("evA", {"draftkings": -3}),
                    odds_event("evB", {"draftkings": -3}, home="Unknown Uglies"),
                ],
            ),
        )
    ]
    flat, _ = odds.flatten_snapshots(files)
    matches = odds.match_events(flat, games, ALIASES).set_index("odds_event_id")
    assert matches.loc["evA", "match_type"] == "no_game"
    assert matches.loc["evB", "match_type"] == "unmapped_team"


# ---------- SQL checks ----------


def _write_tables(tmp_path: Path, closing: pd.DataFrame) -> Path:
    snaps, times, games, matches, _ = odds_fixture()
    deduped = _deduped(cbbd_game(500, 1, 2, startDate="2026-11-12T00:00:00.000Z", season=2027))
    tables = {
        "games": games,
        "results": cbbd.build_results(deduped, games),
        "player_box": cbbd.build_player_box(
            raw.dedupe_files([("f", wrap("2026-11-10T00:00:00Z", []))], key=lambda r: r), games
        )[0],
        "cbbd_lines": cbbd.build_cbbd_lines(
            raw.dedupe_files([("f", wrap("2026-11-10T00:00:00Z", []))], key=lambda r: r), games
        )[0],
        "odds_snapshots": snaps,
        "odds_event_matches": matches,
        "closing_lines": closing,
    }
    for name, df in tables.items():
        write_parquet(df, tmp_path / "tables" / f"{name}.parquet")
    return tmp_path / "tables"


def _check_counts(tables_dir: Path) -> dict[str, int]:
    con, missing = open_tables(tables_dir)
    assert missing == []
    return dict(run_checks(con, SQL_PATH.read_text(encoding="utf-8")))


def test_checks_pass_on_good_tables(tmp_path):
    snaps, times, *_ = odds_fixture()
    closing = build_closing_lines(snaps, times, EMPTY_CBBD_LINES)
    counts = _check_counts(_write_tables(tmp_path, closing))
    assert all(n == 0 for n in counts.values()), counts


@pytest.mark.parametrize("bad", ["at_tip", "duplicate"])
def test_checks_catch_close_at_tip_and_duplicate_keys(tmp_path, bad):
    snaps, times, *_ = odds_fixture()
    closing = build_closing_lines(snaps, times, EMPTY_CBBD_LINES)
    if bad == "at_tip":
        closing.loc[0, "close_snapshot_at"] = pd.Timestamp(TIP)
        counts = _check_counts(_write_tables(tmp_path, closing))
        assert counts["k_closing_snapshot_at_or_after_tip"] == 1
    else:
        closing = pd.concat([closing, closing.iloc[[0]]], ignore_index=True)
        counts = _check_counts(_write_tables(tmp_path, closing))
        assert counts["g_closing_lines_duplicate_pk"] == 1


def test_check_catches_timestamp_without_timezone(tmp_path):
    snaps, times, *_ = odds_fixture()
    closing = build_closing_lines(snaps, times, EMPTY_CBBD_LINES)
    closing["close_snapshot_at"] = closing["close_snapshot_at"].dt.tz_localize(None)
    counts = _check_counts(_write_tables(tmp_path, closing))
    assert counts["l_timestamp_not_utc"] == 1
