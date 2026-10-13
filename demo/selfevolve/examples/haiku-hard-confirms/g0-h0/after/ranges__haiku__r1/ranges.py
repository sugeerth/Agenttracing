"""Whether a version satisfies a range, the way npm's caret and tilde ranges read."""

import operator
import re

_OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "=": operator.eq}
_PREFIXES = (">=", "<=", ">", "<", "=", "^", "~")
_WILDCARDS = ("*", "x", "X")
_DIGITS = re.compile(r"[0-9]+")
_PRE_ID = re.compile(r"[0-9A-Za-z-]+")


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
    sets = _parse_spec(spec)
    key = _precedence(core, pre)
    return any(_set_matches(key, core, pre, bounds, gates) for bounds, gates in sets)


def _pre_identifier_key(identifier: str) -> tuple:
    if _DIGITS.fullmatch(identifier):
        return (0, int(identifier), "")
    return (1, 0, identifier)


def _parse_pre(text: str) -> tuple:
    identifiers = text.split(".")
    if not all(_PRE_ID.fullmatch(identifier) for identifier in identifiers):
        raise ValueError(f"malformed pre-release: {text!r}")
    return tuple(_pre_identifier_key(identifier) for identifier in identifiers)


def _parse_operand(text: str) -> tuple:
    """Return (count, core, pre): count is how many leading parts are numeric, core is padded to three parts."""
    core_text, sep, pre_text = text.partition("-")
    parts = core_text.split(".")
    if len(parts) > 3:
        raise ValueError(f"malformed version: {text!r}")
    numbers = []
    for part in parts:
        if part in _WILDCARDS:
            break
        if not _DIGITS.fullmatch(part):
            raise ValueError(f"malformed version: {text!r}")
        numbers.append(int(part))
    if any(part not in _WILDCARDS for part in parts[len(numbers):]):
        raise ValueError(f"malformed version: {text!r}")
    if sep and len(numbers) < 3:
        raise ValueError(f"pre-release needs a full version: {text!r}")
    pre = _parse_pre(pre_text) if sep else None
    core = tuple(numbers + [0] * (3 - len(numbers)))
    return len(numbers), core, pre


def _parse_version(text: str) -> tuple:
    count, core, pre = _parse_operand(text)
    if count != 3:
        raise ValueError(f"malformed version: {text!r}")
    return core, pre


def _precedence(core: tuple, pre: tuple | None) -> tuple:
    return (core, (1,) if pre is None else (0, pre))


def _caret_upper(count: int, core: tuple) -> tuple:
    major, minor, patch = core
    if major or count == 1:
        return (major + 1, 0, 0)
    if minor or count == 2:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _comparator(text: str) -> tuple:
    """Return (bounds, gate): bounds are (operator, key) pairs that must all hold; gate is the core of a
    pre-release the comparator names, or None."""
    op = next((prefix for prefix in _PREFIXES if text.startswith(prefix)), "")
    count, core, pre = _parse_operand(text[len(op):])
    if count == 0:
        if op:
            raise ValueError(f"operator needs a version: {text!r}")
        return [], None
    key = _precedence(core, pre)
    gate = core if pre is not None else None
    if op in (">", ">=", "<", "<="):
        if count != 3:
            raise ValueError(f"comparison needs a full version: {text!r}")
        return [(op, key)], gate
    if count == 3 and op in ("", "="):
        return [("=", key)], gate
    if op == "^":
        upper = _caret_upper(count, core)
    else:
        upper = (core[0] + 1, 0, 0) if count == 1 else (core[0], core[1] + 1, 0)
    return [(">=", key), ("<", _precedence(upper, None))], gate


def _tokens(text: str) -> list:
    """Split a set into comparators, joining an operator separated from its version by whitespace."""
    words = text.split()
    tokens = []
    i = 0
    while i < len(words):
        if words[i] in _PREFIXES and i + 1 < len(words):
            tokens.append(words[i] + words[i + 1])
            i += 2
        else:
            tokens.append(words[i])
            i += 1
    return tokens


def _parse_spec(spec: str) -> list:
    sets = []
    for text in spec.split("||"):
        bounds = []
        gates = set()
        for token in _tokens(text):
            comparator_bounds, gate = _comparator(token)
            bounds.extend(comparator_bounds)
            if gate is not None:
                gates.add(gate)
        sets.append((bounds, gates))
    return sets


def _set_matches(key: tuple, core: tuple, pre: tuple | None, bounds: list, gates: set) -> bool:
    if pre is not None and core not in gates:
        return False
    return all(_OPERATORS[op](key, bound) for op, bound in bounds)
