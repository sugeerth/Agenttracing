"""Evaluating integer arithmetic."""


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
    if not tokens:
        raise ValueError(f"empty expression: {text!r}")
    parser = _Parser(tokens)
    value = parser.expr()
    if parser.peek() is not None:
        raise ValueError(f"unexpected token {parser.peek()!r} in {text!r}")
    return value


_OPERATORS = ("**", "//", "+", "-", "*", "%", "(", ")")


def _tokenize(text: str) -> list:
    """Split into integers (``int``) and operator/parenthesis strings; spaces are skipped."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == " ":
            i += 1
        elif "0" <= c <= "9":
            j = i
            while j < len(text) and "0" <= text[j] <= "9":
                j += 1
            tokens.append(int(text[i:j]))
            i = j
        else:
            for op in _OPERATORS:
                if text.startswith(op, i):
                    tokens.append(op)
                    i += len(op)
                    break
            else:
                raise ValueError(f"unexpected character {c!r} in {text!r}")
    return tokens


class _Parser:
    """Recursive descent following Python's grammar: ``**`` takes a unary operand on its right."""

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        token = self.peek()
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
        value = self.factor()
        while self.peek() in ("*", "//", "%"):
            op = self.take()
            right = self.factor()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("division or modulo by zero")
            elif op == "//":
                value //= right
            else:
                value %= right
        return value

    def factor(self) -> int:
        if self.peek() == "-":
            self.take()
            return -self.factor()
        if self.peek() == "+":
            self.take()
            return self.factor()
        return self.power()

    def power(self) -> int:
        base = self.atom()
        if self.peek() == "**":
            self.take()
            exponent = self.factor()
            if exponent < 0:
                raise ValueError("negative exponent gives a fraction, which is not supported")
            return base**exponent
        return base

    def atom(self) -> int:
        token = self.take()
        if isinstance(token, int):
            return token
        if token == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("unbalanced parentheses")
            return value
        raise ValueError(f"unexpected token {token!r}")
