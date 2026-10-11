"""ISO 8601 durations, as seconds."""

import re

_NUMBER = r"[0-9]+(?:[.,][0-9]+)?"
_WEEKS = re.compile(rf"P({_NUMBER})W")
_DURATION = re.compile(
    rf"P(?:({_NUMBER})Y)?(?:({_NUMBER})M)?(?:({_NUMBER})D)?"
    rf"(?:T(?:({_NUMBER})H)?(?:({_NUMBER})M)?(?:({_NUMBER})S)?)?"
)
# Seconds per Y, M, D, H, M, S, in the order the groups appear in _DURATION.
_FACTORS = (365 * 86400, 30 * 86400, 86400, 3600, 60, 1)


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

    if text.endswith("T"):
        raise ValueError(f"no time part after 'T' in {text!r}")
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    given = [(part, factor) for part, factor in zip(match.groups(), _FACTORS) if part is not None]
    if not given:
        raise ValueError(f"no components in ISO 8601 duration: {text!r}")
    for part, _ in given[:-1]:
        if "." in part or "," in part:
            raise ValueError(f"only the last number may have a fraction: {text!r}")

    return sum(float(part.replace(",", ".")) * factor for part, factor in given)
