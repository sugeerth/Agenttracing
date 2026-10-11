"""ISO 8601 durations, as seconds."""

import re

_NUM = r"[0-9]+(?:[.,][0-9]+)?"

# Weeks stand alone. Otherwise the date part comes first, then an optional time part
# introduced by T; the T must be followed by at least one time component.
_DURATION = re.compile(
    r"P(?=[0-9T])"
    rf"(?:(?P<W>{_NUM})W"
    rf"|(?:(?P<Y>{_NUM})Y)?(?:(?P<Mo>{_NUM})M)?(?:(?P<D>{_NUM})D)?"
    rf"(?:T(?=[0-9])(?:(?P<H>{_NUM})H)?(?:(?P<Mi>{_NUM})M)?(?:(?P<S>{_NUM})S)?)?)"
)

_SECONDS = {
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
    if not isinstance(text, str):
        raise ValueError(f"not an ISO 8601 duration: {text!r}")
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(f"not an ISO 8601 duration: {text!r}")

    # groupdict() is in pattern order, which is textual order, so the last entry is the last number.
    parts = [(unit, value) for unit, value in match.groupdict().items() if value is not None]
    for _, value in parts[:-1]:
        if not value.isdigit():
            raise ValueError(f"only the last number may have a fraction: {text!r}")

    return sum(float(value.replace(",", ".")) * _SECONDS[unit] for unit, value in parts)
