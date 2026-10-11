"""Shell-style matching of a path against a glob pattern."""


def _parse_class(pattern: str, i: int):
    """Parse a bracket expression starting at ``pattern[i] == '['``.

    Returns ``(predicate, next_index)``, or ``None`` when there is no closing ``]``
    (the ``[`` is then a literal).
    """
    n = len(pattern)
    j = i + 1
    negated = False
    if j < n and pattern[j] == "!":
        negated = True
        j += 1
    start = j
    # Find the closing bracket. A ']' right after '[' or '[!' is a member, not the end.
    end = None
    k = start
    while k < n:
        if pattern[k] == "\\" and k + 1 < n:
            k += 2
            continue
        if pattern[k] == "]" and k > start:
            end = k
            break
        k += 1
    if end is None:
        return None

    # Collect members as inclusive (lo, hi) ranges.
    ranges = []
    k = start
    while k < end:
        if pattern[k] == "\\" and k + 1 < end:
            lo = pattern[k + 1]
            k += 2
        else:
            lo = pattern[k]
            k += 1
        hi = lo
        # A '-' between two members forms a range; a trailing '-' is literal.
        if k + 1 < end and pattern[k] == "-":
            if pattern[k + 1] == "\\" and k + 2 < end:
                hi = pattern[k + 2]
                k += 3
            else:
                hi = pattern[k + 1]
                k += 2
        ranges.append((lo, hi))

    def predicate(ch: str) -> bool:
        if ch == "/":
            return False
        inside = any(lo <= ch <= hi for lo, hi in ranges)
        return inside != negated

    return predicate, end + 1


def _tokenize(pattern: str):
    """Turn a pattern into tokens the matcher can step through.

    Token kinds: ``("lit", ch)``, ``("any", None)`` for ``?``,
    ``("star", None)`` for ``*``, ``("globstar", None)`` for ``**``,
    ``("class", predicate)``.
    """
    tokens = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 < n:
                tokens.append(("lit", pattern[i + 1]))
                i += 2
            else:
                # A trailing backslash has nothing to escape, so it is literal.
                tokens.append(("lit", "\\"))
                i += 1
        elif c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                tokens.append(("globstar", None))
                i += 2
            else:
                tokens.append(("star", None))
                i += 1
        elif c == "?":
            tokens.append(("any", None))
            i += 1
        elif c == "[":
            parsed = _parse_class(pattern, i)
            if parsed is None:
                tokens.append(("lit", "["))
                i += 1
            else:
                predicate, i = parsed
                tokens.append(("class", predicate))
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
    # Set of positions in ``path`` reachable after consuming the tokens so far.
    positions = {0}
    for kind, arg in _tokenize(pattern):
        nxt = set()
        for p in positions:
            if kind == "globstar":
                nxt.update(range(p, n + 1))
            elif kind == "star":
                q = p
                nxt.add(q)
                while q < n and path[q] != "/":
                    q += 1
                    nxt.add(q)
            elif p < n:
                ch = path[p]
                if kind == "lit" and ch == arg:
                    nxt.add(p + 1)
                elif kind == "any" and ch != "/":
                    nxt.add(p + 1)
                elif kind == "class" and arg(ch):
                    nxt.add(p + 1)
        positions = nxt
        if not positions:
            return False
    return n in positions
