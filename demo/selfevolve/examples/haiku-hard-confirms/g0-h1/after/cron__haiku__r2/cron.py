"""When a cron schedule fires next."""

from datetime import datetime, timedelta

_MONTHS = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_DAYS = {name: i for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# Searching this many years of days covers every Gregorian 400-year cycle, so an expression with no match in
# that span can never fire (for example ``0 0 31 2 *``).
_SEARCH_DAYS = 400 * 366


def _number(text: str) -> int:
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"not a number: {text!r}")
    return int(text)


def _value(text: str, lo: int, hi: int, names: dict[str, int]) -> int:
    upper = text.upper()
    if upper in names:
        value = names[upper]
    else:
        value = _number(text)
    if not lo <= value <= hi:
        raise ValueError(f"{text!r} is outside {lo}-{hi}")
    return value


def _parse_field(text: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    names = names or {}
    values: set[int] = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        if slash:
            step = _number(step_text)
            if step == 0:
                raise ValueError(f"step must be positive: {part!r}")
        else:
            step = 1

        if base == "*":
            # A bare ``*`` covers the whole field; ``*/n`` steps through it.
            start, end = lo, hi
        elif "-" in base:
            pieces = base.split("-")
            if len(pieces) != 2:
                raise ValueError(f"malformed range: {part!r}")
            start = _value(pieces[0], lo, hi, names)
            end = _value(pieces[1], lo, hi, names)
            if start > end:
                raise ValueError(f"range runs backwards: {part!r}")
        else:
            if slash:
                raise ValueError(f"a step needs * or a range: {part!r}")
            values.add(_value(base, lo, hi, names))
            continue

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
        raise ValueError(f"expected five single-space-separated fields: {expr!r}")
    minute_f, hour_f, dom_f, month_f, dow_f = fields

    minutes = _parse_field(minute_f, 0, 59)
    hours = _parse_field(hour_f, 0, 23)
    doms = _parse_field(dom_f, 1, 31)
    months = _parse_field(month_f, 1, 12, _MONTHS)
    # Sunday may be written as 0 or 7; fold both onto 0 (isoweekday % 7 gives 0 for Sunday).
    dows = {d % 7 for d in _parse_field(dow_f, 0, 7, _DAYS)}

    dom_restricted = dom_f != "*"
    dow_restricted = dow_f != "*"

    def day_matches(day: datetime) -> bool:
        if day.month not in months:
            return False
        dom_ok = day.day in doms
        dow_ok = day.isoweekday() % 7 in dows
        if dom_restricted and dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(_SEARCH_DAYS):
        if day_matches(day):
            same_day = day.date() == start.date()
            for hour in sorted(hours):
                if same_day and hour < start.hour:
                    continue
                for minute in sorted(minutes):
                    if same_day and hour == start.hour and minute < start.minute:
                        continue
                    return day.replace(hour=hour, minute=minute)
        day += timedelta(days=1)
    raise ValueError(f"expression never fires: {expr!r}")
