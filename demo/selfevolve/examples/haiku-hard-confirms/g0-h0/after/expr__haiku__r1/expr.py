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
    parser = _Parser(_tokenize(text))
    value = parser.expression()
    parser.expect_end()
    return value


def _tokenize(text: str) -> list:
    """Split text into integers and operator strings, skipping spaces."""
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
        elif text.startswith("**", i):
            tokens.append("**")
            i += 2
        elif text.startswith("//", i):
            tokens.append("//")
            i += 2
        elif c in "+-*%()":
            tokens.append(c)
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r}")
    return tokens


class _Parser:
    """Recursive descent over the grammar, lowest precedence first::

        expression := term (('+' | '-') term)*
        term       := unary (('*' | '//' | '%') unary)*
        unary      := ('-' | '+') unary | power
        power      := primary ('**' unary)?
        primary    := INTEGER | '(' expression ')'
    """

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return token

    def expect_end(self):
        if self.peek() is not None:
            raise ValueError(f"unexpected token {self.peek()!r}")

    def expression(self) -> int:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.advance()
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self) -> int:
        value = self.unary()
        while self.peek() in ("*", "//", "%"):
            op = self.advance()
            right = self.unary()
            if op == "*":
                value = value * right
            elif right == 0:
                raise ValueError("division by zero")
            elif op == "//":
                value = value // right
            else:
                value = value % right
        return value

    def unary(self) -> int:
        if self.peek() in ("-", "+"):
            op = self.advance()
            value = self.unary()
            return -value if op == "-" else value
        return self.power()

    def power(self) -> int:
        base = self.primary()
        if self.peek() == "**":
            self.advance()
            exponent = self.unary()
            if exponent < 0:
                raise ValueError("negative exponent is not an integer")
            return base**exponent
        return base

    def primary(self) -> int:
        token = self.advance()
        if isinstance(token, int):
            return token
        if token == "(":
            value = self.expression()
            if self.advance() != ")":
                raise ValueError("expected ')'")
            return value
        raise ValueError(f"unexpected token {token!r}")
