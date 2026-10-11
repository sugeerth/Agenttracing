"""ISO 8601 week dates, by hand."""


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if _is_leap(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _check_date(year: int, month: int, day: int) -> None:
    if year < 1:
        raise ValueError(f"year out of range: {year}")
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    if not 1 <= day <= _days_in_month(year, month):
        raise ValueError(f"day out of range for {year}-{month:02d}: {day}")


def _ordinal(year: int, month: int, day: int) -> int:
    """Day number of a Gregorian date, counting 0001-01-01 as day 1."""
    y = year - 1
    days = y * 365 + y // 4 - y // 100 + y // 400
    days += sum(_days_in_month(year, m) for m in range(1, month))
    return days + day


def _weekday(ordinal: int) -> int:
    """ISO weekday (Monday 1 to Sunday 7). Day 1, 0001-01-01, is a Monday."""
    return (ordinal - 1) % 7 + 1


def _from_ordinal(ordinal: int) -> tuple:
    year = (ordinal - 1) // 366 + 1
    while _ordinal(year + 1, 1, 1) <= ordinal:
        year += 1
    month = 1
    while month < 12 and _ordinal(year, month + 1, 1) <= ordinal:
        month += 1
    return year, month, ordinal - _ordinal(year, month, 1) + 1


def iso_week(year: int, month: int, day: int) -> tuple:
    """The ISO 8601 week date of a Gregorian date: (ISO year, week 1-53, weekday 1-7 with Monday 1).

    Week 1 is the week with the year's first Thursday in it; weeks start on Monday. So 29-31 December may be in
    week 1 of the next ISO year, and 1-3 January in week 52 or 53 of the previous one. A date that does not exist
    (2026-02-29, month 13, day 0) raises ValueError. The ``datetime``, ``calendar`` and ``time`` modules are
    not to be used.
    """
    _check_date(year, month, day)
    n = _ordinal(year, month, day)
    weekday = _weekday(n)
    # The Thursday of this date's ISO week lies in the same ISO year as the week itself.
    thursday = n - weekday + 4
    iso_year, _, _ = _from_ordinal(thursday)
    week = (thursday - _ordinal(iso_year, 1, 1)) // 7 + 1
    return iso_year, week, weekday


def from_iso(iso_year: int, week: int, weekday: int) -> tuple:
    """The Gregorian (year, month, day) of an ISO week date; a week the ISO year does not have (week 53 of a
    52-week year), week 0, or a weekday outside 1-7 raises ValueError."""
    if iso_year < 1:
        raise ValueError(f"ISO year out of range: {iso_year}")
    if not 1 <= weekday <= 7:
        raise ValueError(f"weekday out of range: {weekday}")
    # The last week of an ISO year contains its 28 December, which is always in the last week.
    last_week = iso_week(iso_year, 12, 28)[1]
    if not 1 <= week <= last_week:
        raise ValueError(f"week {week} does not exist in ISO year {iso_year}")
    # 4 January is always in week 1, so week 1 starts on the Monday on or before it.
    jan4 = _ordinal(iso_year, 1, 4)
    week1_monday = jan4 - (_weekday(jan4) - 1)
    return _from_ordinal(week1_monday + (week - 1) * 7 + (weekday - 1))
