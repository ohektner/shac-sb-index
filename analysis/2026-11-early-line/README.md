# Early-line and absence tests, November 2026

These are the scripts behind tests 2 and 3 in `docs/FINDINGS.md`. Each one's method and reading was committed before it ran:
- `docs/EARLY_LINE.md` sections 3 and 4;
- `docs/PLAYER_IMPACT.md` section 9;
- the dated entries in `docs/DECISIONS.md`.

The scripts read the private tables and write summary CSVs. The tables in `results/` contain aggregates only.

Run from the repo root with `uv run python <script> <args>`, in this order:

| Script | Args | What it does |
| --- | --- | --- |
| `early_line_read.py` | `<private repo> <out dir>` | The first read: captured 2026-27 games at the first line we saw (EARLY_LINE.md section 3) |
| `controls.py` | `<out dir> <private repo>` | Added after the first read: simple side rules and a split by book count |
| `season_check.py` | `<private repo>` | Added after the first read: a rough full-season check on CBBD's untimed opens |
| `coverage_diagnosis.py` | `<private repo>` | Why some games have no night-before line (lines only) |
| `archive_backtest.py` | `<private repo> <out dir>` | Test 2: checks 1 and 2, then CLV, ATS, controls, the curve and tiers. It stops if check 2 fails; check 1's failure goes ahead under Owen's recorded decision |
| `g2_ceiling.py` | `<private repo> <out dir>` | Test 3: the perfect-knowledge absence ceiling. It needs `private_archive_games.parquet` from `archive_backtest.py` |

`results/first_read/` holds the first read's tables; `results/archive/` holds tests 2 and 3. Files named `private_*` hold per-game lines and are never committed.
