"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import re
from operator import eq, ge, gt, le, lt

_PRE = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_VERSION_RE = re.compile(rf"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-({_PRE}))?")
_RANGE_RE = re.compile(rf"([0-9]+)(?:\.([0-9]+)(?:\.([0-9]+)(?:-({_PRE}))?)?)?")
_WILD_RE = re.compile(r"([0-9]+)\.(?:([0-9]+)\.)?[x*]")
_OPERATORS = {">=": ge, "<=": le, ">": gt, "<": lt, "=": eq}


def _key(core, pre):
    """Sort key under SemVer precedence: a release outranks any pre-release of the same core."""
    if pre is None:
        return (core, 1, ())
    idents = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre.split("."))
    return (core, 0, idents)


def _release(major, minor, patch):
    return _key((major, minor, patch), None)


def _comparator(token):
    """Return ``(anchor, bounds)``: anchor is the core a pre-release comparator names, bounds are (op, key) pairs."""
    if token in ("*", "x"):
        return None, []
    wild = _WILD_RE.fullmatch(token)
    if wild:
        major = int(wild.group(1))
        if wild.group(2) is None:
            return None, [(ge, _release(major, 0, 0)), (lt, _release(major + 1, 0, 0))]
        minor = int(wild.group(2))
        return None, [(ge, _release(major, minor, 0)), (lt, _release(major, minor + 1, 0))]

    op = next((o for o in (">=", "<=", ">", "<", "=", "^", "~") if token.startswith(o)), "")
    m = _RANGE_RE.fullmatch(token[len(op):])
    if m is None:
        raise ValueError(f"malformed comparator: {token!r}")
    major = int(m.group(1))
    minor = None if m.group(2) is None else int(m.group(2))
    patch = None if m.group(3) is None else int(m.group(3))
    pre = m.group(4)
    anchor = (major, minor, patch) if pre else None

    if op == "~":
        if minor is None:
            lo, hi = _release(major, 0, 0), _release(major + 1, 0, 0)
        elif patch is None:
            lo, hi = _release(major, minor, 0), _release(major, minor + 1, 0)
        else:
            lo, hi = _key((major, minor, patch), pre), _release(major, minor + 1, 0)
        return anchor, [(ge, lo), (lt, hi)]

    if patch is None:
        raise ValueError(f"malformed comparator: {token!r}")
    lo = _key((major, minor, patch), pre)
    if op == "^":
        if major:
            hi = _release(major + 1, 0, 0)
        elif minor:
            hi = _release(0, minor + 1, 0)
        else:
            hi = _release(0, 0, patch + 1)
        return anchor, [(ge, lo), (lt, hi)]
    return anchor, [(_OPERATORS.get(op, eq), lo)]


def _set_matches(key, core, pre, comparators):
    if pre is not None and not any(anchor == core for anchor, _ in comparators):
        return False
    return all(op(key, bound) for _, bounds in comparators for op, bound in bounds)


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
    m = _VERSION_RE.fullmatch(version)
    if m is None:
        raise ValueError(f"malformed version: {version!r}")
    core = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    pre = m.group(4)
    key = _key(core, pre)
    # Parse every set before matching so a malformed spec always raises.
    sets = [[_comparator(token) for token in part.split()] for part in spec.split("||")]
    return any(_set_matches(key, core, pre, comparators) for comparators in sets)
