"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_OPERATORS = {"=": operator.eq, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
_WILDCARDS = ("*", "x")
_PRE = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_VERSION = re.compile(rf"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-({_PRE}))?")
_PARTIAL = re.compile(rf"([0-9]+)(?:\.([0-9]+)(?:\.([0-9]+)(?:-({_PRE}))?)?)?")
_NUMBER = re.compile(r"[0-9]+")


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
    parsed = _parse_version(version)
    sets = [_parse_set(text) for text in spec.split("||")]
    return any(_matches(parsed, comparators) for comparators in sets)


def _parse_version(text):
    """Return ``((major, minor, patch), pre)``, where ``pre`` is a list of identifiers or None."""
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch)), (pre.split(".") if pre is not None else None)


def _parse_set(text):
    """Return the ``(operator, version)`` pairs of a set; an empty set gives no pairs (any version)."""
    comparators = []
    for token in text.split():
        comparators.extend(_comparators(token))
    return comparators


def _comparators(token):
    if token in _WILDCARDS:
        return []
    if any(part in _WILDCARDS for part in token.split(".")):
        return _x_range(token)
    if token[0] in "^~":
        return _caret_or_tilde(token)
    for op in (">=", "<=", ">", "<", "="):
        if token.startswith(op):
            return [(op, _parse_version(token[len(op):]))]
    return [("=", _parse_version(token))]


def _x_range(token):
    parts = token.split(".")
    fixed = 0
    while fixed < len(parts) and parts[fixed] not in _WILDCARDS:
        fixed += 1
    if (
        len(parts) > 3
        or fixed == 0
        or any(part not in _WILDCARDS for part in parts[fixed:])
        or not all(_NUMBER.fullmatch(part) for part in parts[:fixed])
    ):
        raise ValueError(f"malformed range: {token!r}")
    major = int(parts[0])
    if fixed == 1:
        low, high = (major, 0, 0), (major + 1, 0, 0)
    else:
        minor = int(parts[1])
        low, high = (major, minor, 0), (major, minor + 1, 0)
    return [(">=", (low, None)), ("<", (high, None))]


def _caret_or_tilde(token):
    match = _PARTIAL.fullmatch(token[1:])
    if match is None:
        raise ValueError(f"malformed range: {token!r}")
    major, minor, patch, pre = match.groups()
    given = [int(part) for part in (major, minor, patch) if part is not None]
    low = tuple(given + [0] * (3 - len(given)))
    if token[0] == "~":
        index = 1 if len(given) >= 2 else 0
    else:
        index = next((i for i, part in enumerate(given) if part != 0), len(given) - 1)
    high = tuple(
        part + 1 if i == index else (part if i < index else 0) for i, part in enumerate(low)
    )
    return [(">=", (low, pre.split(".") if pre is not None else None)), ("<", (high, None))]


def _precedence(version):
    core, pre = version
    if pre is None:
        return (core, 1)
    return (core, 0, tuple(_identifier(ident) for ident in pre))


def _identifier(ident):
    if ident.isdigit():
        return (0, int(ident), "")
    return (1, 0, ident)


def _matches(version, comparators):
    core, pre = version
    if pre is not None and not any(
        bound_pre is not None and bound_core == core for _, (bound_core, bound_pre) in comparators
    ):
        return False
    key = _precedence(version)
    return all(_OPERATORS[op](key, _precedence(bound)) for op, bound in comparators)
