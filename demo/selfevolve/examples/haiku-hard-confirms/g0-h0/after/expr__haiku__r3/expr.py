"""Evaluating integer arithmetic."""

import re

_TOKEN = re.compile(r"[0-9]+|\*\*|//|[-+*%()]")


def _tokenize(text: str) -> list[str]:
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ValueError(f"unexpected character {text[pos]!r}")
        tokens.append(match.group())
        pos = match.end()
    return tokens


class _Parser:
    """Recursive descent over the grammar, lowest precedence first."""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return token

    def expr(self) -> int:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self) -> int:
        value = self.unary()
        while self.peek() in ("*", "//", "%"):
            op = self.take()
            right = self.unary()
            if op == "*":
                value *= right
                continue
            if right == 0:
                raise ValueError("division by zero")
            value = value // right if op == "//" else value % right
        return value

    def unary(self) -> int:
        if self.peek() in ("+", "-"):
            op = self.take()
            operand = self.unary()
            return -operand if op == "-" else operand
        return self.power()

    def power(self) -> int:
        base = self.atom()
        if self.peek() == "**":
            self.take()
            exponent = self.unary()
            if exponent < 0:
                raise ValueError("negative exponent")
            return base**exponent
        return base

    def atom(self) -> int:
        token = self.take()
        if token == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("unbalanced parentheses")
            return value
        if token.isdigit():
            return int(token)
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
    if parser.peek() is not None:
        raise ValueError(f"unexpected token {parser.peek()!r}")
    return value
