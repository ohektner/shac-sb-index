"""The team-name map: one `team_id` per D-I team, and every source's name for it.

- `normalize.py`: turning names into comparable keys, and season-range helpers.
- `sources.py`: reading the raw CBBD, Torvik and Odds API files from the private repo.
- `matching.py`: deciding which team a name means (hand table first, then exact keys).
- `build.py`: writes `data/teams.csv`, `data/team_aliases.csv`, `data/team_map_review.csv`.
- `check.py`: runs `sql/checks/team_map.sql` and prints how many problem rows each check finds.

Hand-curated mappings live next to this file in `manual_aliases.csv` and `manual_teams.csv`.
"""
