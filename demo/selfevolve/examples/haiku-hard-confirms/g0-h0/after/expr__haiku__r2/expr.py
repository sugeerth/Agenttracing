"""Evaluating integer arithmetic."""

import re

_SPACES = re.compile(" *")
_TOKEN = re.compile(r"[0-9]+|\*\*|//|[-+*%()]")


def _tokenize(text: str) -> list[str]:
    tokens = []
    pos = _SPACES.match(text, 0).end()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ValueError(f"unexpected character {text[pos]!r}")
        tokens.append(match.group())
        pos = _SPACES.match(text, match.end()).end()
    return tokens


class _Parser:
    """Recursive descent over the grammar, one method per precedence level."""

    def __init__(self, tokens: list[str]):
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self._pos += 1
        return tok

    def parse(self) -> int:
        value = self._expr()
        if self._peek() is not None:
            raise ValueError(f"unexpected token {self._peek()!r}")
        return value

    def _expr(self) -> int:
        value = self._term()
        while self._peek() in ("+", "-"):
            op = self._next()
            right = self._term()
            value = value + right if op == "+" else value - right
        return value

    def _term(self) -> int:
        value = self._factor()
        while self._peek() in ("*", "//", "%"):
            op = self._next()
            right = self._factor()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("division by zero")
            elif op == "//":
                value //= right
            else:
                value %= right
        return value

    def _factor(self) -> int:
        if self._peek() in ("+", "-"):
            op = self._next()
            value = self._factor()
            return -value if op == "-" else value
        return self._power()

    def _power(self) -> int:
        base = self._primary()
        if self._peek() == "**":
            self._next()
            exponent = self._factor()
            if exponent < 0:
                raise ValueError("negative exponent is not an integer")
            return base**exponent
        return base

    def _primary(self) -> int:
        tok = self._next()
        if tok == "(":
            value = self._expr()
            if self._next() != ")":
                raise ValueError("unbalanced parentheses")
            return value
        if tok[0].isdigit():
            return int(tok)
        raise ValueError(f"unexpected token {tok!r}")


def evaluate(text: str) -> int:
    """The value of an integer expression.

    Integers (digits only), ``+ - * // % **``, unary ``-`` and ``+``, and parentheses; spaces anywhere between
    tokens. The rules are Python's:

    - ``**`` binds tighter than unary minus on its left and groups right to left: ``-2 ** 2`` is -4,
      ``2 ** 3 ** 2`` is 512, ``2 ** -1`` raises ValueError (no fractions here);
    - ``*``, ``//`` and ``%`` bind tighter than ``+`` and ``-``, all group left to right;
    - ``//`` rounds toward negative infinity and ``%`` takes the sign of the divisor: ``-7 // 2`` is -4,
      ``-7 % 2`` is 1, ``7 % -2`` is -1;
    - unary operators stack: ``--3`` is 3, ``-+-3`` is 3.

    Division or modulo by zero, an empty expression, unbalanced parentheses, a lone operator, a single ``/``,
    or any other character raises ValueError. ``eval`` and ``ast`` are not to be used.
    """
    return _Parser(_tokenize(text)).parse()
