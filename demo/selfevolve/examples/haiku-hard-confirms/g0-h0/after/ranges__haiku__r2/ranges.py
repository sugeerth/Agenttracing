"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_NUMERIC = re.compile(r"0|[1-9]\d*")
_IDENTIFIER = re.compile(r"[0-9A-Za-z-]+")
_COMPARE = {"=": operator.eq, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_OPERATORS = ("<=", ">=", "<", ">", "=", "^", "~")


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
    sets = [_parse_set(text) for text in spec.split("||")]
    key = _sort_key(core, pre)
    for bounds, pre_cores in sets:
        if pre is not None and core not in pre_cores:
            continue
        if all(compare(key, bound) for compare, bound in bounds):
            return True
    return False


def _pre_key(text: str, whole: str) -> tuple:
    """SemVer precedence key for dot-separated pre-release identifiers."""
    key = []
    for ident in text.split("."):
        if not _IDENTIFIER.fullmatch(ident):
            raise ValueError(f"malformed pre-release in {whole!r}")
        if ident.isdigit():
            if not _NUMERIC.fullmatch(ident):
                raise ValueError(f"malformed pre-release in {whole!r}")
            key.append((0, int(ident)))
        else:
            key.append((1, ident))
    return tuple(key)


def _sort_key(core: tuple, pre: tuple | None) -> tuple:
    """A release sorts above its own pre-releases; pre-releases sort by their identifiers."""
    if pre is None:
        return (core, 1, ())
    return (core, 0, pre)


def _parse_version(text: str) -> tuple:
    """(core, pre) of a full MAJOR.MINOR.PATCH version; ``pre`` is None for a release."""
    core, sep, pre = text.partition("-")
    nums = core.split(".")
    if len(nums) != 3 or not all(_NUMERIC.fullmatch(n) for n in nums):
        raise ValueError(f"malformed version {text!r}")
    return tuple(int(n) for n in nums), (_pre_key(pre, text) if sep else None)


def _bump(core: tuple, n: int) -> tuple:
    """Upper edge of a range that fixes the first ``n`` parts: bump the minor, or the major if only one part is fixed."""
    if n == 1:
        return (core[0] + 1, 0, 0)
    return (core[0], core[1] + 1, 0)


def _parse_comparator(token: str) -> tuple:
    """Bounds ``[(compare, key), ...]`` for one comparator, and the core it names if it has a pre-release."""
    op = next((o for o in _OPERATORS if token.startswith(o)), "")
    body = token[len(op):]
    if body in ("*", "x") and not op:
        return [], None

    main, sep, pre = body.partition("-")
    nums = []
    for part in main.split("."):
        if part in ("x", "*"):
            nums.append(None)
        elif _NUMERIC.fullmatch(part):
            nums.append(int(part))
        else:
            raise ValueError(f"malformed comparator {token!r}")
    n = nums.index(None) if None in nums else len(nums)
    if n == 0 or n > 3 or (sep and n < 3) or any(x is not None for x in nums[n:]):
        raise ValueError(f"malformed comparator {token!r}")

    core = tuple(nums[:n]) + (0,) * (3 - n)
    pre_key = _pre_key(pre, token) if sep else None
    key = _sort_key(core, pre_key)
    named = core if pre_key is not None else None

    if op == "^":
        # the first non-zero fixed part is the one that may not change
        i = next((i for i in range(n) if nums[i]), n - 1)
        upper = core[:i] + (core[i] + 1,) + (0,) * (2 - i)
        return [(operator.ge, key), (operator.lt, _sort_key(upper, None))], named
    if op == "~":
        return [(operator.ge, key), (operator.lt, _sort_key(_bump(core, n), None))], named
    if n == 3 or op in ("<", ">="):
        return [(_COMPARE[op or "="], key)], named

    # a partial such as 1.2 or 1.x names a whole range of versions
    bump = _sort_key(_bump(core, n), None)
    if op in ("", "="):
        return [(operator.ge, key), (operator.lt, bump)], named
    if op == "<=":
        return [(operator.lt, bump)], named
    return [(operator.ge, bump)], named


def _parse_set(text: str) -> tuple:
    """Bounds for every comparator in a set, and the cores of those that name a pre-release."""
    bounds = []
    pre_cores = set()
    for token in text.split():
        comparator_bounds, named = _parse_comparator(token)
        bounds += comparator_bounds
        if named is not None:
            pre_cores.add(named)
    return bounds, pre_cores
