"""Tests for `ratings_daily` and the Time Machine as-of check (task 1.4).

No network and no private data: team names, ids and ratings below are made up.
"""

import gzip
import json

import duckdb
import pandas as pd
import pytest

from shac.tables import torvik
from shac.tables.findings import split_findings
from shac.tables.timemachine_check import SQL_PATH
from shac.teams.sources import load_torvik_names

ALIASES = pd.DataFrame(
    {
        "source": ["torvik", "torvik", "torvik", "cbbd"],
        "alias": ["Old Name St.", "New Name", "Alpha", "Alpha"],
        "valid_from_season": [2019, 2025, 2019, 2019],
        "valid_to_season": pd.array([2024, None, None, None], dtype="Int64"),
        "team_id": [7, 7, 1, 1],
    }
)


def tm_row(rank, team, record, adj_o=110.0, adj_d=95.0, tempo=68.0):
    """A 45-field Time Machine row; only the fields we read are meaningful."""
    row = [0] * 45
    row[0], row[1], row[2], row[3] = rank, team, "X", record
    row[4], row[6], row[8], row[44] = adj_o, adj_d, 0.8, tempo
    return row


def write_tm(data_dir, season, ymd, rows, fetched="2026-11-10T21:30:00Z"):
    folder = data_dir / "torvik" / "timemachine" / str(season)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ymd}_team_results.json.gz"
    path.write_bytes(gzip.compress(json.dumps(rows).encode()))
    manifest = data_dir / "torvik" / "timemachine" / "manifest.csv"
    if not manifest.exists():
        manifest.write_text(
            "fetched_at_utc,season,source,status,http_status,n_rows,file,sha256,bytes\n"
        )
    rel = path.relative_to(data_dir).as_posix()
    with manifest.open("a") as handle:
        handle.write(f"{fetched},{season},x,ok,200,{len(rows)},{rel},x,1\n")


def write_daily(data_dir, folder, name, rows, header=True):
    path = data_dir / "torvik" / "raw" / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if header:
        lines.append(",".join(["rank", "team"] + [f"c{i}" for i in range(43)]))
    lines += [",".join(str(v) for v in r) for r in rows]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))


def test_as_of_is_six_am_eastern_the_next_day_across_dst():
    dates = pd.Series(pd.to_datetime(["2025-01-23", "2025-03-09", "2024-11-02"]))
    got = torvik.time_machine_as_of(dates).dt.strftime("%Y-%m-%dT%H:%M").tolist()
    # EST is UTC-5, EDT is UTC-4. 2025-03-10 and 2024-11-03 are both after the switch.
    assert got == ["2025-01-24T11:00", "2025-03-10T10:00", "2024-11-03T11:00"]


def test_parse_record_rejects_anything_but_w_l():
    ok = torvik.parse_record(pd.Series(["3-1", "0-0"]))
    assert ok["wins"].tolist() == [3, 0] and ok["losses"].tolist() == [1, 0]
    with pytest.raises(ValueError):
        torvik.parse_record(pd.Series(["3-1", "N/A"]))


def test_old_names_map_only_in_their_seasons_and_preseason_games_are_dropped(tmp_path):
    # 2024: the old name applies. 2025: only the new name does, so a stray old name
    # in a 2025 file is unmatched and counted, not silently mapped.
    write_tm(
        tmp_path, 2024, "20231105", [tm_row(1, "Old Name St.", "0-0"), tm_row(2, "Alpha", "0-0")]
    )
    write_tm(
        tmp_path, 2024, "20231106", [tm_row(1, "Old Name St.", "1-0"), tm_row(2, "Alpha", "0-1")]
    )
    # 2025's first file already shows a game for Alpha: that row is dropped.
    write_tm(tmp_path, 2025, "20241103", [tm_row(1, "New Name", "0-0"), tm_row(2, "Alpha", "1-0")])
    write_tm(
        tmp_path, 2025, "20241104", [tm_row(1, "Old Name St.", "1-0"), tm_row(2, "Alpha", "1-0")]
    )

    wide = torvik.load_time_machine(tmp_path)
    empty_daily = torvik.load_daily(tmp_path)
    table, counts = torvik.build_ratings_daily(wide, empty_daily, ALIASES)

    assert counts["time_machine_unmatched_names"] == 1
    assert counts["time_machine_preseason_rows_dropped"] == 1
    assert list(table.columns) == torvik.RATINGS_COLUMNS
    rows = {(str(r.rating_date), r.team_id): r for r in table.itertuples()}
    assert (rows[("2023-11-06", 7)].wins, rows[("2023-11-06", 7)].losses) == (1, 0)
    assert ("2024-11-03", 1) not in rows  # pre-season row with a game: dropped
    assert ("2024-11-04", 7) not in rows  # old name in a 2025 file: unmatched
    assert ("2024-11-03", 7) in rows  # new name in 2025: matched
    as_of = rows[("2023-11-06", 1)].as_of
    assert as_of.isoformat() == "2023-11-07T11:00:00+00:00"
    assert str(rows[("2023-11-06", 1)].fetched_at) == "2026-11-10 21:30:00+00:00"
    assert set(table["source"]) == {"torvik_time_machine"}


