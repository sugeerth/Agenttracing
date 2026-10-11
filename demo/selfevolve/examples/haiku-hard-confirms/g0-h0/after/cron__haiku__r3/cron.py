"""When a cron schedule fires next."""

from datetime import datetime, timedelta

_MONTHS = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_DAYS = {name: i for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (low, high, names) for minute, hour, day of month, month, day of week
_FIELDS = [
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, _MONTHS),
    (0, 7, _DAYS),
]

# The Gregorian calendar repeats every 400 years, so no satisfiable schedule has a gap longer than this.
_MAX_DAYS = 146097


def _number(token: str, low: int, high: int, names: dict) -> int:
    if token.upper() in names:
        return names[token.upper()]
    if not (token.isascii() and token.isdigit()):
        raise ValueError(f"invalid cron value {token!r}")
    value = int(token)
    if not low <= value <= high:
        raise ValueError(f"cron value {value} outside {low}-{high}")
    return value


def _parse_field(text: str, low: int, high: int, names: dict) -> set[int]:
    values = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not (step_text.isascii() and step_text.isdigit()) or int(step_text) < 1:
                raise ValueError(f"invalid cron step {step_text!r}")
            step = int(step_text)
        if base == "*":
            first, last = low, high
        elif "-" in base:
            start_text, dash, end_text = base.partition("-")
            if not dash:
                raise ValueError(f"invalid cron range {base!r}")
            first = _number(start_text, low, high, names)
            last = _number(end_text, low, high, names)
            if first > last:
                raise ValueError(f"cron range {base!r} runs backwards")
        elif slash:
            raise ValueError(f"step needs * or a range, not {base!r}")
        else:
            first = last = _number(base, low, high, names)
        values.update(range(first, last + 1, step))
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
        raise ValueError(f"cron expression must have five single-space-separated fields: {expr!r}")

    minutes, hours, doms, months, dows = (
        _parse_field(text, low, high, names) for text, (low, high, names) in zip(fields, _FIELDS))
    dows = {day % 7 for day in dows}

    # Vixie cron treats a field starting with '*' as unrestricted, so "*/2" counts as '*' here.
    dom_restricted = not fields[2].startswith("*")
    dow_restricted = not fields[4].startswith("*")

    minutes, hours = sorted(minutes), sorted(hours)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(_MAX_DAYS):
        dom_ok = day.day in doms
        dow_ok = (day.weekday() + 1) % 7 in dows
        day_ok = (dom_ok or dow_ok) if dom_restricted and dow_restricted else (dom_ok and dow_ok)
        if day_ok and day.month in months:
            for hour in hours:
                for minute in minutes:
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    raise ValueError(f"cron expression never fires: {expr!r}")
