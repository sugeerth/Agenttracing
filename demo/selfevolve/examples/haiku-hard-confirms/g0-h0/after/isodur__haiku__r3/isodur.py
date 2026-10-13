"""ISO 8601 durations, as seconds."""

import re

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_DURATION = re.compile(
    rf"P(?:{_NUM}W|(?:{_NUM}Y)?(?:{_NUM}M)?(?:{_NUM}D)?(?:T(?:{_NUM}H)?(?:{_NUM}M)?(?:{_NUM}S)?)?)"
)
_SECONDS = (
    365 * 86400,  # Y
    30 * 86400,  # M (months)
    86400,  # D
    3600,  # H
    60,  # M (minutes)
    1,  # S
)


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

    parts = [part for part in match.groups() if part is not None]
    has_time = "T" in text
    time_parts = match.groups()[4:]
    if not parts or (has_time and all(part is None for part in time_parts)):
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    if any(("." in part or "," in part) for part in parts[:-1]):
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    if match.group(1) is not None:
        return float(parts[0].replace(",", ".")) * 7 * 86400

    total = 0.0
    for part, unit in zip(match.groups()[1:], _SECONDS):
        if part is not None:
            total += float(part.replace(",", ".")) * unit
    return total
