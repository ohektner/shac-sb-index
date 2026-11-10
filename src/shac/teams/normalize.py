"""Small, pure helpers: name normalization and season ranges.

Nothing here reads files, so it is easy to test.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime


def normalize_name(name: str) -> str:
    """Turn a team name into a comparison key.

    Lower-case, accents removed ("San José" -> "san jose"), periods and apostrophes
    dropped ("St. John's" -> "st johns"), hyphens, brackets and commas turned into spaces
    ("Miami (OH)" -> "miami oh"), and repeated spaces squeezed. "&" is kept, so
    "Texas A&M" stays "texas a&m".

    This only makes spelling differences disappear. It does NOT decide that "St." means
    "State" or "Saint"; that is done by `name_variants`, on our own (CBBD) names only.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[.'’`]", "", text)
    text = re.sub(r"[-()/,]", " ", text)
    return " ".join(text.split())


def name_variants(name: str) -> set[str]:
    """Normalized spellings of one of OUR names that other sources commonly use.

    "Kansas State" -> {"kansas state", "kansas st"}; "Saint Mary's" -> {"saint marys",
    "st marys"}. Variants are made from CBBD's names, never guessed from a source's
    name, so "St." in a source name is only matched if one of our names expands to it.
    """
    out = {normalize_name(name)}
    swaps = [(r"\bState\b", "St"), (r"\bSaint\b", "St")]
    pending = [name]
    for pattern, repl in swaps:
        pending += [re.sub(pattern, repl, n) for n in pending]
    out |= {normalize_name(n) for n in pending}
    return out


def season_of(when: datetime) -> int:
    """Season for a date: July 2026 through June 2027 is season 2027."""
    return when.year + 1 if when.month >= 7 else when.year


def contiguous_runs(seasons: Iterable[int]) -> list[tuple[int, int]]:
    """Group seasons into runs of consecutive years.

    [2019, 2020, 2022] -> [(2019, 2020), (2022, 2022)].
    """
    runs: list[tuple[int, int]] = []
    for season in sorted(set(seasons)):
        if runs and season == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], season)
        else:
            runs.append((season, season))
    return runs


def ranges_overlap(a_from: int, a_to: int | None, b_from: int, b_to: int | None) -> bool:
    """True if two inclusive season ranges share a season. `None` as an end means 'still in use'."""
    a_end = a_to if a_to is not None else 10_000
    b_end = b_to if b_to is not None else 10_000
    return a_from <= b_end and b_from <= a_end
