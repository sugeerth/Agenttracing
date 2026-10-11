"""Shell-style matching of a path against a glob pattern."""

import re
from functools import lru_cache


def _escape_class_char(c: str) -> str:
    return "\\" + c if c in "\\]^-[" else c


def _parse_class(pattern: str, i: int) -> tuple[str, int] | None:
    """Parse a bracket expression starting at ``pattern[i] == '['``.

    Returns the equivalent regex fragment and the index just past the closing
    ``]``, or ``None`` when the bracket is never closed (a literal ``[``).
    """
    n = len(pattern)
    j = i + 1
    negate = j < n and pattern[j] == "!"
    if negate:
        j += 1
    start = j
    members: list[str] = []
    while j < n:
        if pattern[j] == "]" and j != start:
            break
        if pattern[j] == "\\" and j + 1 < n:
            lo = pattern[j + 1]
            j += 2
        else:
            lo = pattern[j]
            j += 1
        if j + 1 < n and pattern[j] == "-" and pattern[j + 1] != "]":
            hi = pattern[j + 1]
            j += 2
            if hi == "\\" and j < n:
                hi = pattern[j]
                j += 1
            if lo <= hi:
                members.append(_escape_class_char(lo) + "-" + _escape_class_char(hi))
        else:
            members.append(_escape_class_char(lo))
    if j >= n:
        return None
    body = "".join(members)
    if negate:
        # Negated classes still never match "/".
        return "[^/" + body + "]", j + 1
    if not body:
        return "(?!)", j + 1
    return "(?!/)[" + body + "]", j + 1


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == "[":
            parsed = _parse_class(pattern, i)
            if parsed is None:
                parts.append(re.escape(c))
                i += 1
            else:
                fragment, i = parsed
                parts.append(fragment)
        elif c == "\\" and i + 1 < n:
            parts.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("".join(parts), re.DOTALL)


def match(pattern: str, path: str) -> bool:
    """Whether the whole of ``path`` matches ``pattern``.

    - ``*`` matches any run of characters (including none) except ``/``.
    - ``**`` matches any run of characters, ``/`` included.
    - ``?`` matches exactly one character other than ``/``.
    - ``[abc]`` matches one character in the set; ``[a-z]`` a range; ``[!abc]`` one character not in the set.
      A class never matches ``/``. A ``]`` right after ``[`` or ``[!`` is a member of the set.
    - ``\\`` makes the next character literal (``\\*`` matches a ``*``).
    - A ``[`` with no closing ``]`` is a literal ``[``.
    Matching is case-sensitive.
    """
    return _compile(pattern).fullmatch(path) is not None
