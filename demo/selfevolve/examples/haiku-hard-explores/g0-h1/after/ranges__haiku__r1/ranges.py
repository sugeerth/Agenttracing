"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_COMPARE = {"==": operator.eq, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
_ORDER_OPS = (">", ">=", "<", "<=")
_COMPARATOR = re.compile(r"(\^|~|>=|<=|>|<|=)?(.*)")
_NUMBER = re.compile(r"0|[1-9][0-9]*")
_PRE_IDENTIFIER = re.compile(r"[0-9A-Za-z-]+")
_LEADING_ZERO = re.compile(r"0[0-9]+")


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
    key = _key(core, pre)
    return any(_set_matches(key, core, pre, checks, pre_cores) for checks, pre_cores in _parse_spec(spec))


def _parse_version(version):
    nums, wild, pre = _split(version)
    if wild or len(nums) != 3:
        raise ValueError(f"malformed version: {version!r}")
    return nums, pre


def _parse_spec(spec):
    """Return one ``(checks, pre_cores)`` pair per ``||`` alternative."""
    sets = []
    for alternative in spec.split("||"):
        checks = []
        pre_cores = set()
        for token in alternative.split():
            token_checks, pre_core = _parse_comparator(token)
            checks.extend(token_checks)
            if pre_core is not None:
                pre_cores.add(pre_core)
        sets.append((checks, pre_cores))
    return sets


def _parse_comparator(token):
    """Return ``(checks, pre_core)`` for one comparator; ``pre_core`` is set if it names a pre-release."""
    op, body = _COMPARATOR.fullmatch(token).groups(default="")
    nums, wild, pre = _split(body)
    if not nums:
        if op in ("", "=") and wild:
            return [], None
        raise ValueError(f"malformed comparator: {token!r}")
    if len(nums) < 3:
        if pre is not None or op in _ORDER_OPS:
            raise ValueError(f"malformed comparator: {token!r}")
        return [(">=", _key(_pad(nums), None)), ("<", _bound(nums, _upper_index(op, nums)))], None
    key = _key(nums, pre)
    pre_core = nums if pre is not None else None
    if op in ("", "="):
        return [("==", key)], pre_core
    if op in ("^", "~"):
        return [(">=", key), ("<", _bound(nums, _upper_index(op, nums)))], pre_core
    return [(op, key)], pre_core


def _split(text):
    """Split ``MAJOR[.MINOR[.PATCH]][-PRE]`` into ``(numbers, has_wildcard, pre)``; ``pre`` is None if absent."""
    core, dash, pre_text = text.partition("-")
    pre = _parse_prerelease(pre_text) if dash else None
    parts = core.split(".")
    if len(parts) > 3:
        raise ValueError(f"malformed version or comparator: {text!r}")
    nums, wild = [], False
    for part in parts:
        if part in ("x", "X", "*"):
            wild = True
        elif wild or not _NUMBER.fullmatch(part):
            raise ValueError(f"malformed version or comparator: {text!r}")
        else:
            nums.append(int(part))
    if wild and pre is not None:
        raise ValueError(f"malformed version or comparator: {text!r}")
    return tuple(nums), wild, pre


def _parse_prerelease(text):
    idents = text.split(".")
    for ident in idents:
        if not _PRE_IDENTIFIER.fullmatch(ident) or _LEADING_ZERO.fullmatch(ident):
            raise ValueError(f"malformed pre-release: {text!r}")
    return tuple(idents)


def _pad(nums):
    return tuple(nums) + (0,) * (3 - len(nums))


def _key(nums, pre):
    """SemVer precedence key; a release sorts above its own pre-releases."""
    if pre is None:
        return (*nums, 1, ())
    return (*nums, 0, tuple((0, int(ident)) if ident.isdigit() else (1, ident) for ident in pre))


def _upper_index(op, nums):
    """Index of the component that a caret, tilde, or x-range bumps to get its exclusive upper bound."""
    last = len(nums) - 1
    if op == "^":
        return next((i for i, part in enumerate(nums) if part), last)
    if op == "~":
        return min(1, last)
    return last


def _bound(nums, index):
    """Exclusive upper bound: bump ``nums[index]`` and zero what follows. Its pre-releases stay excluded."""
    padded = _pad(nums)
    return (*_pad(padded[:index] + (padded[index] + 1,)), 0, ())


def _set_matches(key, core, pre, checks, pre_cores):
    if pre is not None and core not in pre_cores:
        return False
    return all(_COMPARE[op](key, bound) for op, bound in checks)
