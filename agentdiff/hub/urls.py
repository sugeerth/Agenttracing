"""Paths, query strings and form bodies, parsed without ``urllib``.

The engine's rule is that nothing outside ``agentdiff.harness`` imports a
network module, ``urllib`` included, even its string helpers. The hub
needs four small things (split a path from its query, read a query or a
url-encoded form, percent-encode a value), so they are here.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

__all__ = ["split", "parse_query", "quote", "unquote"]

_SAFE = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def split(target: str) -> Tuple[str, str]:
    """``("/path", "query")`` from a request target, fragment dropped."""
    target = target.split("#", 1)[0]
    path, _, query = target.partition("?")
    return path or "/", query


def unquote(text: str, plus: bool = True) -> str:
    """Percent-decoding; ``+`` is a space in a form or query."""
    if plus:
        text = text.replace("+", " ")
    raw = text.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == 0x25 and i + 2 < len(raw) and _is_hex(raw[i + 1:i + 3]):
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(c)
            i += 1
    return out.decode("utf-8", "replace")


def _is_hex(pair: bytes) -> bool:
    return len(pair) == 2 and all(chr(b) in "0123456789abcdefABCDEF" for b in pair)


def parse_query(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for part in text.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out.setdefault(unquote(key), []).append(unquote(value))
    return out


def quote(text: str) -> str:
    """Percent-encode everything but the unreserved characters."""
    return "".join(chr(b) if b in _SAFE else f"%{b:02X}" for b in text.encode("utf-8"))
