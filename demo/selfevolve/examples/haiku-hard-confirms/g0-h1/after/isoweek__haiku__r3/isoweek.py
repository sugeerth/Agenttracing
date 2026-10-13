"""ISO 8601 week dates, by hand."""


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if _is_leap(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _check_date(year: int, month: int, day: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    if not 1 <= day <= _days_in_month(year, month):
        raise ValueError(f"day out of range for {year}-{month:02d}: {day}")


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 of a Gregorian date (proleptic; negative before the epoch)."""
    if month <= 2:
        year -= 1
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(days: int) -> tuple:
    """Inverse of _days_from_civil: the Gregorian (year, month, day) of a day number."""
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


def iso_week(year: int, month: int, day: int) -> tuple:
    """The ISO 8601 week date of a Gregorian date: (ISO year, week 1-53, weekday 1-7 with Monday 1).

    Week 1 is the week with the year's first Thursday in it; weeks start on Monday. So 29-31 December may be in
    week 1 of the next ISO year, and 1-3 January in week 52 or 53 of the previous one. A date that does not exist
    (2026-02-29, month 13, day 0) raises ValueError. The ``datetime``, ``calendar`` and ``time`` modules are
    not to be used.
    """
    _check_date(year, month, day)
    days = _days_from_civil(year, month, day)
    weekday = _weekday(days)
    # The week's Thursday decides which Gregorian year the ISO week belongs to.
    thursday = days + (4 - weekday)
    iso_year, _, _ = _civil_from_days(thursday)
    week = (thursday - _days_from_civil(iso_year, 1, 1)) // 7 + 1
    return iso_year, week, weekday


def from_iso(iso_year: int, week: int, weekday: int) -> tuple:
    """The Gregorian (year, month, day) of an ISO week date; a week the ISO year does not have (week 53 of a
    52-week year), week 0, or a weekday outside 1-7 raises ValueError."""
    if not 1 <= weekday <= 7:
        raise ValueError(f"weekday out of range 1-7: {weekday}")
    if not 1 <= week <= 53:
        raise ValueError(f"week out of range 1-53: {week}")
    # Monday of week 1 is the Monday on or before 4 January, which is always in week 1.
    jan4 = _days_from_civil(iso_year, 1, 4)
    week1_monday = jan4 - (_weekday(jan4) - 1)
    target = week1_monday + (week - 1) * 7 + (weekday - 1)
    if iso_week(*_civil_from_days(target)) != (iso_year, week, weekday):
        raise ValueError(f"ISO year {iso_year} has no week {week}")
    return _civil_from_days(target)
