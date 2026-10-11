"""Shell-style matching of a path against a glob pattern."""

import re


def _read_char(pattern: str, i: int) -> tuple[str, int]:
    """Return the character at ``i``, honouring a backslash escape, and the index after it."""
    if pattern[i] == "\\" and i + 1 < len(pattern):
        return pattern[i + 1], i + 2
    return pattern[i], i + 1


def _parse_class(pattern: str, i: int) -> tuple[str, int] | None:
    """Translate the bracket class opening at ``pattern[i]`` to a regex.

    Returns the regex and the index after the closing ``]``, or ``None`` if the class is unclosed.
    """
    j = i + 1
    negated = pattern.startswith("!", j)
    if negated:
        j += 1
    members = []
    first = True
    while True:
        if j >= len(pattern):
            return None
        if pattern[j] == "]" and not first:
            break
        first = False
        lo, j = _read_char(pattern, j)
        if j + 1 < len(pattern) and pattern[j] == "-" and pattern[j + 1] != "]":
            hi, j = _read_char(pattern, j + 1)
            if lo <= hi:  # an inverted range matches nothing
                members.append(f"{re.escape(lo)}-{re.escape(hi)}")
        else:
            members.append(re.escape(lo))
    j += 1  # skip the closing ']'

    if not members:
        return ("[^/]" if negated else "(?!)"), j
    body = "".join(members)
    if negated:
        return f"[^/{body}]", j
    return f"(?!/)[{body}]", j


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
    regex = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                regex.append(".*")
                i += 2
            else:
                regex.append("[^/]*")
                i += 1
        elif c == "?":
            regex.append("[^/]")
            i += 1
        elif c == "[":
            parsed = _parse_class(pattern, i)
            if parsed is None:
                regex.append(re.escape("["))
                i += 1
            else:
                regex.append(parsed[0])
                i = parsed[1]
        else:
            ch, i = _read_char(pattern, i)
            regex.append(re.escape(ch))
    return re.fullmatch("".join(regex), path, re.DOTALL) is not None
