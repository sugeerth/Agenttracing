"""The telemetry vector, byte for byte.

A vector is what travels with the agent: a fixed header, a record per hop,
and a table of the names its hashes stand for. Everything is big-endian
(network order) and every length is checked on the way in, because a
sink decodes vectors it did not write.

Two encodings share one header. Version 2 is what a probe writes and
version 1 stays readable.

::

    header (38 bytes)
      2s  magic            b"AD"
      B   version          1 or 2
      B   flags            OVERFLOW | CLAMPED | NAMES | DEFLATE
      I   instructions     bitmap of the fields each hop writes (fields.py)
      H   remaining        hops this vector may still take (INT's remaining hop count)
      H   hop_count
      H   dropped          hops refused once ``remaining`` reached 0
      8s  trace_id
      Q   t0               the run's start, epoch milliseconds (a hop from another process
                           places itself on the run's clock by it)
      I   agent            name hash
      I   task             name hash

    version 1, after the header
      hops: hop_count x popcount(instructions) x 4 bytes, words in bit order
      names (when NAMES):  H count, then per name: I hash, B length, UTF-8 bytes

    version 2, after the header
      names: H count, then per name: I hash, B length, UTF-8 bytes (length 0: hash only)
      hops (when DEFLATE: varint raw length, then the zlib stream of):
        hop_count x popcount(instructions) varints, in bit order, where
        a name field (tool, node, span) is 1 + its index in the names (0: none)
        and ``start`` is the zigzag delta from the previous hop's start

A version-2 field is still one self-delimiting value, so a reader that
does not know a field skips it and reads the rest. A hop never carries what
a tool read or returned, only how much, how long and how it ended, so a
vector is small enough for an environment variable or an HTTP header and
safe to hand to either.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

__all__ = ["Vector", "encode", "decode", "to_text", "from_text", "name_hash", "WireError",
           "OVERFLOW", "CLAMPED", "NAMES", "DEFLATE", "PREFIX", "MAX_BYTES", "VERSIONS"]

MAGIC = b"AD"
VERSIONS = (1, 2)
OVERFLOW, CLAMPED, NAMES, DEFLATE = 0x01, 0x02, 0x04, 0x08
_HEADER = struct.Struct("!2sBBIHHH8sQII")
#: the text form: a prefix naming the format, then unpadded base64url
PREFIX = "adi1."
#: no sink decodes more than this, compressed or not; a vector that large is not telemetry
MAX_BYTES = 1 << 20
MAX_NAME = 255
#: the fields whose words are names (fields.py fixes these bits), and the one sent as a delta
NAME_BITS = frozenset((0, 9, 12))
DELTA_BIT = 1
#: deflate only a hop block at least this long, and only when it comes out smaller
_DEFLATE_FROM = 96


class WireError(ValueError):
    """A vector that is not one: wrong magic, version, or lengths."""


def name_hash(text: str) -> int:
    """A name's 32-bit id. 0 means "none", so a name that hashes to 0 is 1."""
    return zlib.crc32(text.encode("utf-8")) or 1


@dataclass
class Vector:
    trace_id: bytes
    instructions: int
    #: the run's start, epoch milliseconds
    t0: int
    agent: int = 0
    task: int = 0
    remaining: int = 0
    dropped: int = 0
    flags: int = 0
    #: each hop is its words, in bit order of ``instructions``
    hops: List[List[int]] = field(default_factory=list)
    #: hash -> name, for the hashes this vector's hops and header use
    names: Dict[int, str] = field(default_factory=dict)

    @property
    def words_per_hop(self) -> int:
        return bin(self.instructions).count("1")

    @property
    def overflowed(self) -> bool:
        return bool(self.flags & OVERFLOW)

    @property
    def clamped(self) -> bool:
        return bool(self.flags & CLAMPED)


def _bits(instructions: int) -> List[int]:
    return [b for b in range(32) if instructions >> b & 1]


def _name_bytes(text: str) -> bytes:
    data = text.encode("utf-8")[:MAX_NAME]
    # never cut a character in half
    return data.decode("utf-8", "ignore").encode("utf-8")


