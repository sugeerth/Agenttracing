"""ISO 8601 durations, as seconds."""

import re

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY
_YEAR = 365 * _DAY

# [0-9] rather than \d, which would also match non-ASCII digits.
_NUM = r"[0-9]+(?:[.,][0-9]+)?"
_DURATION = re.compile(
    rf"P(?=[0-9T])"
    rf"(?:({_NUM})W"
    rf"|(?:({_NUM})Y)?(?:({_NUM})M)?(?:({_NUM})D)?"
    rf"(?:T(?=[0-9])(?:({_NUM})H)?(?:({_NUM})M)?(?:({_NUM})S)?)?)"
)
_TIME_UNITS = (_YEAR, _MONTH, _DAY, _HOUR, _MINUTE, 1)


def parse(text: str) -> float:
    """The number of seconds in an ISO 8601 duration such as ``P1DT2H30M`` or ``PT0.5S``.

    The form is ``P`` then optional ``nY``, ``nM``, ``nD``, then optionally ``T`` and optional ``nH``, ``nM``,
    ``nS``, in that order; or ``nW`` alone (``P2W``), with nothing else. A year is 365 days and a month 30 days.
    Numbers are digits, and only the last number given may have a fraction, written with ``.`` or ``,``
    (``PT1.5M``, ``P0,5D``). Upper case only. ``P`` alone, ``PT`` alone, a ``T`` with no time part after it,
    units out of order or repeated, a sign, a fraction on any number but the last, or anything else raises
    ValueError.
    """
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    if match.group(1) is not None:
        parts = [(match.group(1), _WEEK)]
    else:
        parts = [
            (value, unit)
            for value, unit in zip(match.groups()[1:], _TIME_UNITS)
            if value is not None
        ]

    if any(re.search(r"[.,]", value) for value, _ in parts[:-1]):
        raise ValueError(f"only the last number in an ISO 8601 duration may have a fraction: {text!r}")

    return sum(float(value.replace(",", ".")) * unit for value, unit in parts)
