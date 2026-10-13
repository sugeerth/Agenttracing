"""Evaluating integer arithmetic."""

import re

_TOKEN = re.compile(r"[0-9]+|\*\*|//|[-+*%()]")


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
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    value = parser.expression()
    if parser.peek() is not None:
        raise ValueError(f"unexpected token {parser.peek()!r}")
    return value


def _tokenize(text: str) -> list[str]:
    """Split text into operator and number tokens, skipping spaces."""
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos] == " ":
            pos += 1
            continue
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ValueError(f"unexpected character {text[pos]!r}")
        tokens.append(match.group())
        pos = match.end()
    return tokens


class _Parser:
    """Recursive-descent parser; each method handles one precedence level.

    expression := term (('+' | '-') term)*
    term       := factor (('*' | '//' | '%') factor)*
    factor     := ('+' | '-') factor | power
    power      := atom ('**' factor)?
    atom       := NUMBER | '(' expression ')'
    """

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expression(self) -> int:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self) -> int:
        value = self.factor()
        while self.peek() in ("*", "//", "%"):
            op = self.take()
            right = self.factor()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("division by zero")
            elif op == "//":
                value //= right
            else:
                value %= right
        return value

    def factor(self) -> int:
        if self.peek() in ("+", "-"):
            op = self.take()
            value = self.factor()
            return -value if op == "-" else value
        return self.power()

    def power(self) -> int:
        base = self.atom()
        if self.peek() == "**":
            self.take()
            exponent = self.factor()
            if exponent < 0:
                raise ValueError("negative exponent is not an integer")
            return base**exponent
        return base

    def atom(self) -> int:
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        if token == "(":
            self.take()
            value = self.expression()
            if self.peek() != ")":
                raise ValueError("unbalanced parentheses")
            self.take()
            return value
        if token[0] in "0123456789":
            self.take()
            return int(token)
        raise ValueError(f"unexpected token {token!r}")