def _header(v: Vector, version: int, flags: int) -> bytes:
    if len(v.trace_id) != 8:
        raise WireError("trace_id must be 8 bytes")
    return _HEADER.pack(MAGIC, version, flags, v.instructions, min(v.remaining, 0xFFFF), len(v.hops),
                        min(v.dropped, 0xFFFF), v.trace_id, v.t0, v.agent, v.task)


# ------------------------------------------------------------------ varints
def _varint(n: int, out: bytearray) -> None:
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    n = shift = 0
    while True:
        if pos >= len(data):
            raise WireError("a value is cut short")
        b = data[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, pos
        shift += 7
        if shift > 35:
            raise WireError("a value is longer than a word")


def _zigzag(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def _unzigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


# ------------------------------------------------------------------- encode
def encode(v: Vector, version: int = 2) -> bytes:
    if version == 1:
        return _encode_v1(v)
    if version != 2:
        raise WireError(f"no encoder for version {version}")
    bits = _bits(v.instructions)
    width = len(bits)
    names_at = [i for i, b in enumerate(bits) if b in NAME_BITS]
    delta_at = bits.index(DELTA_BIT) if DELTA_BIT in bits else -1
    # every hash a hop uses, named when the vector knows the name
    used = {v.agent, v.task} | {hop[i] for hop in v.hops for i in names_at}
    used.discard(0)
    order = sorted(used | set(v.names))
    index = {h: i + 1 for i, h in enumerate(order)}
    out = bytearray()
    out += struct.pack("!H", len(order))
    for h in order:
        data = _name_bytes(v.names.get(h, ""))
        out += struct.pack("!IB", h, len(data)) + data
    block = bytearray()
    add = block.append
    # what each position is, once: 0 a plain value, 1 a name, 2 the delta-coded start
    kinds = [2 if i == delta_at else 1 if i in names_at else 0 for i in range(width)]
    prev = 0
    for hop in v.hops:
        if len(hop) != width:
            raise WireError(f"a hop has {len(hop)} words; the instructions ask for {width}")
        for kind, word in zip(kinds, hop):
            if kind == 2:
                word, prev = _zigzag(word - prev), word
            elif kind == 1 and word:
                word = index[word]
            if word < 0x80:
                add(word)
            else:
                _varint(word, block)
    flags = v.flags & ~(NAMES | DEFLATE)
    if len(block) >= _DEFLATE_FROM:
        packed = zlib.compress(bytes(block), 6)
        head = bytearray()
        _varint(len(block), head)
        if len(head) + len(packed) < len(block):
            block, flags = head + packed, flags | DEFLATE
    data = _header(v, 2, flags) + bytes(out) + bytes(block)
    if len(data) > MAX_BYTES:
        raise WireError(f"a vector of {len(data)} bytes is over the {MAX_BYTES}-byte limit")
    return data


def _encode_v1(v: Vector) -> bytes:
    width = v.words_per_hop
    flags = (v.flags & ~DEFLATE) | (NAMES if v.names else 0)
    out = [_header(v, 1, flags)]
    row = struct.Struct(f"!{width}I")
    for hop in v.hops:
        if len(hop) != width:
            raise WireError(f"a hop has {len(hop)} words; the instructions ask for {width}")
        out.append(row.pack(*hop))
    if v.names:
        out.append(struct.pack("!H", len(v.names)))
        for h, text in sorted(v.names.items()):
            data = _name_bytes(text)
            out.append(struct.pack("!IB", h, len(data)) + data)
    data = b"".join(out)
    if len(data) > MAX_BYTES:
        raise WireError(f"a vector of {len(data)} bytes is over the {MAX_BYTES}-byte limit")
    return data


# ------------------------------------------------------------------- decode
def _read_names(data: bytes, pos: int) -> Tuple[List[Tuple[int, str]], int]:
    if len(data) < pos + 2:
        raise WireError("the name table is cut short")
    (n,) = struct.unpack_from("!H", data, pos)
    pos += 2
    entries = []
    for _ in range(n):
        if len(data) < pos + 5:
            raise WireError("a name entry is cut short")
        h, length = struct.unpack_from("!IB", data, pos)
        pos += 5
        if len(data) < pos + length:
            raise WireError("a name is cut short")
        entries.append((h, data[pos:pos + length].decode("utf-8", "replace")))
        pos += length
    return entries, pos


def decode(data: bytes) -> Vector:
    if len(data) > MAX_BYTES:
        raise WireError(f"{len(data)} bytes is over the {MAX_BYTES}-byte limit")
    if len(data) < _HEADER.size:
        raise WireError("shorter than a header")
    (magic, version, flags, instructions, remaining, count, dropped, trace_id, t0, agent,
     task) = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise WireError("not an AgentDiff telemetry vector (bad magic)")
    if version not in VERSIONS:
        raise WireError(f"version {version} is not one this reader knows ({', '.join(map(str, VERSIONS))})")
    v = Vector(trace_id=trace_id, instructions=instructions, t0=t0, agent=agent, task=task,
               remaining=remaining, dropped=dropped, flags=flags & ~(NAMES | DEFLATE))
    if version == 1:
        _decode_v1(data, v, count, flags)
    else:
        _decode_v2(data, v, count, flags)
    return v


def _decode_v1(data: bytes, v: Vector, count: int, flags: int) -> None:
    width = v.words_per_hop
    pos = _HEADER.size
    need = count * width * 4
    if len(data) < pos + need:
        raise WireError(f"{count} hops of {width} words need {need} bytes; {len(data) - pos} remain")
    row = struct.Struct(f"!{width}I")
    v.hops = [list(row.unpack_from(data, pos + i * row.size)) for i in range(count)]
    pos += need
    if flags & NAMES:
        entries, pos = _read_names(data, pos)
        v.names = dict(entries)
    if pos != len(data):
        raise WireError(f"{len(data) - pos} trailing bytes after the vector")


def _decode_v2(data: bytes, v: Vector, count: int, flags: int) -> None:
    entries, pos = _read_names(data, _HEADER.size)
    v.names = {h: text for h, text in entries if text}
    table = [h for h, _ in entries]
    block = data[pos:]
    if flags & DEFLATE:
        raw_len, start = _read_varint(block, 0)
        if raw_len > MAX_BYTES:
            raise WireError("the hops would inflate past the size limit")
        z = zlib.decompressobj()
        try:
            block = z.decompress(block[start:], raw_len + 1)
        except zlib.error as exc:
            raise WireError(f"the hops do not inflate: {exc}") from None
        if len(block) != raw_len or z.unconsumed_tail or z.unused_data or not z.eof:
            raise WireError("the hops inflate to another length than the vector says")
    bits = _bits(v.instructions)
    delta_at = bits.index(DELTA_BIT) if DELTA_BIT in bits else -1
    kinds = [2 if i == delta_at else 1 if b in NAME_BITS else 0 for i, b in enumerate(bits)]
    hops, prev, p = [], 0, 0
    size, entries_n = len(block), len(table)
    for _ in range(count):
        hop = []
        add = hop.append
        for kind in kinds:
            # most values are one byte: read those without a call
            if p < size and block[p] < 0x80:
                n = block[p]
                p += 1
            else:
                n, p = _read_varint(block, p)
            if kind == 2:
                prev += _unzigzag(n)
                n = prev
                if n < 0 or n > 0xFFFFFFFF:
                    raise WireError("a start is outside a word")
            elif kind == 1 and n:
                if n > entries_n:
                    raise WireError(f"a hop names entry {n} of a table of {entries_n}")
                n = table[n - 1]
            elif n > 0xFFFFFFFF:
                raise WireError("a value is outside a word")
            add(n)
        hops.append(hop)
    if p != len(block):
        raise WireError(f"{len(block) - p} trailing bytes after the hops")
    v.hops = hops


# --------------------------------------------------------------------- text
def to_text(data: bytes) -> str:
    return PREFIX + base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_text(text: str) -> bytes:
    text = (text or "").strip()
    if not text.startswith(PREFIX):
        raise WireError(f"not a telemetry vector (no {PREFIX!r} prefix)")
    body = text[len(PREFIX):]
    if len(body) > MAX_BYTES * 4 // 3 + 4:
        raise WireError("over the size limit")
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError) as exc:
        raise WireError(f"not base64url: {exc}") from None
