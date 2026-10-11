"""Evaluating integer arithmetic."""

import re


class _Parser:
    """Recursive descent over the tokens of an expression, one method per precedence level."""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str | None:
        tok = self.peek()
        self.pos += 1
        return tok

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
            elif right == 0:
                raise ValueError("division or modulo by zero")
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
            exponent = self.unary()  # Python lets the exponent carry unary operators, so 2 ** -1 parses
            if exponent < 0:
                raise ValueError("negative exponent would produce a fraction")
            return base**exponent
        return base

    def primary(self) -> int:
        tok = self.take()
        if tok == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("unbalanced parentheses")
            return value
        if tok and tok[0] in "0123456789":
            return int(tok)
        if tok is None:
            raise ValueError("unexpected end of expression")
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
    # Each non-space token is a run of digits, a two-character operator, or a single other character
    # (which the parser then rejects if it is not valid there).
    parser = _Parser(re.findall(r"[0-9]+|\*\*|//|[-+*%()]|\S", text))
    value = parser.expr()
    if parser.peek() is not None:
        raise ValueError(f"unexpected token {parser.peek()!r}")
    return value
