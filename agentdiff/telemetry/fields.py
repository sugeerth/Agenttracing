"""What each hop writes: the field registry.

In-band network telemetry has a switch read an *instruction bitmap* in
the packet's header and append, for each bit set, one word of its own
state. This is that, for an agent: the vector's header carries a bitmap,
and every hop (a tool call, a sub-agent, a wrapped command) writes one
32-bit word per bit set, in bit order.

One word per field, whatever the field, is what keeps the format open:
a reader that does not know bit 19 still knows it is four bytes, skips
it, and reads the rest. A field is added by registering it here. Nothing
else changes: the encoder, decoder, sink and pages read the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..trace import EFFECTS, STEP_TYPES

__all__ = ["Field", "Registry", "FIELDS", "WORD_MAX", "KINDS", "STATUSES", "DEFAULT_INSTRUCTIONS"]

#: every field is one unsigned 32-bit word
WORD_MAX = 0xFFFFFFFF

#: step kinds by code: the SCHEMA's step types, in their own order, so a
#: code is the type's position and a new type only ever appends
KINDS: Tuple[str, ...] = STEP_TYPES
#: effects by code; 0 is "not declared"
EFFECT_CODES: Tuple[Optional[str], ...] = (None,) + tuple(EFFECTS)
#: what became of the call
STATUSES: Tuple[str, ...] = ("ok", "error", "timeout", "denied", "cancelled")


@dataclass(frozen=True)
class Field:
    """One word a hop may write.

    ``encode`` turns the probe's value (seconds, a name, a status) into the
    word; ``decode`` turns the word back into what a reader wants. Values
    too large for a word are clamped, and the vector says one was.
    """

    bit: int
    name: str
    doc: str
    encode: Callable[[object], int]
    decode: Callable[[int], object]

    def word(self, value: object) -> Tuple[int, bool]:
        """``(word, clamped)``."""
        raw = int(self.encode(value))
        if raw < 0:
            return 0, True
        if raw > WORD_MAX:
            return WORD_MAX, True
        return raw, False


class Registry:
    """The fields a vector can carry, by bit and by name."""

    def __init__(self) -> None:
        self._by_bit: Dict[int, Field] = {}
        self._by_name: Dict[str, Field] = {}

    def register(self, field: Field) -> Field:
        if not 0 <= field.bit < 32:
            raise ValueError(f"field {field.name!r}: bit {field.bit} is outside the 32-bit instruction bitmap")
        if field.bit in self._by_bit:
            raise ValueError(f"bit {field.bit} is already {self._by_bit[field.bit].name!r}")
        if field.name in self._by_name:
            raise ValueError(f"field {field.name!r} is already bit {self._by_name[field.name].bit}")
        self._by_bit[field.bit] = field
        self._by_name[field.name] = field
        return field

    def get(self, name: str) -> Field:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no telemetry field {name!r}; known: {', '.join(self.names())}") from None

    def by_bit(self, bit: int) -> Optional[Field]:
        return self._by_bit.get(bit)

    def names(self) -> List[str]:
        return [self._by_bit[b].name for b in sorted(self._by_bit)]

    def __iter__(self):
        return iter(self._by_bit[b] for b in sorted(self._by_bit))

    def bitmap(self, names: Iterable[str]) -> int:
        bits = 0
        for n in names:
            bits |= 1 << self.get(n).bit
        return bits

    def selected(self, bitmap: int) -> List[Tuple[int, Optional[Field]]]:
        """Every bit set, in order, with its field (None for a bit this
        registry does not know: its word is skipped, not misread)."""
        return [(b, self._by_bit.get(b)) for b in range(32) if bitmap >> b & 1]


def _code(table: tuple) -> Callable[[object], int]:
    def encode(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        try:
            return table.index(value)
        except ValueError:
            raise ValueError(f"{value!r} is not one of {', '.join(str(t) for t in table if t is not None)}") from None
    return encode


def _name(code: int, table: tuple) -> object:
    return table[code] if 0 <= code < len(table) else f"code {code}"


def _scaled(factor: float) -> Callable[[object], int]:
    return lambda v: round(float(v or 0) * factor)


#: the shipped fields. A deployment adds its own with FIELDS.register.
FIELDS = Registry()
for _f in (
    Field(0, "tool", "the tool, as a 32-bit name hash (names travel once in the string table)",
          lambda v: int(v or 0), int),
    Field(1, "start", "seconds from the run's start to this hop's start, in milliseconds",
          _scaled(1000), lambda w: w / 1000),
    Field(2, "latency", "how long the hop took, in microseconds (clamps at about 71 minutes)",
          _scaled(1_000_000), lambda w: w / 1_000_000),
    Field(3, "status", "what became of the call: " + ", ".join(STATUSES),
          _code(STATUSES), lambda w: _name(w, STATUSES)),
    Field(4, "kind", "the SCHEMA step type: " + ", ".join(KINDS),
          _code(KINDS), lambda w: _name(w, KINDS)),
    Field(5, "bytes_in", "bytes the tool was given (its arguments), never their content",
          lambda v: int(v or 0), int),
    Field(6, "bytes_out", "bytes the tool returned, never their content",
          lambda v: int(v or 0), int),
    Field(7, "tokens_in", "prompt tokens this hop spent, when a model call", lambda v: int(v or 0), int),
    Field(8, "tokens_out", "completion tokens this hop spent", lambda v: int(v or 0), int),
    Field(9, "node", "which process stamped the hop, as a name hash (the switch id of INT)",
          lambda v: int(v or 0), int),
    Field(10, "effect", "declared effect: none, " + ", ".join(EFFECTS),
          _code(EFFECT_CODES), lambda w: _name(w, EFFECT_CODES)),
    Field(11, "attempt", "which try at the same call, 1-based; 0 when the harness did not retry it",
          lambda v: int(v or 0), int),
    Field(12, "span", "the (sub-)agent acting, as a name hash; 0 is the root agent",
          lambda v: int(v or 0), int),
    Field(13, "wait", "time spent waiting before the call ran (a queue, a rate limit), in milliseconds",
          _scaled(1000), lambda w: w / 1000),
    Field(14, "cost", "what the hop cost, in millionths of a US dollar", _scaled(1_000_000),
          lambda w: w / 1_000_000),
):
    FIELDS.register(_f)
del _f

#: what a probe asks every hop for unless told otherwise
DEFAULT_INSTRUCTIONS: Tuple[str, ...] = ("tool", "start", "latency", "status", "kind", "bytes_in", "bytes_out",
                                         "tokens_in", "tokens_out", "node")
