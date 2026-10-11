"""When a cron schedule fires next."""

from datetime import datetime, timedelta

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")

# (name, low, high, named values) for minute, hour, day of month, month, day of week.
FIELDS = (
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, {name: i + 1 for i, name in enumerate(MONTHS)}),
    ("day of week", 0, 7, {name: i for i, name in enumerate(DAYS)}),
)

# Gregorian calendar repeats every 400 years, so anything unmatched by then never fires.
SEARCH_YEARS = 400


def _value(text: str, name: str, low: int, high: int, names: dict[str, int]) -> int:
    if text.upper() in names:
        return names[text.upper()]
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"bad {name} value {text!r}")
    value = int(text)
    if not low <= value <= high:
        raise ValueError(f"{name} value {value} out of range {low}-{high}")
    return value


def _parse_field(text: str, name: str, low: int, high: int, names: dict[str, int]) -> set[int]:
    values = set()
    for item in text.split(","):
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not (step_text.isascii() and step_text.isdigit()) or int(step_text) < 1:
                raise ValueError(f"bad {name} step {step_text!r}")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        else:
            first, dash, last = base.partition("-")
            start = _value(first, name, low, high, names)
            if dash:
                end = _value(last, name, low, high, names)
                if end < start:
                    raise ValueError(f"{name} range {base!r} runs backwards")
            elif slash:
                raise ValueError(f"{name} step needs * or a range, got {item!r}")
            else:
                end = start
        values.update(range(start, end + 1, step))
    return values


def _day_matches(day: datetime, dom_star: bool, dow_star: bool, doms: set[int], dows: set[int]) -> bool:
    dom_ok = day.day in doms
    dow_ok = (day.weekday() + 1) % 7 in dows  # Python Monday is 0; cron Sunday is 0.
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok  # Vixie cron: when both are restricted, either one matches.


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
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)} in {expr!r}")
    minutes, hours, doms, months, dows = (
        _parse_field(text, *spec) for text, spec in zip(fields, FIELDS)
    )
    # Day of week 7 is Sunday too.
    dows = {0 if day == 7 else day for day in dows}
    dom_star = fields[2] == "*"
    dow_star = fields[4] == "*"

    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    last_year = t.year + SEARCH_YEARS
    while t.year <= last_year:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not _day_matches(t, dom_star, dow_star, doms, dows):
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
        elif t.hour not in hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
        elif t.minute not in minutes:
            t += timedelta(minutes=1)
        else:
            return t
    raise ValueError(f"{expr!r} never fires")
