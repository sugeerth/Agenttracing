"""ISO 8601 durations, as seconds."""

import re

_NUM = r"[0-9]+(?:[.,][0-9]+)?"
_WEEKS = re.compile(rf"P({_NUM})W")
_DURATION = re.compile(
    rf"P(?=T?[0-9])"
    rf"(?:({_NUM})Y)?(?:({_NUM})M)?(?:({_NUM})D)?"
    rf"(?:T(?=[0-9])(?:({_NUM})H)?(?:({_NUM})M)?(?:({_NUM})S)?)?"
)
_UNITS = (365 * 86400, 30 * 86400, 86400, 3600, 60, 1)


def parse(text: str) -> float:
    """The number of seconds in an ISO 8601 duration such as ``P1DT2H30M`` or ``PT0.5S``.

    The form is ``P`` then optional ``nY``, ``nM``, ``nD``, then optionally ``T`` and optional ``nH``, ``nM``,
    ``nS``, in that order; or ``nW`` alone (``P2W``), with nothing else. A year is 365 days and a month 30 days.
    Numbers are digits, and only the last number given may have a fraction, written with ``.`` or ``,``
    (``PT1.5M``, ``P0,5D``). Upper case only. ``P`` alone, ``PT`` alone, a ``T`` with no time part after it,
    units out of order or repeated, a sign, a fraction on any number but the last, or anything else raises
    ValueError.
    """
    weeks = _WEEKS.fullmatch(text)
    if weeks:
        return float(weeks.group(1).replace(",", ".")) * 7 * 86400

    match = _DURATION.fullmatch(text)
    if not match:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    numbers = match.groups()
    given = [i for i, n in enumerate(numbers) if n is not None]
    for i in given[:-1]:
        if "." in numbers[i] or "," in numbers[i]:
            raise ValueError(f"only the last number may have a fraction: {text!r}")

    return sum(float(numbers[i].replace(",", ".")) * _UNITS[i] for i in given)
