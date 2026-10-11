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
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    value = parser.expr()
    if parser.pos != len(tokens):
        raise ValueError(f"unexpected token {tokens[parser.pos]!r}")
    return value


_DIGITS = "0123456789"


def _tokenize(text: str) -> list:
    """Split text into ints (numbers) and strings (operators and parentheses)."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == " ":
            i += 1
        elif c in _DIGITS:
            j = i
            while j < len(text) and text[j] in _DIGITS:
                j += 1
            tokens.append(int(text[i:j]))
            i = j
        elif text.startswith(("**", "//"), i):
            tokens.append(text[i : i + 2])
            i += 2
        elif c in "+-*%()":
            tokens.append(c)
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r}")
    return tokens


class _Parser:
    """Grammar, lowest precedence first::

        expr    := term (('+' | '-') term)*
        term    := unary (('*' | '//' | '%') unary)*
        unary   := ('-' | '+') unary | power
        power   := primary ('**' unary)?
        primary := INT | '(' expr ')'
    """

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
                raise ValueError("unbalanced parentheses")
            return value
        raise ValueError(f"expected a number or '(', got {token!r}")
