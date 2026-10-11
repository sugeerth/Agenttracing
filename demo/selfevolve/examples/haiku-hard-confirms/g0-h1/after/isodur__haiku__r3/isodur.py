"""ISO 8601 durations, as seconds."""

import re

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_PATTERN = re.compile(
    rf"P(?:{_NUM}W"
    rf"|(?=[0-9]|T)(?:{_NUM}Y)?(?:{_NUM}M)?(?:{_NUM}D)?"
    rf"(?:T(?=[0-9])(?:{_NUM}H)?(?:{_NUM}M)?(?:{_NUM}S)?)?)"
)

# Seconds per unit, for the date part and the time part (after ``T``).
_DATE_SECONDS = {"Y": 365 * 86400, "M": 30 * 86400, "D": 86400}
_TIME_SECONDS = {"H": 3600, "M": 60, "S": 1}


def parse(text: str) -> float:
    """The number of seconds in an ISO 8601 duration such as ``P1DT2H30M`` or ``PT0.5S``.

    The form is ``P`` then optional ``nY``, ``nM``, ``nD``, then optionally ``T`` and optional ``nH``, ``nM``,
    ``nS``, in that order; or ``nW`` alone (``P2W``), with nothing else. A year is 365 days and a month 30 days.
    Numbers are digits, and only the last number given may have a fraction, written with ``.`` or ``,``
    (``PT1.5M``, ``P0,5D``). Upper case only. ``P`` alone, ``PT`` alone, a ``T`` with no time part after it,
    units out of order or repeated, a sign, a fraction on any number but the last, or anything else raises
    ValueError.
    """
    match = _PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    if match.group(1) is not None:
        parts = [(match.group(1), 7 * 86400)]
    else:
        date_groups = match.groups()[1:4]
        time_groups = match.groups()[4:7]
        parts = [(v, _DATE_SECONDS[u]) for v, u in zip(date_groups, "YMD") if v is not None]
        parts += [(v, _TIME_SECONDS[u]) for v, u in zip(time_groups, "HMS") if v is not None]

    # Only the last number may carry a fraction.
    for value, _ in parts[:-1]:
        if "." in value or "," in value:
            raise ValueError(f"only the last number may have a fraction: {text!r}")

    return sum(float(value.replace(",", ".")) * seconds for value, seconds in parts)
