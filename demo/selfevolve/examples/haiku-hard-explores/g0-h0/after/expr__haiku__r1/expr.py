"""Evaluating integer arithmetic."""

import re

_TOKEN = re.compile(r"\s*(?:(?P<num>[0-9]+)|(?P<op>\*\*|//|[-+*%()]))")


def _tokenize(text: str) -> list:
    """Split text into ints (numbers) and strings (operators and parentheses)."""
    tokens = []
    pos = 0
    while True:
        match = _TOKEN.match(text, pos)
        if match is None:
            if text[pos:].strip():
                raise ValueError(f"unexpected character {text[pos:].lstrip()[0]!r}")
            return tokens
        if match.group("num") is not None:
            tokens.append(int(match.group("num")))
        else:
            tokens.append(match.group("op"))
        pos = match.end()


class _Parser:
    """Recursive descent over the grammar, computing values as it goes."""

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return token

    def expr(self) -> int:
        value = self.term()
        while self.peek() in ("+", "-"):
            if self.take() == "+":
                value += self.term()
            else:
                value -= self.term()
        return value

    def term(self) -> int:
        value = self.unary()
        while self.peek() in ("*", "//", "%"):
            op = self.take()
            right = self.unary()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("division by zero")
            elif op == "//":
                value //= right
            else:
                value %= right
        return value

    def unary(self) -> int:
        if self.peek() == "-":
            self.take()
            return -self.unary()
        if self.peek() == "+":
            self.take()
            return self.unary()
        return self.power()

    def power(self) -> int:
        base = self.primary()
        if self.peek() == "**":
            self.take()
            exponent = self.unary()
            if exponent < 0:
                raise ValueError("negative exponent")
            return base**exponent
        return base

    def primary(self) -> int:
        token = self.take()
        if isinstance(token, int):
            return token
        if token == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("expected ')'")
            return value
        raise ValueError(f"unexpected token {token!r}")


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
    parser = _Parser(_tokenize(text))
    value = parser.expr()
    if parser.pos != len(parser.tokens):
        raise ValueError(f"unexpected token {parser.peek()!r}")
    return value
