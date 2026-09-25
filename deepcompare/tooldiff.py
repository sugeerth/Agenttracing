"""Tool-call diffing for DeepCompare AI.

Implements the SCHEMA.md "Tool-call diff" contract: heuristic parsing of
``name(k=v, k2='v2', ...)`` style step inputs, a word/token-level LCS diff of
raw inputs, and :func:`tool_diff` which combines both into the ``tool_diff``
object attached to alignment entries that pair two tool-ish steps.
"""

from __future__ import annotations

import re
from typing import Optional, Union

from .trace import Step

#: step types that count as tool-ish for diffing purposes.
TOOLISH_TYPES = frozenset({"tool_call", "search"})

_CALL_RE = re.compile(r"^\s*[\w.]+\s*\((.*)\)\s*$", re.DOTALL)
_KEY_RE = re.compile(r"^[A-Za-z_]\w*$")
_TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]")


def parse_args(input_text: str) -> Optional[dict[str, str]]:
    """Heuristically parse a ``name(k=v, k2='v2', ...)`` style call string.

    Splits arguments at top-level commas (commas inside quotes or nested
    parens/brackets are kept), requires every argument to look like
    ``identifier=value``, and strips one layer of matching quotes from
    values.  Returns ``None`` when the text does not look like such a call
    (no ``name(...)`` shape, or any argument is not ``k=v``); returns ``{}``
    for an empty argument list.
    """
    m = _CALL_RE.match(input_text or "")
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return {}

    # Split at top-level commas, tracking quotes and bracket depth.
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: Optional[str] = None
    for ch in body:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))

    args: dict[str, str] = {}
    for part in parts:
        part = part.strip()
        if not part:
            return None
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            return None
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        args[key] = value
    return args


def token_diff(a: str, b: str) -> list[list[str]]:
    """Word/token-level LCS diff of two strings.

    Returns a list of ``[op, text]`` pairs with op in ``eq``/``del``/``ins``.
    Tokens (words, whitespace runs, punctuation) keep their separators, and
    consecutive same-op tokens are merged, so joining all ``eq``+``del`` text
    reproduces ``a`` exactly and ``eq``+``ins`` reproduces ``b``.
    """
    ta = _TOKEN_RE.findall(a or "")
    tb = _TOKEN_RE.findall(b or "")
    n, m = len(ta), len(tb)

    # LCS length table.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if ta[i] == tb[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    ops: list[list[str]] = []

    def emit(op: str, text: str) -> None:
        if ops and ops[-1][0] == op:
            ops[-1][1] += text
        else:
            ops.append([op, text])

    i = j = 0
    while i < n and j < m:
        if ta[i] == tb[j]:
            emit("eq", ta[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            emit("del", ta[i])
            i += 1
        else:
            emit("ins", tb[j])
            j += 1
    while i < n:
        emit("del", ta[i])
        i += 1
    while j < m:
        emit("ins", tb[j])
        j += 1
    return ops


def _field(step: Union[Step, dict], key: str) -> str:
    if isinstance(step, dict):
        return str(step.get(key, ""))
    return str(getattr(step, key, ""))


def tool_diff(step_a: Union[Step, dict], step_b: Union[Step, dict]) -> Optional[dict]:
    """Build the SCHEMA.md ``tool_diff`` object for a pair of aligned steps.

    Returns ``None`` when neither step is of a tool-ish type (``tool_call``
    or ``search``).  ``args_a``/``args_b`` are the heuristic parses (null when
    the input does not look like a call); ``changed``/``only_a``/``only_b``
    compare parsed argument dicts, and ``raw_diff`` is the token-level LCS
    diff of the raw inputs as a fallback.
    """
    type_a, type_b = _field(step_a, "type"), _field(step_b, "type")
    if type_a not in TOOLISH_TYPES and type_b not in TOOLISH_TYPES:
        return None

    name_a, name_b = _field(step_a, "name"), _field(step_b, "name")
    input_a, input_b = _field(step_a, "input"), _field(step_b, "input")
    args_a = parse_args(input_a)
    args_b = parse_args(input_b)

    changed: list[dict] = []
    only_a: list[str] = []
    only_b: list[str] = []
    if args_a is not None and args_b is not None:
        for key, value in args_a.items():
            if key in args_b:
                if value != args_b[key]:
                    changed.append({"key": key, "a": value, "b": args_b[key]})
            else:
                only_a.append(key)
        only_b = [key for key in args_b if key not in args_a]

    return {
        "name_a": name_a,
        "name_b": name_b,
        "same_tool": name_a == name_b,
        "args_a": args_a,
        "args_b": args_b,
        "changed": changed,
        "only_a": only_a,
        "only_b": only_b,
        "raw_diff": token_diff(input_a, input_b),
    }
