"""ISO 8601 durations, as seconds."""

import re

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_DURATION = re.compile(
    rf"P(?:{_NUM}W|(?:{_NUM}Y)?(?:{_NUM}M)?(?:{_NUM}D)?(T(?:{_NUM}H)?(?:{_NUM}M)?(?:{_NUM}S)?)?)"
)
_YEAR = 365 * 86400
_MONTH = 30 * 86400
_WEEK = 7 * 86400
_DAY = 86400


def parse(text: str) -> float:
    """The number of seconds in an ISO 8601 duration such as ``P1DT2H30M`` or ``PT0.5S``.

    The form is ``P`` then optional ``nY``, ``nM``, ``nD``, then optionally ``T`` and optional ``nH``, ``nM``,
    ``nS``, in that order; or ``nW`` alone (``P2W``), with nothing else. A year is 365 days and a month 30 days.
    Numbers are digits, and only the last number given may have a fraction, written with ``.`` or ``,``
    (``PT1.5M``, ``P0,5D``). Upper case only. ``P`` alone, ``PT`` alone, a ``T`` with no time part after it,
    units out of order or repeated, a sign, a fraction on any number but the last, or anything else raises
    ValueError.
    """
    if not isinstance(text, str):
        raise ValueError(f"not an ISO 8601 duration: {text!r}")
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(f"not an ISO 8601 duration: {text!r}")

    weeks, years, months, days, time_part, hours, minutes, seconds = match.group(1, 2, 3, 4, 5, 6, 7, 8)
    if time_part is not None and hours is None and minutes is None and seconds is None:
        raise ValueError(f"'T' with no time part: {text!r}")

    units = [
        (weeks, _WEEK),
        (years, _YEAR),
        (months, _MONTH),
        (days, _DAY),
        (hours, 3600),
        (minutes, 60),
        (seconds, 1),
    ]
    given = [value for value, _ in units if value is not None]
    if not given:
        raise ValueError(f"no components in duration: {text!r}")
    if any(("," in value or "." in value) for value in given[:-1]):
        raise ValueError(f"only the last number may have a fraction: {text!r}")

    return sum(float(value.replace(",", ".")) * size for value, size in units if value is not None)
