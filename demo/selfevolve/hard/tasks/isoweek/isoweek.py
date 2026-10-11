"""ISO 8601 week dates, by hand."""


def iso_week(year: int, month: int, day: int) -> tuple:
    """The ISO 8601 week date of a Gregorian date: (ISO year, week 1-53, weekday 1-7 with Monday 1).

    Week 1 is the week with the year's first Thursday in it; weeks start on Monday. So 29-31 December may be in
    week 1 of the next ISO year, and 1-3 January in week 52 or 53 of the previous one. A date that does not exist
    (2026-02-29, month 13, day 0) raises ValueError. The ``datetime``, ``calendar`` and ``time`` modules are
    not to be used.
    """
    raise NotImplementedError


def from_iso(iso_year: int, week: int, weekday: int) -> tuple:
    """The Gregorian (year, month, day) of an ISO week date; a week the ISO year does not have (week 53 of a
    52-week year), week 0, or a weekday outside 1-7 raises ValueError."""
    raise NotImplementedError
