"""Shell-style matching of a path against a glob pattern."""


def _parse_class(pattern: str, i: int):
    """Parse a bracket class starting at ``pattern[i] == '['``.

    Returns ``(matcher, next_index)``, or ``None`` if the class has no closing ``]``.
    """
    n = len(pattern)
    j = i + 1
    negate = False
    if j < n and pattern[j] == "!":
        negate = True
        j += 1
    start = j
    members = []  # (low, high) inclusive ranges
    k = start
    while True:
        if k >= n:
            return None
        c = pattern[k]
        if c == "]" and k > start:
            break
        if c == "\\" and k + 1 < n:
            k += 1
            c = pattern[k]
        if k + 2 < n and pattern[k + 1] == "-" and pattern[k + 2] != "]":
            hi = pattern[k + 2]
            if hi == "\\" and k + 3 < n:
                hi = pattern[k + 3]
                k += 1
            members.append((c, hi))
            k += 3
        else:
            members.append((c, c))
            k += 1

    def matcher(ch: str) -> bool:
        if ch == "/":
            return False
        hit = any(lo <= ch <= hi for lo, hi in members)
        return hit != negate

    return matcher, k + 1


def _tokenize(pattern: str):
    """Turn a pattern into tokens: ``(kind, value)`` pairs.

    Kinds: ``"lit"`` (char), ``"star"``, ``"dstar"``, ``"any"`` (``?``), ``"cls"`` (matcher).
    """
    tokens = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                tokens.append(("dstar", None))
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
                matcher, i = parsed
                tokens.append(("cls", matcher))
        elif c == "\\" and i + 1 < n:
            tokens.append(("lit", pattern[i + 1]))
            i += 2
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
    tokens = _tokenize(pattern)
    m, n = len(tokens), len(path)

    # ok[i][j]: tokens[i:] match path[j:]
    ok = [[False] * (n + 1) for _ in range(m + 1)]
    ok[m][n] = True
    for i in range(m - 1, -1, -1):
        kind, value = tokens[i]
        for j in range(n, -1, -1):
            if kind == "star":
                result = ok[i + 1][j] or (j < n and path[j] != "/" and ok[i][j + 1])
            elif kind == "dstar":
                result = ok[i + 1][j] or (j < n and ok[i][j + 1])
            elif j == n:
                result = False
            elif kind == "lit":
                result = path[j] == value and ok[i + 1][j + 1]
            elif kind == "any":
                result = path[j] != "/" and ok[i + 1][j + 1]
            else:  # cls
                result = value(path[j]) and ok[i + 1][j + 1]
            ok[i][j] = result
    return ok[0][0]
