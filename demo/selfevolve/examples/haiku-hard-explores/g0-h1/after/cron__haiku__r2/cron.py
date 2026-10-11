"""When a cron schedule fires next."""

import re
from datetime import datetime, timedelta

_MONTH_NAMES = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_DOW_NAMES = {name: i for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (lowest, highest, names) for minute, hour, day of month, month, day of week
_FIELDS = [(0, 59, {}), (0, 23, {}), (1, 31, {}), (1, 12, _MONTH_NAMES), (0, 7, _DOW_NAMES)]

# The Gregorian calendar repeats every 400 years, so a search longer than that never finds a match.
_SEARCH_DAYS = 400 * 366


def _parse_value(text: str, lo: int, hi: int, names: dict) -> int:
    if text.upper() in names:
        return names[text.upper()]
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"bad value {text!r}")
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError(f"{value} is out of range {lo}-{hi}")
    return value


def _parse_field(text: str, lo: int, hi: int, names: dict) -> set[int]:
    values = set()
    for item in text.split(","):
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not re.fullmatch(r"[0-9]+", step_text) or int(step_text) == 0:
                raise ValueError(f"bad step in {item!r}")
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
        else:
            start_text, dash, end_text = base.partition("-")
            start = _parse_value(start_text, lo, hi, names)
            if dash:
                end = _parse_value(end_text, lo, hi, names)
            elif slash:
                raise ValueError(f"step {item!r} needs * or a range")
            else:
                end = start
            if start > end:
                raise ValueError(f"range {base!r} runs backwards")
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
    if len(fields) != 5 or not all(fields):
        raise ValueError(f"expected five single-space-separated fields, got {expr!r}")
    minutes, hours, doms, months, dows = (
        _parse_field(text, lo, hi, names) for text, (lo, hi, names) in zip(fields, _FIELDS))
    dows = {d % 7 for d in dows}  # 7 is Sunday too
    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"

    def day_matches(day: datetime) -> bool:
        dom_ok = day.day in doms
        dow_ok = day.isoweekday() % 7 in dows  # Monday=1 ... Sunday=0
        if dom_restricted and dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + timedelta(days=_SEARCH_DAYS)
    while t < limit:
        if t.month not in months:
            year, month = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
            t = t.replace(year=year, month=month, day=1, hour=0, minute=0)
        elif not day_matches(t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
        elif t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
        elif t.minute not in minutes:
            t += timedelta(minutes=1)
        else:
            return t
    raise ValueError(f"{expr!r} never fires")
