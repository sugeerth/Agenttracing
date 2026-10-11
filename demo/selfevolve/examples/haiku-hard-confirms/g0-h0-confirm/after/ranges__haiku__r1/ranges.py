"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re
from typing import NamedTuple

_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
_NUMBER = re.compile(r"[0-9]+")
_OPERATOR = re.compile(r"(>=|<=|>|<|=|\^|~)?(.*)", re.DOTALL)
_WILDCARDS = ("x", "X", "*")
_OPS = {
    "=": operator.eq,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


class _Bound(NamedTuple):
    op: str
    key: tuple  # ((MAJOR, MINOR, PATCH), prerelease key)
    has_pre: bool


def _prerelease_key(pre):
    if pre is None:
        return (1,)  # a release sorts above every pre-release of the same core
    return (0, tuple((0, int(part)) if part.isdigit() else (1, part) for part in pre.split(".")))


def _parse_version(text):
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch)), pre


def _parse_range_version(text):
    """Return (numbers, pre, full) for a full version or a partial one such as ``1.2``, ``1.x``, or ``*``."""
    if _VERSION.fullmatch(text):
        core, pre = _parse_version(text)
        return list(core), pre, True
    pieces = text.split(".")
    if len(pieces) > 3:
        raise ValueError(f"malformed version: {text!r}")
    numbers = []
    wildcard = False
    for piece in pieces:
        if piece in _WILDCARDS:
            wildcard = True
        elif _NUMBER.fullmatch(piece) and not wildcard:
            numbers.append(int(piece))
        else:
            raise ValueError(f"malformed version: {text!r}")
    return numbers, None, False


def _interval(numbers, bump, pre=None):
    """Bounds for ``>= numbers`` and ``< numbers`` with the part at index ``bump`` incremented."""
    lower = tuple(numbers + [0] * (3 - len(numbers)))
    upper = [*numbers[:bump], numbers[bump] + 1]
    upper = tuple(upper + [0] * (3 - len(upper)))
    return [
        _Bound(">=", (lower, _prerelease_key(pre)), pre is not None),
        _Bound("<", (upper, _prerelease_key(None)), False),
    ]


def _bounds(token):
    op, text = _OPERATOR.fullmatch(token).groups()
    numbers, pre, full = _parse_range_version(text)
    if op in ("^", "~"):
        if pre is not None and not full:
            raise ValueError(f"pre-release needs a full version: {token!r}")
        if not numbers:
            return []
        if op == "^":
            bump = next((i for i, n in enumerate(numbers) if n), len(numbers) - 1)
        else:
            bump = min(1, len(numbers) - 1)
        return _interval(numbers, bump, pre)
    if not full:
        if op is not None:
            raise ValueError(f"comparator needs a full version: {token!r}")
        if not numbers:
            return []
        return _interval(numbers, len(numbers) - 1)
    core = tuple(numbers)
    return [_Bound(op or "=", (core, _prerelease_key(pre)), pre is not None)]


def satisfies(version: str, spec: str) -> bool:
    """Whether ``version`` (MAJOR.MINOR.PATCH, digits only, or with a pre-release after "-") is in ``spec``.

    A spec is comparator sets joined by ``||`` (either one); a set is comparators separated by spaces (all of
    them). A comparator is one of:

    - ``=1.2.3`` or ``1.2.3``, ``>1.2.3``, ``>=1.2.3``, ``<1.2.3``, ``<=1.2.3``;
    - ``^1.2.3``: >=1.2.3 <2.0.0; ``^0.2.3``: >=0.2.3 <0.3.0; ``^0.0.3``: >=0.0.3 <0.0.4;
    - ``~1.2.3``: >=1.2.3 <1.3.0; ``~1.2``: >=1.2.0 <1.3.0; ``~1``: >=1.0.0 <2.0.0;
    - ``1.2.x`` or ``1.2.*``: >=1.2.0 <1.3.0; ``1.x``: >=1.0.0 <2.0.0; ``*`` or ``x`` or an empty set: any version.

    A version with a pre-release (``1.2.3-beta``) satisfies only a set in which some comparator names the same
    MAJOR.MINOR.PATCH with a pre-release, and then compares by SemVer precedence (``1.2.3-alpha`` < ``1.2.3-beta``
    < ``1.2.3``). Whitespace around ``||`` and between comparators is ignored. A malformed version or spec raises
    ValueError.
    """
    core, pre = _parse_version(version)
    key = (core, _prerelease_key(pre))
    sets = [[bound for token in text.split() for bound in _bounds(token)] for text in spec.split("||")]
    for bounds in sets:
        if pre is not None and not any(bound.has_pre and bound.key[0] == core for bound in bounds):
            continue
        if all(_OPS[bound.op](key, bound.key) for bound in bounds):
            return True
    return False
