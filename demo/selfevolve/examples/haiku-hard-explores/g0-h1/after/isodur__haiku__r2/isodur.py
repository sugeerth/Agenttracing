"""ISO 8601 durations, as seconds."""

import re

_TOKEN = re.compile(r"([0-9]+(?:[.,][0-9]+)?)([A-Z])")

_DATE_UNITS = {"Y": 0, "M": 1, "D": 2, "W": 3}
_TIME_UNITS = {"H": 0, "M": 1, "S": 2}

_DATE_SECONDS = {"Y": 365 * 86400, "M": 30 * 86400, "D": 86400, "W": 7 * 86400}
_TIME_SECONDS = {"H": 3600, "M": 60, "S": 1}


def _tokens(part: str, units: dict) -> list:
    """Split a part such as ``1Y2M`` into (number, unit) pairs, checking units are allowed and in order."""
    found = []
    pos = 0
    while pos < len(part):
        match = _TOKEN.match(part, pos)
        if match is None or match.group(2) not in units:
            raise ValueError(f"invalid duration component at {pos} in {part!r}")
        found.append(match.groups())
        pos = match.end()
    order = [units[unit] for _, unit in found]
    if order != sorted(set(order)):
        raise ValueError(f"duration units out of order or repeated in {part!r}")
    return found


def parse(text: str) -> float:
    """The number of seconds in an ISO 8601 duration such as ``P1DT2H30M`` or ``PT0.5S``.

    The form is ``P`` then optional ``nY``, ``nM``, ``nD``, then optionally ``T`` and optional ``nH``, ``nM``,
    ``nS``, in that order; or ``nW`` alone (``P2W``), with nothing else. A year is 365 days and a month 30 days.
    Numbers are digits, and only the last number given may have a fraction, written with ``.`` or ``,``
    (``PT1.5M``, ``P0,5D``). Upper case only. ``P`` alone, ``PT`` alone, a ``T`` with no time part after it,
    units out of order or repeated, a sign, a fraction on any number but the last, or anything else raises
    ValueError.
    """
    if not text.startswith("P"):
        raise ValueError(f"duration must start with 'P': {text!r}")
    body = text[1:]
    if body.count("T") > 1:
        raise ValueError(f"duration has more than one 'T': {text!r}")
    has_time = "T" in body
    date_part, _, time_part = body.partition("T")

    date = _tokens(date_part, _DATE_UNITS) if date_part else []
    time = _tokens(time_part, _TIME_UNITS) if time_part else []

    if has_time and not time:
        raise ValueError(f"'T' must be followed by a time part: {text!r}")

    if any(unit == "W" for _, unit in date):
        # Week form stands alone: a single nW and nothing else.
        if len(date) != 1 or has_time:
            raise ValueError(f"'W' must be the only component: {text!r}")
        parts = [(date[0][0], _DATE_SECONDS["W"])]
    else:
        if not date and not time:
            raise ValueError(f"duration has no components: {text!r}")
        parts = [(number, _DATE_SECONDS[unit]) for number, unit in date]
        parts += [(number, _TIME_SECONDS[unit]) for number, unit in time]

    for number, _ in parts[:-1]:
        if "." in number or "," in number:
            raise ValueError(f"only the last number may have a fraction: {text!r}")

    total = 0.0
    for number, seconds in parts:
        total += float(number.replace(",", ".")) * seconds
    return total
