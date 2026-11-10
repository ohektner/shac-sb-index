"""Build the private data tables (games, results, player_box, lines, odds) from raw files.

Everything here reads the raw files in the private data repo and writes parquet tables
back into that repo's `tables/` folder. Nothing here makes a network call.
"""
