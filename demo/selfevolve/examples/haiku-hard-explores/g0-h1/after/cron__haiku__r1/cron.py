"""When a cron schedule fires next."""

import re
from datetime import datetime, timedelta

_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
    )
}
_DAY_NAMES = {name: number for number, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (low, high, names) for minute, hour, day of month, month, day of week
_FIELD_SPECS = (
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, _MONTH_NAMES),
    (0, 7, _DAY_NAMES),
)


def _number(token: str, low: int, high: int, names: dict) -> int:
    if token.upper() in names:
        return names[token.upper()]
    if not re.fullmatch(r"[0-9]+", token):
        raise ValueError(f"bad value {token!r}")
    value = int(token)
    if not low <= value <= high:
        raise ValueError(f"value {value} out of range {low}-{high}")
    return value


def _parse_field(text: str, low: int, high: int, names: dict) -> tuple[set[int], bool]:
    """Return the set of values a field matches, and whether the field is exactly ``*``."""
    if text == "*":
        return set(range(low, high + 1)), True
    values = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not re.fullmatch(r"[0-9]+", step_text) or int(step_text) < 1:
                raise ValueError(f"bad step {step_text!r} in {part!r}")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start = _number(first, low, high, names)
            end = _number(last, low, high, names)
            if start > end:
                raise ValueError(f"range {base!r} is backwards")
        elif slash:
            raise ValueError(f"step must follow * or a range: {part!r}")
        else:
            start = end = _number(base, low, high, names)
        values.update(range(start, end + 1, step))
    return values, False


def _day_matches(day: datetime, doms: set[int], dom_star: bool, dows: set[int], dow_star: bool) -> bool:
    dom_ok = day.day in doms
    dow_ok = day.isoweekday() % 7 in dows  # ISO Sunday is 7; cron Sunday is 0
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok


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
    parts = expr.split(" ")
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields, got {len(parts)}: {expr!r}")
    fields = [_parse_field(part, low, high, names) for part, (low, high, names) in zip(parts, _FIELD_SPECS)]
    (minutes, _), (hours, _), (doms, dom_star), (months, _), (dows, dow_star) = fields
    dows = {day % 7 for day in dows}

    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    last_year = after.year + 400  # the Gregorian calendar repeats every 400 years
    try:
        while t.year <= last_year:
            if t.month not in months:
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            elif not _day_matches(t, doms, dom_star, dows, dow_star):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            elif t.hour not in hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in minutes:
                t += timedelta(minutes=1)
            else:
                return t
    except OverflowError:
        pass
    raise ValueError(f"no matching time for {expr!r} after {after}")
