"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re
from typing import NamedTuple


class _Version(NamedTuple):
    core: tuple[int, int, int]
    pre: tuple[str, ...]  # pre-release identifiers; empty for a release


_NUM = r"0|[1-9][0-9]*"
_VERSION = re.compile(rf"({_NUM})\.({_NUM})\.({_NUM})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
_COMPARATOR = re.compile(r"(?P<op>>=|<=|>|<|=|\^|~)?(?P<body>\S+)")
_WILDCARD = re.compile(rf"({_NUM})\.(?:({_NUM})\.)?[x*]")  # 1.x, 1.*, 1.2.x, 1.2.*
_PARTIAL = re.compile(rf"({_NUM})(?:\.({_NUM}))?")  # 1, 1.2 (for tilde)
_OPS = {
    "=": operator.eq,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


def _parse_version(text: str) -> _Version:
    m = _VERSION.fullmatch(text)
    if m is None:
        raise ValueError(f"malformed version: {text!r}")
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    for ident in pre:
        if ident.isdigit() and len(ident) > 1 and ident[0] == "0":
            raise ValueError(f"numeric pre-release identifier has leading zero: {text!r}")
    return _Version((int(m.group(1)), int(m.group(2)), int(m.group(3))), pre)


def _release(*core: int) -> _Version:
    return _Version(core, ())


def _precedence(v: _Version) -> tuple:
    """SemVer precedence as a sortable key: a release outranks its pre-releases; numeric identifiers
    sort below alphanumeric ones and compare as integers."""
    if not v.pre:
        return (v.core, 1, ())
    return (v.core, 0, tuple((0, int(p)) if p.isdigit() else (1, p) for p in v.pre))


def _parse_comparator(token: str) -> list[tuple[str, _Version]]:
    """Expand one comparator into (operator, version) pairs that must all hold."""
    if token in ("*", "x"):
        return []
    m = _COMPARATOR.fullmatch(token)
    if m is None:
        raise ValueError(f"malformed comparator: {token!r}")
    op, body = m.group("op"), m.group("body")

    if op is None:
        w = _WILDCARD.fullmatch(body)
        if w is not None:
            major = int(w.group(1))
            if w.group(2) is None:
                return [(">=", _release(major, 0, 0)), ("<", _release(major + 1, 0, 0))]
            minor = int(w.group(2))
            return [(">=", _release(major, minor, 0)), ("<", _release(major, minor + 1, 0))]

    if op == "~":
        p = _PARTIAL.fullmatch(body)
        if p is not None:
            major = int(p.group(1))
            if p.group(2) is None:
                return [(">=", _release(major, 0, 0)), ("<", _release(major + 1, 0, 0))]
            minor = int(p.group(2))
            return [(">=", _release(major, minor, 0)), ("<", _release(major, minor + 1, 0))]

    v = _parse_version(body)  # everything else needs a full MAJOR.MINOR.PATCH
    if op == "^":
        major, minor, patch = v.core
        if major:
            hi = _release(major + 1, 0, 0)
        elif minor:
            hi = _release(0, minor + 1, 0)
        else:
            hi = _release(0, 0, patch + 1)
        return [(">=", v), ("<", hi)]
    if op == "~":
        major, minor, _ = v.core
        return [(">=", v), ("<", _release(major, minor + 1, 0))]
    return [(op or "=", v)]


def _parse_set(part: str) -> list[tuple[str, _Version]]:
    comparators: list[tuple[str, _Version]] = []
    for token in part.split():
        comparators.extend(_parse_comparator(token))
    return comparators


def _set_matches(version: _Version, comparators: list[tuple[str, _Version]]) -> bool:
    # A pre-release only matches a set that names the same MAJOR.MINOR.PATCH with a pre-release.
    if version.pre and not any(c.pre and c.core == version.core for _, c in comparators):
        return False
    key = _precedence(version)
    return all(_OPS[op](key, _precedence(bound)) for op, bound in comparators)


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
    v = _parse_version(version)
    sets = [_parse_set(part) for part in spec.split("||")]  # parse everything first so bad specs always raise
    return any(_set_matches(v, comparators) for comparators in sets)
