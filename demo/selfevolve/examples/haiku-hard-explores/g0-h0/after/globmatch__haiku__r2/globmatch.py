"""Shell-style matching of a path against a glob pattern."""


def _parse_class(pattern: str, start: int):
    """Parse a bracket class whose ``[`` is at ``start``.

    Returns ``(predicate, index_after_class)``, or ``None`` if the class is
    never closed, in which case the ``[`` is a literal.
    """
    i = start + 1
    negate = pattern[i:i + 1] == "!"
    if negate:
        i += 1
    chars = set()
    ranges = []
    first = True
    while True:
        if i >= len(pattern):
            return None
        ch = pattern[i]
        if ch == "]" and not first:
            break
        first = False
        if ch == "\\" and i + 1 < len(pattern):
            i += 1
            ch = pattern[i]
        i += 1
        if pattern[i:i + 1] == "-" and i + 1 < len(pattern) and pattern[i + 1] != "]":
            hi = pattern[i + 1]
            i += 2
            if hi == "\\" and i < len(pattern):
                hi = pattern[i]
                i += 1
            ranges.append((ch, hi))
        else:
            chars.add(ch)

    def predicate(c: str) -> bool:
        if c == "/":
            return False
        hit = c in chars or any(lo <= c <= hi for lo, hi in ranges)
        return hit != negate

    return predicate, i + 1


def _tokenize(pattern: str):
    """Split a pattern into ``("lit", ch)``, ``("pred", fn)``, ``("star", None)``
    and ``("globstar", None)`` tokens."""
    tokens = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i + 1:i + 2] == "*":
                tokens.append(("globstar", None))
                i += 2
            else:
                tokens.append(("star", None))
                i += 1
        elif c == "?":
            tokens.append(("pred", lambda ch: ch != "/"))
            i += 1
        elif c == "\\" and i + 1 < len(pattern):
            tokens.append(("lit", pattern[i + 1]))
            i += 2
        elif c == "[" and (parsed := _parse_class(pattern, i)) is not None:
            predicate, i = parsed
            tokens.append(("pred", predicate))
        else:
            tokens.append(("lit", c))
            i += 1
    return tokens


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
    n = len(path)
    # Set of path offsets that the tokens consumed so far can end at.
    positions = {0}
    for kind, arg in _tokenize(pattern):
        if not positions:
            return False
        nxt = set()
        if kind == "lit":
            nxt = {j + 1 for j in positions if j < n and path[j] == arg}
        elif kind == "pred":
            nxt = {j + 1 for j in positions if j < n and arg(path[j])}
        elif kind == "star":
            for j in positions:
                k = j
                nxt.add(k)
                while k < n and path[k] != "/":
                    k += 1
                    nxt.add(k)
        else:  # globstar: consumes any run, so every offset from the earliest onward
            nxt = set(range(min(positions), n + 1))
        positions = nxt
    return n in positions
