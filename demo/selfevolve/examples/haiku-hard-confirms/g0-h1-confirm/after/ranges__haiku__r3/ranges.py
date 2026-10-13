"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUMBER = re.compile(r"0|[1-9][0-9]*")
_VERSION = re.compile(rf"({_NUMBER.pattern})\.({_NUMBER.pattern})\.({_NUMBER.pattern})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
_WILDCARDS = ("x", "X", "*")
_CHECKS = {"==": operator.eq, ">=": operator.ge, ">": operator.gt, "<": operator.lt, "<=": operator.le}


def _parse_version(text):
    """Return (major, minor, patch, prerelease) for a full version; prerelease is a tuple of identifiers or None."""
    match = _VERSION.fullmatch(text)
    if not match:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch, pre = match.groups()
    if pre is not None:
        ids = pre.split(".")
        if any(i.isdigit() and len(i) > 1 and i[0] == "0" for i in ids):
            raise ValueError(f"numeric pre-release identifier has a leading zero: {text!r}")
        pre = tuple(ids)
    return int(major), int(minor), int(patch), pre


def _key(major, minor, patch, pre):
    """Sort key giving SemVer precedence: a release outranks its pre-releases."""
    if pre is None:
        return (major, minor, patch, 1, ())
    ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre)
    return (major, minor, patch, 0, ids)


def _parse_partial(text):
    """Parse ``1``, ``1.2``, ``1.x``, ``1.2.*`` into three slots; None marks a wildcard (only trailing)."""
    parts = text.split(".")
    if len(parts) > 3:
        raise ValueError(f"malformed version: {text!r}")
    numbers = []
    for part in parts:
        if part in _WILDCARDS:
            numbers.append(None)
        elif _NUMBER.fullmatch(part):
            numbers.append(int(part))
        else:
            raise ValueError(f"malformed version: {text!r}")
    numbers += [None] * (3 - len(numbers))
    first = numbers.index(None) if None in numbers else 3
    if any(n is not None for n in numbers[first:]):
        raise ValueError(f"wildcard must be trailing: {text!r}")
    return numbers


def _window(text):
    """Bounds for ``~`` and partial comparators: ``~1.2.3`` is >=1.2.3 <1.3.0, ``1.2.x`` is >=1.2.0 <1.3.0."""
    if "-" in text:
        major, minor, patch, pre = _parse_version(text)
        fixed = [major, minor, patch]
    else:
        pre = None
        fixed = [n for n in _parse_partial(text) if n is not None]
    keep = min(len(fixed), 2)
    upper = fixed[:keep]
    upper[-1] += 1
    upper += [0] * (3 - keep)
    lower = fixed + [0] * (3 - len(fixed))
    named = tuple(fixed) if pre is not None else None
    return [(">=", _key(*lower, pre)), ("<", _key(*upper, None))], named


def _comparator(token):
    """Return (constraints, named) for one comparator; named is the M.m.p a pre-release comparator names, else None."""
    if token in _WILDCARDS:
        return [], None
    if token.startswith("^"):
        major, minor, patch, pre = _parse_version(token[1:])
        if major:
            upper = (major + 1, 0, 0)
        elif minor:
            upper = (0, minor + 1, 0)
        else:
            upper = (0, 0, patch + 1)
        constraints = [(">=", _key(major, minor, patch, pre)), ("<", _key(*upper, None))]
        return constraints, ((major, minor, patch) if pre is not None else None)
    if token.startswith("~"):
        return _window(token[1:])
    for op in (">=", "<=", ">", "<", "="):
        if token.startswith(op):
            major, minor, patch, pre = _parse_version(token[len(op):])
            check = "==" if op == "=" else op
            return [(check, _key(major, minor, patch, pre))], ((major, minor, patch) if pre is not None else None)
    try:
        major, minor, patch, pre = _parse_version(token)
    except ValueError:
        return _window(token)
    return [("==", _key(major, minor, patch, pre))], ((major, minor, patch) if pre is not None else None)


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
    major, minor, patch, pre = _parse_version(version)
    key = _key(major, minor, patch, pre)
    triple = (major, minor, patch)

    # Parse every set before matching so a malformed spec raises even when an earlier set already matches.
    sets = [[_comparator(token) for token in set_text.split()] for set_text in spec.split("||")]

    for comparators in sets:
        if pre is not None and not any(named == triple for _, named in comparators):
            continue
        if all(_CHECKS[check](key, bound) for constraints, _ in comparators for check, bound in constraints):
            return True
    return False
