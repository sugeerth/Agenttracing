"""Shell-style matching of a path against a glob pattern."""


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
    # NFA simulation: ``states`` holds every token index the pattern could be at.
    states = _closure({0}, tokens)
    for ch in path:
        nxt = set()
        for k in states:
            if k == len(tokens):
                continue
            kind, pred = tokens[k]
            if kind == "star":
                if ch != "/":
                    nxt.add(k)
            elif kind == "globstar":
                nxt.add(k)
            elif pred(ch):
                nxt.add(k + 1)
        states = _closure(nxt, tokens)
        if not states:
            return False
    return len(tokens) in states


def _closure(states, tokens):
    """Add every token index reachable without consuming a character."""
    out = set()
    stack = list(states)
    while stack:
        k = stack.pop()
        if k in out:
            continue
        out.add(k)
        if k < len(tokens) and tokens[k][0] in ("star", "globstar"):
            stack.append(k + 1)
    return out


def _tokenize(pattern):
    """Split ``pattern`` into ``(kind, predicate)`` tokens.

    Kinds are ``"char"`` (predicate tests one character), ``"star"`` and ``"globstar"``.
    """
    tokens = []
    n = len(pattern)
    i = 0
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i + 1 : i + 2] == "*":
                tokens.append(("globstar", None))
                i += 2
            else:
                tokens.append(("star", None))
                i += 1
        elif c == "?":
            tokens.append(("char", _not_slash))
            i += 1
        elif c == "[":
            parsed = _parse_class(pattern, i)
            if parsed is None:
                # No closing ']': the '[' is an ordinary character.
                tokens.append(("char", _literal(c)))
                i += 1
            else:
                pred, i = parsed
                tokens.append(("char", pred))
        elif c == "\\" and i + 1 < n:
            tokens.append(("char", _literal(pattern[i + 1])))
            i += 2
        else:
            # A trailing backslash falls through here and matches itself.
            tokens.append(("char", _literal(c)))
            i += 1
    return tokens


def _literal(expected):
    return lambda ch: ch == expected


def _not_slash(ch):
    return ch != "/"


def _class_char(pattern, k):
    """Read one character of a bracket class at ``k``, honouring ``\\`` escapes."""
    if pattern[k] == "\\" and k + 1 < len(pattern):
        return pattern[k + 1], k + 2
    return pattern[k], k + 1


def _parse_class(pattern, start):
    """Parse the bracket expression beginning at ``start``.

    Returns ``(predicate, index_after_class)``, or ``None`` if the class is unterminated.
    """
    n = len(pattern)
    k = start + 1
    negate = False
    if k < n and pattern[k] == "!":
        negate = True
        k += 1

    members = []
    first = True
    while True:
        if k >= n:
            return None
        if pattern[k] == "]" and not first:
            break
        first = False
        lo, k = _class_char(pattern, k)
        hi = lo
        if k + 1 < n and pattern[k] == "-" and pattern[k + 1] != "]":
            hi, k = _class_char(pattern, k + 1)
        members.append((lo, hi))

    def pred(ch):
        if ch == "/":
            return False
        inside = any(lo <= ch <= hi for lo, hi in members)
        return inside != negate

    return pred, k + 1
