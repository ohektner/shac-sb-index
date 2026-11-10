"""Date windows for paging through a season, and the per-endpoint row caps.

CBBD returns at most a fixed number of records per call, so a season has to be
fetched in slices of dates. Windows are half-open: [start, end). The end of one
window is the start of the next, so there are no gaps. (The API's end date is
"at or before", so a game exactly on a boundary may appear twice. Duplicates are
removed later, when the raw files are parsed into tables.)

Season S is the ending year: season 2026 is 2025-26. We cover Nov 1 of S-1
through Apr 15 of S. The end is Apr 16 at 00:00 UTC, because the window is
half-open and Apr 15 should be included.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Most records one call returns, per endpoint. CHANGE THESE here if the real data shows a
# different number. The unit of the player cap is unknown: the docs say "1,000 games", but
# we count the rows in the response, which for player box scores might be players, not games.
# If real responses come back with far more than 1,000 rows and no truncation, raise it;
# if they stop at a round number below it, lower it.
CAP_ROWS: dict[str, int] = {
    "games": 3000,
    "lines": 3000,
    "games/teams": 3000,
    "games/players": 1000,
}

# How wide the first-try windows are for each endpoint that is paged by date.
MONTHLY_ENDPOINTS = ("games", "lines", "games/teams")
PLAYER_BLOCK_DAYS = 10

ONE_DAY = timedelta(days=1)

Window = tuple[datetime, datetime]


def season_bounds(season: int) -> Window:
    """Return (start, end) for a season: Nov 1 of the year before, to Apr 16 of the season year."""
    start = datetime(season - 1, 11, 1, tzinfo=UTC)
    end = datetime(season, 4, 16, tzinfo=UTC)
    return start, end


def month_windows(season: int) -> list[Window]:
    """Split a season into calendar-month windows (the last one is clipped to the season end)."""
    start, season_end = season_bounds(season)
    windows: list[Window] = []
    current = start
    while current < season_end:
        if current.month == 12:
            next_month = datetime(current.year + 1, 1, 1, tzinfo=UTC)
        else:
            next_month = datetime(current.year, current.month + 1, 1, tzinfo=UTC)
        windows.append((current, min(next_month, season_end)))
        current = next_month
    return windows


def block_windows(season: int, days: int = PLAYER_BLOCK_DAYS) -> list[Window]:
    """Split a season into blocks of `days` days (the last one is clipped to the season end)."""
    start, season_end = season_bounds(season)
    windows: list[Window] = []
    current = start
    while current < season_end:
        windows.append((current, min(current + timedelta(days=days), season_end)))
        current += timedelta(days=days)
    return windows


def windows_for(endpoint: str, season: int) -> list[Window]:
    """Return the first-try windows for an endpoint in a season."""
    if endpoint == "games/players":
        return block_windows(season)
    return month_windows(season)


def split_window(window: Window) -> tuple[Window, Window]:
    """Cut a window of 2+ days into two halves that meet exactly in the middle."""
    start, end = window
    days = (end - start).days
    if days < 2:
        raise ValueError("A window of one day cannot be split.")
    middle = start + timedelta(days=days // 2)
    return (start, middle), (middle, end)


def is_single_day(window: Window) -> bool:
    """True when a window is one day or shorter, so it cannot be split any further."""
    return window[1] - window[0] <= ONE_DAY
