"""When a cron schedule fires next."""

import re
from datetime import date, datetime, time, timedelta

_MONTHS = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_WEEKDAYS = {name: i for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# Gregorian calendar repeats every 400 years, so a schedule with no match in that span never matches.
_SEARCH_DAYS = 400 * 365 + 97

_NUMBER = re.compile(r"\d+")


def _value(text: str, lo: int, hi: int, names: dict) -> int:
    if text.upper() in names:
        number = names[text.upper()]
    elif _NUMBER.fullmatch(text):
        number = int(text)
    else:
        raise ValueError(f"invalid cron value: {text!r}")
    if not lo <= number <= hi:
        raise ValueError(f"cron value {number} out of range {lo}-{hi}")
    return number


def _field(text: str, lo: int, hi: int, names: dict | None = None) -> set[int]:
    """Expand one cron field into the set of values it matches, validating against ``lo``..``hi``."""
    names = names or {}
    values: set[int] = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        if slash:
            if not _NUMBER.fullmatch(step_text) or int(step_text) < 1:
                raise ValueError(f"invalid cron step: {part!r}")
            step = int(step_text)
        else:
            step = 1

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            first, _, last = base.partition("-")
            start = _value(first, lo, hi, names)
            end = _value(last, lo, hi, names)
            if start > end:
                raise ValueError(f"cron range runs backwards: {base!r}")
        elif slash:
            # The grammar only allows a step after ``*`` or a range.
            raise ValueError(f"invalid cron step: {part!r}")
        else:
            start = end = _value(base, lo, hi, names)

        values.update(range(start, end + 1, step))
    return values


def next_run(expr: str, after: datetime) -> datetime:
    """The first minute strictly after ``after`` (seconds and microseconds dropped first) that ``expr`` matches.

    ``expr`` has five fields separated by single spaces: minute (0-59), hour (0-23), day of month (1-31),
    month (1-12), day of week (0-6, Sunday is 0; 7 is also Sunday). Each field is ``*``, a number, a range
    ``a-b``, a step ``*/n`` or ``a-b/n``, or a comma list of those. Months may be written JAN-DEC and days of
    week SUN-SAT, in any case, also inside ranges (``MON-FRI``).

    When both day of month and day of week are restricted (neither is ``*``), a day matches when EITHER does,
    as in Vixie cron: ``0 0 13 * FRI`` fires on every 13th and on every Friday. When one of them is ``*``, only
    the other one counts. A malformed expression, or a value out of its field's range, raises ValueError.
    Times are naive: no time zones, no daylight saving.
    """
    fields = expr.split(" ")
    if len(fields) != 5 or any(not f for f in fields):
        raise ValueError(f"cron expression needs five single-space-separated fields: {expr!r}")
    minute_f, hour_f, dom_f, month_f, dow_f = fields

    minutes = _field(minute_f, 0, 59)
    hours = _field(hour_f, 0, 23)
    doms = _field(dom_f, 1, 31)
    months = _field(month_f, 1, 12, _MONTHS)
    # 7 is an alias for Sunday (0).
    dows = {d % 7 for d in _field(dow_f, 0, 7, _WEEKDAYS)}

    dom_restricted = dom_f != "*"
    dow_restricted = dow_f != "*"

    def day_matches(day: date) -> bool:
        dom_ok = day.day in doms
        dow_ok = day.isoweekday() % 7 in dows
        if dom_restricted and dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    first_day = start.date()
    sorted_hours = sorted(hours)
    sorted_minutes = sorted(minutes)

    day = first_day
    for _ in range(_SEARCH_DAYS):
        if day.month in months and day_matches(day):
            for hour in sorted_hours:
                if day == first_day and hour < start.hour:
                    continue
                for minute in sorted_minutes:
                    if day == first_day and hour == start.hour and minute < start.minute:
                        continue
                    return datetime.combine(day, time(hour, minute), tzinfo=start.tzinfo)
        day += timedelta(days=1)

    raise ValueError(f"cron expression never matches: {expr!r}")
