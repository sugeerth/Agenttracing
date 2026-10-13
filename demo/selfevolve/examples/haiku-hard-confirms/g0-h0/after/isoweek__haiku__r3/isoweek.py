"""ISO 8601 week dates, by hand."""


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 (proleptic Gregorian), Howard Hinnant's algorithm."""
    if month <= 2:
        year -= 1
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(days: int) -> tuple:
    """Inverse of _days_from_civil: (year, month, day)."""
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return year, month, day


def _weekday(days: int) -> int:
    """ISO weekday 1-7 (Monday is 1). 1970-01-01 was a Thursday."""
    return (days + 3) % 7 + 1


def _jan1(year: int) -> int:
    return _days_from_civil(year, 1, 1)


def iso_week(year: int, month: int, day: int) -> tuple:
    """The ISO 8601 week date of a Gregorian date: (ISO year, week 1-53, weekday 1-7 with Monday 1).

    Week 1 is the week with the year's first Thursday in it; weeks start on Monday. So 29-31 December may be in
    week 1 of the next ISO year, and 1-3 January in week 52 or 53 of the previous one. A date that does not exist
    (2026-02-29, month 13, day 0) raises ValueError. The ``datetime``, ``calendar`` and ``time`` modules are
    not to be used.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, not {month}")
    days_in_month = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not 1 <= day <= days_in_month[month - 1]:
        raise ValueError(f"day {day} does not exist in {year:04d}-{month:02d}")

    days = _days_from_civil(year, month, day)
    weekday = _weekday(days)
    # The Thursday of this ISO week decides which year the week belongs to, and its position in that year.
    thursday = days - weekday + 4
    if thursday < _jan1(year):
        iso_year = year - 1
    elif thursday >= _jan1(year + 1):
        iso_year = year + 1
    else:
        iso_year = year
    week = (thursday - _jan1(iso_year)) // 7 + 1
    return iso_year, week, weekday


def from_iso(iso_year: int, week: int, weekday: int) -> tuple:
    """The Gregorian (year, month, day) of an ISO week date; a week the ISO year does not have (week 53 of a
    52-week year), week 0, or a weekday outside 1-7 raises ValueError."""
    if not 1 <= weekday <= 7:
        raise ValueError(f"weekday must be 1-7, not {weekday}")
    if week < 1:
        raise ValueError(f"week must be at least 1, not {week}")
    # 28 December always falls in the last ISO week of its year, so its week number is the year's week count.
    if week > iso_week(iso_year, 12, 28)[1]:
        raise ValueError(f"ISO year {iso_year} has no week {week}")

    # Week 1 is the week containing 4 January.
    jan4 = _days_from_civil(iso_year, 1, 4)
    week1_monday = jan4 - (_weekday(jan4) - 1)
    days = week1_monday + (week - 1) * 7 + (weekday - 1)
    return _civil_from_days(days)
