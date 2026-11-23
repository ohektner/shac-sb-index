"""Upload new raw files to the Databricks landing volume (docs/DATABRICKS.md, D3).

Run by the `databricks-sync` workflow (after each CBBD ingest) and by Owen's PC task
(after each Torvik pull). Files are named by fetch time and never change, so only files
the volume doesn't have yet are sent. The team map is re-sent every time. A new file in
`raw/` starts the nightly job (file-arrival trigger).

    python databricks/scripts/sync_raw.py --data-dir <private repo> --what cbbd reference
    python databricks/scripts/sync_raw.py --data-dir <private repo> --what torvik

Needs the Databricks CLI, signed in (DATABRICKS_HOST/DATABRICKS_TOKEN in Actions, the
CLI profile on the PC). Standard library only. Prints counts, never file contents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VOLUME = "/Volumes/workspace/shac/landing"
DAILY = re.compile(r"^\d{4}_team_results_\d{8}T\d{6}Z\.csv\.gz$")


def cli() -> str:
    found = os.environ.get("DATABRICKS_CLI") or shutil.which("databricks")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    for exe in winget.glob("Databricks.DatabricksCLI_*/databricks.exe"):
        return str(exe)
    sys.exit("Databricks CLI not found (set DATABRICKS_CLI)")


def run(*args: str) -> str:
    done = subprocess.run([cli(), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"databricks {' '.join(args[:2])} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def remote_names(remote_dir: str) -> set[str] | None:
    """File names already in a volume folder, or None if the folder doesn't exist."""
    try:
        out = run("fs", "ls", f"dbfs:{remote_dir}", "--output", "json")
    except RuntimeError as err:
        if "no such directory" in str(err).lower() or "not found" in str(err).lower():
            return None
        raise
    entries = json.loads(out or "[]")
    return {e["name"] for e in entries if not e.get("is_directory")}


def planned(data_dir: Path, map_dir: Path, what: set[str]) -> list[tuple[Path, str, bool]]:
    """(local file, remote path, always send) for everything in scope."""
    files: list[tuple[Path, str, bool]] = []
    if "cbbd" in what:
        for slug in ("games", "lines"):
            for p in sorted((data_dir / "cbbd" / "raw" / slug).glob("*.json.gz")):
                files.append((p, f"{VOLUME}/raw/cbbd/raw/{slug}/{p.name}", False))
    if "torvik" in what:
        tm = data_dir / "torvik" / "timemachine"
        for p in sorted(tm.glob("*/*_team_results.json.gz")):
            files.append((p, f"{VOLUME}/raw/torvik/timemachine/{p.parent.name}/{p.name}", False))
        raw = data_dir / "torvik" / "raw"
        for p in sorted(raw.rglob("*_team_results_*.csv.gz")):
            rel = p.relative_to(raw)
            if rel.parts[0] == "seasons" or not DAILY.match(p.name):
                continue
            files.append((p, f"{VOLUME}/raw/torvik/raw/{rel.as_posix()}", False))
    if "reference" in what:
        for name in ("teams.csv", "team_aliases.csv"):
            files.append((map_dir / name, f"{VOLUME}/reference/{name}", True))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="private data repo")
    parser.add_argument("--map-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--what", nargs="+", choices=["cbbd", "torvik", "reference"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    by_dir: dict[str, list[tuple[Path, str, bool]]] = defaultdict(list)
    for item in planned(args.data_dir, args.map_dir, set(args.what)):
        by_dir[item[1].rsplit("/", 1)[0]].append(item)

    sent = skipped = tm_new = 0
    for remote_dir, items in sorted(by_dir.items()):
        have = remote_names(remote_dir)
        todo = [(p, r) for p, r, always in items if always or have is None or p.name not in have]
        skipped += len(items) - len(todo)
        if "/timemachine/" in remote_dir:
            tm_new += len(todo)
        if not todo:
            continue
        if args.dry_run:
            sent += len(todo)
            continue
        if have is None:
            run("fs", "mkdir", f"dbfs:{remote_dir}")
        for local, remote in todo:
            run("fs", "cp", "--overwrite", str(local), f"dbfs:{remote}")
            sent += 1
    # The Time Machine manifest (fetch times) changes only when archive files are added.
    manifest = args.data_dir / "torvik" / "timemachine" / "manifest.csv"
    if tm_new and manifest.exists() and not args.dry_run:
        run(
            "fs",
            "cp",
            "--overwrite",
            str(manifest),
            f"dbfs:{VOLUME}/raw/torvik/timemachine/manifest.csv",
        )
        sent += 1
    verb = "would send" if args.dry_run else "sent"
    print(f"sync {' '.join(args.what)}: {verb} {sent} file(s), {skipped} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
