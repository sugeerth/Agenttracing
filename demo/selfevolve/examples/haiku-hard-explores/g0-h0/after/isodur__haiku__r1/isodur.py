"""ISO 8601 durations, as seconds."""

import re

_NUM = r"[0-9]+(?:[.,][0-9]+)?"
_DURATION = re.compile(
    rf"P(?:(?P<W>{_NUM})W"
    rf"|(?:(?P<Y>{_NUM})Y)?(?:(?P<Mo>{_NUM})M)?(?:(?P<D>{_NUM})D)?"
    rf"(?P<T>T(?:(?P<H>{_NUM})H)?(?:(?P<Mi>{_NUM})M)?(?:(?P<S>{_NUM})S)?)?)"
)
_UNITS = {
    "W": 7 * 86400,
    "Y": 365 * 86400,
    "Mo": 30 * 86400,
    "D": 86400,
    "H": 3600,
    "Mi": 60,
    "S": 1,
}


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

    given = [(unit, value) for unit, value in match.groupdict().items() if unit != "T" and value is not None]
    if not given:
        raise ValueError(f"invalid ISO 8601 duration, no components: {text!r}")
    if match.group("T") is not None and not any(match.group(u) for u in ("H", "Mi", "S")):
        raise ValueError(f"invalid ISO 8601 duration, T with no time part: {text!r}")

    for _, value in given[:-1]:
        if "." in value or "," in value:
            raise ValueError(f"invalid ISO 8601 duration, fraction before last component: {text!r}")

    return sum(float(value.replace(",", ".")) * _UNITS[unit] for unit, value in given)
