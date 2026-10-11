"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUM = r"(?:0|[1-9][0-9]*)"
_IDENT = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION_RE = re.compile(rf"({_NUM})\.({_NUM})\.({_NUM})(?:-({_IDENT}(?:\.{_IDENT})*))?")
_PARTIAL_RE = re.compile(rf"({_NUM})(?:\.({_NUM}))?")
_WILD_MINOR_RE = re.compile(rf"({_NUM})\.({_NUM})\.[x*]")
_WILD_MAJOR_RE = re.compile(rf"({_NUM})\.[x*]")

_OPS = {"=": operator.eq, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


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
    key = _version(version)
    sets = []
    for text in spec.split("||"):
        named, checks = [], []
        for token in text.split():
            token_named, token_checks = _comparator(token)
            named += token_named
            checks += token_checks
        sets.append((named, checks))
    return any(_in_set(key, named, checks) for named, checks in sets)


def _ident_key(ident: str) -> tuple:
    return (0, int(ident)) if ident.isdigit() else (1, ident)


def _key(major: int, minor: int, patch: int, pre: str | None = None) -> tuple:
    # Orders by SemVer precedence. A release gets pre (1,) so it sorts above every pre-release (0, ...).
    if pre is None:
        return (major, minor, patch, (1,))
    return (major, minor, patch, (0,) + tuple(_ident_key(i) for i in pre.split(".")))


def _is_pre(key: tuple) -> bool:
    return key[3][0] == 0


def _version(text: str) -> tuple:
    match = _VERSION_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"malformed version: {text!r}")
    major, minor, patch, pre = match.groups()
    return _key(int(major), int(minor), int(patch), pre)


def _comparator(token: str) -> tuple[list, list]:
    """Return the versions a comparator names literally, and the (operator, bound) checks it imposes."""
    if token in ("*", "x"):
        return [], []
    if token[0] in "^~":
        return _caret_or_tilde(token[0], token[1:])
    for op in (">=", "<=", ">", "<", "="):
        if token.startswith(op):
            key = _version(token[len(op):])
            return [key], [(op, key)]
    wild = _WILD_MINOR_RE.fullmatch(token)
    if wild:
        major, minor = int(wild[1]), int(wild[2])
        return [], [(">=", _key(major, minor, 0)), ("<", _key(major, minor + 1, 0))]
    wild = _WILD_MAJOR_RE.fullmatch(token)
    if wild:
        major = int(wild[1])
        return [], [(">=", _key(major, 0, 0)), ("<", _key(major + 1, 0, 0))]
    key = _version(token)
    return [key], [("=", key)]


def _caret_or_tilde(sign: str, body: str) -> tuple[list, list]:
    full = _VERSION_RE.fullmatch(body)
    if full:
        major, minor, patch = int(full[1]), int(full[2]), int(full[3])
        key = _key(major, minor, patch, full[4])
        named, low = [key], key
    else:
        partial = _PARTIAL_RE.fullmatch(body)
        if partial is None:
            raise ValueError(f"malformed range: {sign}{body!r}")
        major = int(partial[1])
        minor = None if partial[2] is None else int(partial[2])
        patch = None
        named, low = [], _key(major, minor or 0, 0)

    if sign == "~":
        upper = (major + 1, 0, 0) if minor is None else (major, minor + 1, 0)
    elif major > 0:
        upper = (major + 1, 0, 0)
    elif minor is None:
        upper = (1, 0, 0)
    elif minor > 0:
        upper = (0, minor + 1, 0)
    elif patch is not None:
        upper = (0, 0, patch + 1)
    else:
        upper = (0, 1, 0)
    return named, [(">=", low), ("<", _key(*upper))]


def _in_set(key: tuple, named: list, checks: list) -> bool:
    if _is_pre(key) and not any(_is_pre(n) and n[:3] == key[:3] for n in named):
        return False
    return all(_OPS[op](key, bound) for op, bound in checks)
