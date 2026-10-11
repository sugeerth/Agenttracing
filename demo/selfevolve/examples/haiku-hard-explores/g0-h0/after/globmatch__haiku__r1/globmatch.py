"""Shell-style matching of a path against a glob pattern."""


def _parse_class(pattern: str, start: int):
    """Parse a bracket expression whose ``[`` is at ``start``.

    Returns ``(token, next_index)``, or ``None`` if there is no closing ``]``.
    """
    n = len(pattern)
    k = start + 1
    negate = False
    if k < n and pattern[k] == "!":
        negate = True
        k += 1
    body = k

    def read_char(i: int):
        # A backslash makes the following character literal.
        if pattern[i] == "\\" and i + 1 < n:
            return pattern[i + 1], i + 2
        return pattern[i], i + 1

    items = []
    while True:
        if k >= n:
            return None
        if pattern[k] == "]" and k > body:
            break
        lo, k = read_char(k)
        # A '-' followed by something other than ']' forms a range.
        if k + 1 < n and pattern[k] == "-" and pattern[k + 1] != "]":
            hi, k = read_char(k + 1)
            items.append((lo, hi))
        else:
            items.append((lo, lo))
    return ("class", negate, items), k + 1


def _tokenize(pattern: str):
    tokens = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                tokens.append(("globstar",))
                i += 2
            else:
                tokens.append(("star",))
                i += 1
        elif c == "?":
            tokens.append(("one",))
            i += 1
        elif c == "[":
            parsed = _parse_class(pattern, i)
            if parsed is None:
                tokens.append(("lit", "["))
                i += 1
            else:
                token, i = parsed
                tokens.append(token)
        elif c == "\\" and i + 1 < n:
            tokens.append(("lit", pattern[i + 1]))
            i += 2
        else:
            tokens.append(("lit", c))
            i += 1
    return tokens


def _char_matches(token, ch: str) -> bool:
    kind = token[0]
    if kind == "lit":
        return ch == token[1]
    if kind == "one":
        return ch != "/"
    # Bracket class: never matches "/".
    _, negate, items = token
    if ch == "/":
        return False
    return any(lo <= ch <= hi for lo, hi in items) != negate


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
    n = len(path)
    # later[j]: the tokens after the current one match path[j:].
    later = [False] * n + [True]
    for token in reversed(tokens):
        cur = [False] * (n + 1)
        for j in range(n, -1, -1):
            if token[0] == "star":
                # Either the star matches nothing, or it consumes path[j] (not "/").
                cur[j] = later[j] or (j < n and path[j] != "/" and cur[j + 1])
            elif token[0] == "globstar":
                cur[j] = later[j] or (j < n and cur[j + 1])
            else:
                cur[j] = j < n and _char_matches(token, path[j]) and later[j + 1]
        later = cur
    return later[0]