def test_wrong_field_count_raises(tmp_path):
    write_tm(tmp_path, 2024, "20231105", [tm_row(1, "Alpha", "0-0")[:44]])
    with pytest.raises(ValueError, match="expected 45"):
        torvik.load_time_machine(tmp_path)


def test_daily_pulls_keep_first_per_eastern_date_and_skip_season_end_files(tmp_path):
    write_tm(tmp_path, 2024, "20231105", [tm_row(1, "Alpha", "0-0")])
    row = tm_row(1, "Alpha", "2-1")
    # Two pulls on the same Eastern date (12:00 and 20:00 UTC on Nov 10) and one
    # 03:00 UTC on Nov 11, which is still Nov 10 in Eastern time.
    write_daily(tmp_path, "2026/11/10", "2027_team_results_20261110T120000Z.csv.gz", [row])
    write_daily(tmp_path, "2026/11/10", "2027_team_results_20261110T200000Z.csv.gz", [row])
    write_daily(
        tmp_path, "2026/11/11", "2027_team_results_20261111T030000Z.csv.gz", [row], header=False
    )
    # A season-END file for a past season: never a point-in-time pull.
    write_daily(tmp_path, "seasons", "2019_team_results_20261110T152018Z.csv.gz", [row])

    daily = torvik.load_daily(tmp_path)
    assert set(daily["season"]) == {2027}
    table, counts = torvik.build_ratings_daily(torvik.load_time_machine(tmp_path), daily, ALIASES)
    pulls = table[table["source"] == "torvik_daily"]
    assert counts["daily_rows"] == 1 and len(pulls) == 1
    first = pulls.iloc[0]
    assert str(first.rating_date) == "2026-11-10"
    assert first.as_of == first.fetched_at
    assert first.as_of.isoformat() == "2026-11-10T12:00:00+00:00"


def test_team_map_reads_time_machine_names(tmp_path):
    write_tm(tmp_path, 2021, "20201125", [tm_row(1, "Old Name St.", "0-0")])
    names = load_torvik_names(tmp_path)
    assert names.to_dict("records") == [
        {"season": 2021, "file_kind": "timemachine", "name": "Old Name St."}
    ]


def run_findings(tm_rows, game_rows):
    """Run sql/checks/timemachine_asof.sql on tiny frames; {finding name: DataFrame}."""
    tm = pd.DataFrame(tm_rows, columns=["season", "rating_date", "team_id", "games"])
    tm["rating_date"] = pd.to_datetime(tm["rating_date"])
    tm["as_of"] = torvik.time_machine_as_of(tm["rating_date"])
    tm["rating_date"] = tm["rating_date"].dt.date
    games = pd.DataFrame(game_rows, columns=["season", "game_id", "team_id", "tip_at"])
    games["tip_at"] = pd.to_datetime(games["tip_at"], utc=True)
    con = duckdb.connect()
    con.register("tm", tm)
    con.register("team_games", games)
    return {
        name: con.execute(q).df()
        for name, q in split_findings(SQL_PATH.read_text(encoding="utf-8"))
    }


def test_asof_check_counts_late_games_in_the_right_window_and_flags_a_leak():
    # Team 1 plays Nov 6 at 1:30 a.m. Eastern on Nov 7 (a Hawaii game): file Nov 6
    # counts it. Under the 06:00 cutoff that fits; under midnight it looks early.
    # Team 2's file Nov 7 counts a game that tips at 11 a.m. Eastern on Nov 8: a leak.
    tm_rows = [
        (2019, "2018-11-05", 1, 0),
        (2019, "2018-11-06", 1, 1),
        (2019, "2018-11-07", 1, 1),
        (2019, "2018-11-06", 2, 0),
        (2019, "2018-11-07", 2, 1),
        (2019, "2018-11-08", 2, 1),
    ]
    game_rows = [
        (2019, 10, 1, "2018-11-07T06:30:00Z"),  # 01:30 Eastern, Nov 7
        (2019, 20, 2, "2018-11-08T16:00:00Z"),  # 11:00 Eastern, Nov 8
    ]
    out = run_findings(tm_rows, game_rows)

    window = out["window_test"].set_index("cutoff")
    six, midnight = window.loc["06:00 ET on D+1"], window.loc["00:00 ET on D+1"]
    # Team 1 fits at 06:00; team 2's early count is "more" under both cutoffs.
    assert (six["file_counts_more"], six["file_counts_fewer"]) == (1, 1)
    assert midnight["file_counts_more"] == 2

    leak = out["leak_check"].iloc[0]
    assert (leak["team_games_with_file"], leak["flagged"], leak["teams_flagged"]) == (2, 1, 1)
    runs = out["leak_check_team_seasons"]
    assert runs[["games_flagged", "first_flagged_game_no"]].values.tolist() == [[1, 1]]
