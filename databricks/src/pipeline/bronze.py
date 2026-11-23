"""Bronze: raw files from the landing volume, as they arrived (Auto Loader, incremental).

Each table keeps the file path, so silver can de-duplicate by fetch time and file name
the same way `shac.tables.raw.dedupe_files` does. Plus the two public team-map CSVs,
re-read in full on every update (they are rewritten in place).

Landing layout (mirrors the private data repo; docs/DATABRICKS.md):
    raw/cbbd/raw/{games,lines}/*.json.gz
    raw/torvik/timemachine/<season>/YYYYMMDD_team_results.json.gz (+ manifest.csv)
    raw/torvik/raw/YYYY/MM/DD/<season>_team_results_<stamp>.csv.gz
    reference/{teams,team_aliases}.csv
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING = spark.conf.get("landing_root")  # noqa: F821 (provided by the pipeline runtime)

GAMES_SCHEMA = (
    "fetched_at STRING, data ARRAY<STRUCT<id BIGINT, season INT, seasonType STRING, "
    "startDate STRING, startTimeTbd BOOLEAN, homeTeamId BIGINT, awayTeamId BIGINT, "
    "neutralSite BOOLEAN, conferenceGame BOOLEAN, status STRING, homePoints INT, "
    "awayPoints INT, homePeriodPoints ARRAY<INT>, awayPeriodPoints ARRAY<INT>>>"
)
LINES_SCHEMA = (
    "fetched_at STRING, data ARRAY<STRUCT<gameId BIGINT, homeTeamId BIGINT, "
    "awayTeamId BIGINT, lines ARRAY<STRUCT<provider STRING, spread DOUBLE, "
    "spreadOpen DOUBLE, overUnder DOUBLE, overUnderOpen DOUBLE, homeMoneyline BIGINT, "
    "awayMoneyline BIGINT>>>>"
)
# Torvik team_results: 45 positional fields, no header (names in shac.tables.torvik).
TORVIK_CSV_SCHEMA = ", ".join(f"_c{i} STRING" for i in range(45))


def _auto_loader(fmt: str, **options: str):
    reader = (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", fmt)
        .option("recursiveFileLookup", "true")
    )
    for key, value in options.items():
        reader = reader.option(key, value)
    return reader


def _with_file(df):
    return df.select(
        "*",
        F.col("_metadata.file_path").alias("source_file"),
        F.col("_metadata.file_name").alias("file_name"),
    )


@dp.table(comment="CBBD /games responses, one row per raw file (wrapper + data array).")
def bronze_cbbd_games():
    df = _auto_loader("json", multiLine="true")
    return _with_file(df.schema(GAMES_SCHEMA).load(f"{LANDING}/raw/cbbd/raw/games"))


@dp.table(comment="CBBD /lines responses, one row per raw file (wrapper + data array).")
def bronze_cbbd_lines():
    df = _auto_loader("json", multiLine="true")
    return _with_file(df.schema(LINES_SCHEMA).load(f"{LANDING}/raw/cbbd/raw/lines"))


@dp.table(comment="Torvik Time Machine files, one row per file (the JSON text, unparsed).")
def bronze_torvik_time_machine():
    df = _auto_loader(
        "text",
        wholetext="true",
        pathGlobFilter="*_team_results.json.gz",
    )
    return _with_file(df.load(f"{LANDING}/raw/torvik/timemachine"))


@dp.table(comment="Our daily Torvik team_results pulls, one row per CSV line, as text.")
def bronze_torvik_daily():
    df = _auto_loader(
        "csv",
        header="false",
        pathGlobFilter="*_team_results_*.csv.gz",
    )
    return _with_file(df.schema(TORVIK_CSV_SCHEMA).load(f"{LANDING}/raw/torvik/raw"))


@dp.materialized_view(comment="Time Machine fetch log (manifest.csv), re-read each update.")
def bronze_torvik_manifest():
    return (
        spark.read.option("header", "true")  # noqa: F821
        .csv(f"{LANDING}/raw/torvik/timemachine/manifest.csv")
        .select("file", "status", "fetched_at_utc")
    )


@dp.materialized_view(comment="Public team list (data/teams.csv), re-read each update.")
def ref_teams():
    return (
        spark.read.option("header", "true")  # noqa: F821
        .schema("team_id BIGINT, name STRING, conference STRING, first_season INT, last_season INT")
        .csv(f"{LANDING}/reference/teams.csv")
    )


@dp.materialized_view(comment="Public team-name aliases (data/team_aliases.csv).")
def ref_team_aliases():
    return (
        spark.read.option("header", "true")  # noqa: F821
        .option("nullValue", "")
        .schema(
            "source STRING, alias STRING, valid_from_season INT, valid_to_season INT, "
            "team_id BIGINT"
        )
        .csv(f"{LANDING}/reference/team_aliases.csv")
    )
