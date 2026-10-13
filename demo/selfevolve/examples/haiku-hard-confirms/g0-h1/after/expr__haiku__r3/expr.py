"""Evaluating integer arithmetic."""

_DIGITS = "0123456789"
_TWO_CHAR_OPS = ("**", "//")
_ONE_CHAR_OPS = "+-*%()"


def _tokenize(text: str) -> list:
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
        elif text[i:i + 2] in _TWO_CHAR_OPS:
            tokens.append(text[i:i + 2])
            i += 2
        elif c in _ONE_CHAR_OPS:
            tokens.append(c)
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r}")
    return tokens


class _Parser:
    # expr   := term (('+' | '-') term)*
    # term   := factor (('*' | '//' | '%') factor)*
    # factor := ('+' | '-') factor | power
    # power  := primary ('**' factor)?
    # primary := NUMBER | '(' expr ')'

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self) -> int:
        value = self.expr()
        if self.peek() is not None:
            raise ValueError(f"unexpected token {self.peek()!r}")
        return value

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
                raise ValueError("division by zero")
            elif op == "//":
                value //= right
            else:
                value %= right
        return value

    def factor(self) -> int:
        # Unary operators are looped rather than recursed so long runs like "- - - 3" stay shallow.
        negate = False
        while self.peek() in ("+", "-"):
            if self.take() == "-":
                negate = not negate
        value = self.power()
        return -value if negate else value

    def power(self) -> int:
        base = self.primary()
        if self.peek() == "**":
            self.take()
            exponent = self.factor()
            if exponent < 0:
                raise ValueError("negative exponent is not an integer")
            return base ** exponent
        return base

    def primary(self) -> int:
        tok = self.take()
        if isinstance(tok, int):
            return tok
        if tok == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("unbalanced parentheses")
            return value
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
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
