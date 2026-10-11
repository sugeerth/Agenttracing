"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUMBER = r"(?:0|[1-9][0-9]*)"
_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_FULL = re.compile(rf"({_NUMBER})\.({_NUMBER})\.({_NUMBER})(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?")
_NUMBER_ONLY = re.compile(_NUMBER)
_COMPARATOR = re.compile(r"(<=|>=|<|>|=|[~^])?(.+)")
_OPERATORS = {
    "=": operator.eq,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
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
    version_key = _version_key(version)
    sets = [_parse_set(text) for text in spec.split("||")]
    return any(_set_matches(version_key, comparators) for comparators in sets)


def _key(parts, pre_ids=()):
    """Sort key with SemVer precedence: a release sorts above every pre-release of the same numbers."""
    major, minor, patch = parts
    if pre_ids:
        return (major, minor, patch, 0, pre_ids)
    return (major, minor, patch, 1, ())


def _lower(parts, pre_ids=()):
    """Key for a partial version's lower bound, with missing or wildcard parts read as zero."""
    return _key([0 if part is None else part for part in parts], pre_ids)


def _parse_full(text):
    """(parts, pre_ids) for a complete MAJOR.MINOR.PATCH[-pre] version, or None if ``text`` is not one."""
    match = _FULL.fullmatch(text)
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    pre_ids = tuple((0, int(ident)) if ident.isdigit() else (1, ident) for ident in pre.split(".")) if pre else ()
    return [int(major), int(minor), int(patch)], pre_ids


def _partial(body):
    """Parts of a partial version such as ``1.2``, ``1.x`` or ``*``; missing and wildcard parts are None."""
    parts = []
    for piece in body.split("."):
        if piece in ("x", "X", "*"):
            parts.append(None)
        elif _NUMBER_ONLY.fullmatch(piece) and None not in parts:
            parts.append(int(piece))
        else:
            raise ValueError(f"malformed version: {body!r}")
    if len(parts) > 3:
        raise ValueError(f"malformed version: {body!r}")
    return parts + [None] * (3 - len(parts))


def _version_key(version):
    parsed = _parse_full(version)
    if parsed is None:
        raise ValueError(f"malformed version: {version!r}")
    return _key(*parsed)


def _caret(parts, pre_ids):
    major, minor, patch = parts
    if major > 0:
        upper = (major + 1, 0, 0)
    elif minor is None:
        upper = (1, 0, 0)
    elif minor > 0:
        upper = (0, minor + 1, 0)
    elif patch is None:
        upper = (0, 1, 0)
    else:
        upper = (0, 0, patch + 1)
    return [(">=", _lower(parts, pre_ids)), ("<", _key(upper))]


def _tilde(parts, pre_ids):
    major, minor, _ = parts
    upper = (major + 1, 0, 0) if minor is None else (major, minor + 1, 0)
    return [(">=", _lower(parts, pre_ids)), ("<", _key(upper))]


def _comparators(token):
    """The (operator, key) pairs one whitespace-free comparator stands for; empty means any version."""
    match = _COMPARATOR.fullmatch(token)
    if match is None:
        raise ValueError(f"malformed comparator: {token!r}")
    op, body = match.groups()
    full = _parse_full(body)
    if full is not None:
        parts, pre_ids = full
        if op in (None, "="):
            return [("=", _key(parts, pre_ids))]
        if op == "~":
            return _tilde(parts, pre_ids)
        if op == "^":
            return _caret(parts, pre_ids)
        return [(op, _key(parts, pre_ids))]
    if op not in (None, "~", "^"):
        raise ValueError(f"malformed comparator: {token!r}")
    parts = _partial(body)
    if parts[0] is None:
        return []
    return _caret(parts, ()) if op == "^" else _tilde(parts, ())


def _parse_set(text):
    comparators = []
    for token in text.split():
        comparators.extend(_comparators(token))
    return comparators


def _set_matches(version_key, comparators):
    is_pre_release = version_key[3] == 0
    if is_pre_release and not any(key[3] == 0 and key[:3] == version_key[:3] for _, key in comparators):
        return False
    return all(_OPERATORS[op](version_key, key) for op, key in comparators)
