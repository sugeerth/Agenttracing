"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUMBER = re.compile(r"0|[1-9][0-9]*")
_PRERELEASE_ID = re.compile(r"[0-9A-Za-z-]+")
_WILDCARDS = ("x", "*")
# Longer operators first so that ">=" is not read as ">" followed by "=".
_OPERATORS = ("<=", ">=", "<", ">", "=", "^", "~")
_COMPARE = {
    "=": operator.eq,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


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
    parsed = _version(version)
    # Parse every alternative before matching, so a malformed later alternative still raises.
    alternatives = [
        [bound for token in alternative.split() for bound in _comparator(token)]
        for alternative in spec.split("||")
    ]
    return any(_set_matches(parsed, bounds) for bounds in alternatives)


def _number(text: str) -> int:
    if not _NUMBER.fullmatch(text):
        raise ValueError(f"malformed version number: {text!r}")
    return int(text)


def _prerelease(text: str) -> tuple[str, ...]:
    identifiers = tuple(text.split("."))
    for identifier in identifiers:
        if not _PRERELEASE_ID.fullmatch(identifier):
            raise ValueError(f"malformed pre-release: {text!r}")
        if identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0":
            raise ValueError(f"numeric pre-release identifier has a leading zero: {text!r}")
    return identifiers


def _version(text: str) -> tuple:
    """Parse a full ``MAJOR.MINOR.PATCH[-pre]`` into ``(major, minor, patch, pre)``; ``pre`` is None if absent."""
    core, dash, pre_text = text.partition("-")
    parts = core.split(".")
    if len(parts) != 3:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch = (_number(part) for part in parts)
    return (major, minor, patch, _prerelease(pre_text) if dash else None)


def _precedence(version: tuple) -> tuple:
    """A sort key implementing SemVer precedence for a parsed version."""
    major, minor, patch, pre = version
    if pre is None:
        tail = (1, ())
    else:
        # Numeric identifiers sort below alphanumeric ones; a longer list wins a tie on the common prefix.
        tail = (0, tuple((0, int(i)) if i.isdigit() else (1, i) for i in pre))
    return ((major, minor, patch), tail)


def _comparator(token: str) -> list:
    """Expand one comparator into ``(operator, bound)`` pairs that must all hold. ``*`` and ``x`` give none."""
    if token in _WILDCARDS:
        return []
    op = next((candidate for candidate in _OPERATORS if token.startswith(candidate)), "")
    core, dash, pre_text = token[len(op):].partition("-")
    parts = core.split(".")

    wild = next((i for i, part in enumerate(parts) if part in _WILDCARDS), len(parts))
    if wild < len(parts):
        # An x-range: no operator, no pre-release, and wildcards only at the tail.
        if op or dash or any(part not in _WILDCARDS for part in parts[wild:]):
            raise ValueError(f"malformed comparator: {token!r}")
        numbers = [_number(part) for part in parts[:wild]]
        if not numbers:
            return []
        major = numbers[0]
        if len(numbers) == 1:
            return [(">=", (major, 0, 0, None)), ("<", (major + 1, 0, 0, None))]
        minor = numbers[1]
        return [(">=", (major, minor, 0, None)), ("<", (major, minor + 1, 0, None))]

    if not 1 <= len(parts) <= 3 or (dash and len(parts) != 3):
        raise ValueError(f"malformed comparator: {token!r}")
    numbers = [_number(part) for part in parts] + [0] * (3 - len(parts))
    pre = _prerelease(pre_text) if dash else None
    lower = (*numbers, pre)
    full = len(parts) == 3

    if op in ("", "=", "<", "<=", ">", ">="):
        if not full:
            raise ValueError(f"malformed comparator: {token!r}")
        return [("=" if op == "" else op, lower)]

    major, minor, patch = numbers
    if op == "^":
        if major > 0 or len(parts) == 1:
            upper = (major + 1, 0, 0)
        elif minor > 0 or len(parts) == 2:
            upper = (0, minor + 1, 0)
        else:
            upper = (0, 0, patch + 1)
    else:  # "~"
        upper = (major + 1, 0, 0) if len(parts) == 1 else (major, minor + 1, 0)
    return [(">=", lower), ("<", (*upper, None))]


def _set_matches(version: tuple, bounds: list) -> bool:
    if version[3] is not None and not any(
        bound[3] is not None and bound[:3] == version[:3] for _, bound in bounds
    ):
        return False
    key = _precedence(version)
    return all(_COMPARE[op](key, _precedence(bound)) for op, bound in bounds)
