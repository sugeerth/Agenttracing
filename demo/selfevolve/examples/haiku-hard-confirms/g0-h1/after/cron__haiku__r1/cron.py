"""When a cron schedule fires next."""

from datetime import datetime, timedelta

_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
    )
}
_DAY_NAMES = {name: number for number, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (low, high, names) for each field, in order: minute, hour, day of month, month, day of week
_FIELDS = (
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, _MONTH_NAMES),
    (0, 7, _DAY_NAMES),  # 7 is accepted as Sunday and folded into 0 below
)

# The Gregorian calendar repeats every 400 years, so a schedule that has no match in that span never fires.
_CYCLE_DAYS = 146097


def _number(text: str) -> int:
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"expected a number, got {text!r}")
    return int(text)


def _value(text: str, low: int, high: int, names: dict[str, int]) -> int:
    if text.upper() in names:
        return names[text.upper()]
    value = _number(text)
    if not low <= value <= high:
        raise ValueError(f"{value} is out of range {low}-{high}")
    return value


def _parse_field(text: str, low: int, high: int, names: dict[str, int]) -> set[int]:
    values = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            step = _number(step_text)
            if step < 1:
                raise ValueError(f"step must be at least 1, got {step}")
        if base == "*":
            first, last = low, high
        elif "-" in base:
            first_text, _, last_text = base.partition("-")
            first = _value(first_text, low, high, names)
            last = _value(last_text, low, high, names)
            if first > last:
                raise ValueError(f"range {base!r} runs backwards")
        else:
            if slash:
                raise ValueError(f"a step needs * or a range, got {part!r}")
            first = last = _value(base, low, high, names)
        values.update(range(first, last + 1, step))
    return values


def _parse(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    fields = expr.split(" ")
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)} in {expr!r}")
    minutes, hours, doms, months, dows = (
        _parse_field(text, low, high, names) for text, (low, high, names) in zip(fields, _FIELDS)
    )
    dows = {day % 7 for day in dows}
    # As in Vixie cron, a field that starts with "*" counts as unrestricted.
    dom_restricted = not fields[2].startswith("*")
    dow_restricted = not fields[4].startswith("*")
    return minutes, hours, doms, months, dows, dom_restricted, dow_restricted


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
    minutes, hours, doms, months, dows, dom_restricted, dow_restricted = _parse(expr)
    times = [(hour, minute) for hour in sorted(hours) for minute in sorted(minutes)]

    try:
        start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.replace(hour=0, minute=0)
        for _ in range(_CYCLE_DAYS):
            if day.month in months:
                dom_ok = day.day in doms
                dow_ok = day.isoweekday() % 7 in dows
                if dom_restricted and dow_restricted:
                    matches = dom_ok or dow_ok
                else:
                    matches = dom_ok and dow_ok
                if matches:
                    for hour, minute in times:
                        candidate = day.replace(hour=hour, minute=minute)
                        if candidate >= start:
                            return candidate
            day += timedelta(days=1)
    except OverflowError:
        pass
    raise ValueError(f"{expr!r} has no run after {after}")
