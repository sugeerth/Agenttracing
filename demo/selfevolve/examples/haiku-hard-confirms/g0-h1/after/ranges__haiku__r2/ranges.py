"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUM = r"0|[1-9][0-9]*"
_PRE = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_VERSION = re.compile(rf"({_NUM})\.({_NUM})\.({_NUM})(?:-({_PRE}))?")
_TILDE_PARTIAL = re.compile(rf"({_NUM})(?:\.({_NUM}))?")
_WILD_MINOR = re.compile(rf"({_NUM})\.({_NUM})\.[x*]")
_WILD_MAJOR = re.compile(rf"({_NUM})\.[x*]")
_OPS = {"=": operator.eq, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def _pre_key(identifier: str) -> tuple:
    # Numeric identifiers compare as numbers and sort below alphanumeric ones, which compare as ASCII text.
    if re.fullmatch(r"[0-9]+", identifier):
        if len(identifier) > 1 and identifier[0] == "0":
            raise ValueError(f"leading zero in pre-release identifier: {identifier!r}")
        return (0, int(identifier))
    return (1, identifier)


def _parse_version(text: str) -> tuple:
    """Sort key ``(major, minor, patch, flag, identifiers)``; flag is 0 for a pre-release and 1 for a release."""
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch, pre = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    return (int(major), int(minor), int(patch), 0, tuple(_pre_key(i) for i in pre.split(".")))


def _release(mmp: tuple) -> tuple:
    return (*mmp, 1, ())


def _range(lo: tuple, hi: tuple) -> tuple:
    """Bounds for ``>=lo <hi``, where ``lo`` is a sort key and ``hi`` a MAJOR.MINOR.PATCH tuple."""
    return [(">=", lo), ("<", _release(hi))]


def _comparator(token: str) -> tuple:
    """Return ``(bounds, pre_mmp)``: bounds as ``(op, key)`` pairs, and the MAJOR.MINOR.PATCH a pre-release names."""
    if token in ("*", "x"):
        return [], None

    if token[0] in "^~":
        op, body = token[0], token[1:]
        try:
            key = _parse_version(body)
        except ValueError:
            key = None
        if key is not None:
            major, minor, patch = key[:3]
            if op == "~":
                bounds = _range(key, (major, minor + 1, 0))
            elif major > 0:
                bounds = _range(key, (major + 1, 0, 0))
            elif minor > 0:
                bounds = _range(key, (0, minor + 1, 0))
            else:
                bounds = _range(key, (0, 0, patch + 1))
        elif op == "~" and (partial := _TILDE_PARTIAL.fullmatch(body)):
            major = int(partial.group(1))
            if partial.group(2) is None:
                bounds = _range((major, 0, 0), (major + 1, 0, 0))
            else:
                minor = int(partial.group(2))
                bounds = _range((major, minor, 0), (major, minor + 1, 0))
        else:
            raise ValueError(f"malformed comparator: {token!r}")
        return bounds, (key[:3] if key is not None and key[3] == 0 else None)

    if (minor_wild := _WILD_MINOR.fullmatch(token)):
        major, minor = int(minor_wild.group(1)), int(minor_wild.group(2))
        return _range((major, minor, 0), (major, minor + 1, 0)), None
    if (major_wild := _WILD_MAJOR.fullmatch(token)):
        major = int(major_wild.group(1))
        return _range((major, 0, 0), (major + 1, 0, 0)), None

    op = "="
    body = token
    for candidate in (">=", "<=", ">", "<", "="):
        if token.startswith(candidate):
            op, body = candidate, token[len(candidate):]
            break
    key = _parse_version(body)
    return [(op, key)], (key[:3] if key[3] == 0 else None)


def _set_matches(key: tuple, comparators: list) -> bool:
    # A pre-release version is only eligible when the set names its MAJOR.MINOR.PATCH with a pre-release.
    if key[3] == 0 and key[:3] not in {pre for _, pre in comparators if pre is not None}:
        return False
    return all(_OPS[op](key, bound) for bounds, _ in comparators for op, bound in bounds)


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
    key = _parse_version(version)
    # Parse every set before matching so a malformed spec raises even when an earlier set already matches.
    sets = [[_comparator(token) for token in part.split()] for part in spec.split("||")]
    return any(_set_matches(key, comparators) for comparators in sets)
