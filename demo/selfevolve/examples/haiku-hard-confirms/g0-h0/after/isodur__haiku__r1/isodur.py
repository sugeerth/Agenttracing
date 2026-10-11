"""ISO 8601 durations, as seconds."""

import re

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"

# Groups are named so each unit can be read back; the T is a lookahead so "PT" alone cannot match.
_PATTERN = re.compile(
    rf"P(?:(?P<w>{_NUM})W"
    rf"|(?:(?P<y>{_NUM})Y)?(?:(?P<mo>{_NUM})M)?(?:(?P<d>{_NUM})D)?"
    rf"(?:T(?=[0-9])(?:(?P<h>{_NUM})H)?(?:(?P<mi>{_NUM})M)?(?:(?P<s>{_NUM})S)?)?)"
)

_UNITS = (
    ("y", 365 * 86400),
    ("mo", 30 * 86400),
    ("d", 86400),
    ("h", 3600),
    ("mi", 60),
    ("s", 1),
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
    match = _PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    if match.group("w") is not None:
        return float(match.group("w").replace(",", ".")) * 7 * 86400

    given = [(name, match.group(name)) for name, _ in _UNITS if match.group(name) is not None]
    if not given:
        raise ValueError(f"invalid ISO 8601 duration: {text!r}")

    total = 0.0
    for index, (name, value) in enumerate(given):
        is_last = index == len(given) - 1
        if not is_last and ("." in value or "," in value):
            raise ValueError(f"only the last number in an ISO 8601 duration may have a fraction: {text!r}")
        factor = dict(_UNITS)[name]
        total += float(value.replace(",", ".")) * factor
    return total
