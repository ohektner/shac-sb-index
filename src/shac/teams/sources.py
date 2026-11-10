"""Read the raw source files in the private data repo into small pandas tables.

Only names, ids, conferences and seasons are pulled out. Scores, lines and ratings are
never read here, so nothing else from the raw files can leak into the public CSVs.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from shac.teams.normalize import season_of

TEAM_FIELDS = ["id", "school", "displayName", "shortDisplayName", "abbreviation", "mascot"]


def _read_wrapped_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_cbbd_teams(data_dir: Path) -> pd.DataFrame:
    """One row per (season, team) from CBBD's per-season `/teams` lists: our D-I definition."""
    rows = []
    for path in sorted((data_dir / "cbbd" / "raw" / "teams").glob("*.json.gz")):
        wrapped = _read_wrapped_json(path)
        season = int(wrapped["params"]["season"])
        for team in wrapped["data"]:
            row = {field: team.get(field) for field in TEAM_FIELDS}
            row["conference"] = team.get("conference")
            row["season"] = season
            rows.append(row)
    df = pd.DataFrame(rows).rename(
        columns={
            "id": "team_id",
            "displayName": "display_name",
            "shortDisplayName": "short_name",
        }
    )
    return df.drop_duplicates(["season", "team_id"]).reset_index(drop=True)


def load_cbbd_games(data_dir: Path) -> pd.DataFrame:
    """Team names and ids per CBBD game, de-duplicated on game id (fetch windows overlap)."""
    games: dict[int, dict] = {}
    for path in sorted((data_dir / "cbbd" / "raw" / "games").glob("*.json.gz")):
        for g in _read_wrapped_json(path)["data"]:
            games[g["id"]] = {
                "game_id": g["id"],
                "season": g["season"],
                "home_team_id": g["homeTeamId"],
                "home_team": g["homeTeam"],
                "away_team_id": g["awayTeamId"],
                "away_team": g["awayTeam"],
            }
    return pd.DataFrame(list(games.values()))


TORVIK_FILE = re.compile(r"^(\d{4})_(team_results|super_sked)_.*\.csv\.gz$")


def _torvik_rows(path: Path) -> list[list[str]]:
    raw = gzip.decompress(path.read_bytes())
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return list(csv.reader(io.StringIO(text)))


def load_torvik_names(data_dir: Path) -> pd.DataFrame:
    """Distinct team names per season and file kind from every saved Torvik file.

    team_results: the name is column 2 (index 1). Some copies start with a header row
    whose name cell is literally "team"; that row is skipped.
    super_sked: names are `team1` (index 8) and `team2` (index 14). This file also lists
    non-D-I opponents.
    """
    seen: set[tuple[int, str, str]] = set()
    for path in sorted((data_dir / "torvik" / "raw").rglob("*.csv.gz")):
        match = TORVIK_FILE.match(path.name)
        if not match:
            continue
        season, kind = int(match.group(1)), match.group(2)
        for row in _torvik_rows(path):
            if kind == "team_results":
                if len(row) > 1 and row[1] and row[1] != "team":
                    seen.add((season, kind, row[1]))
            else:
                for idx in (8, 14):
                    if len(row) > idx and row[idx]:
                        seen.add((season, kind, row[idx]))
    return pd.DataFrame(sorted(seen), columns=["season", "file_kind", "name"])


def load_odds_names(data_dir: Path) -> pd.DataFrame:
    """Distinct (season, team name) pairs from every Odds API snapshot."""
    seen: set[tuple[int, str]] = set()
    for path in sorted((data_dir / "odds" / "raw").rglob("*.json.gz")):
        for game in _read_wrapped_json(path).get("data") or []:
            when = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
            season = season_of(when)
            for key in ("home_team", "away_team"):
                if game.get(key):
                    seen.add((season, game[key]))
    return pd.DataFrame(sorted(seen), columns=["season", "name"])
