"""The telemetry vector, byte for byte.

A vector is what travels with the agent: a fixed header, one fixed-size
record per hop, and an optional table of the names its hashes stand for.
Everything is big-endian (network order) and every length is checked on
the way in, because a sink decodes vectors it did not write.

::

    header (38 bytes)
      2s  magic            b"AD"
      B   version          1
      B   flags            OVERFLOW | CLAMPED | NAMES
      I   instructions     bitmap of the fields each hop writes (fields.py)
      H   remaining        hops this vector may still take (INT's remaining hop count)
      H   hop_count
      H   dropped          hops refused once ``remaining`` reached 0
      8s  trace_id
      Q   t0               the run's start, epoch milliseconds (a hop from another process
                           places itself on the run's clock by it)
      I   agent            name hash
      I   task             name hash
    hops: hop_count x popcount(instructions) x 4 bytes, words in bit order
    names (when NAMES):  H count, then per name: I hash, B length, UTF-8 bytes

A hop never carries what a tool read or returned, only how much, how long
and how it ended, so a vector is small enough for an environment variable
or an HTTP header and safe to hand to either.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List

__all__ = ["Vector", "encode", "decode", "to_text", "from_text", "name_hash", "WireError",
           "OVERFLOW", "CLAMPED", "NAMES", "PREFIX", "MAX_BYTES"]

MAGIC = b"AD"
VERSION = 1
OVERFLOW, CLAMPED, NAMES = 0x01, 0x02, 0x04
_HEADER = struct.Struct("!2sBBIHHH8sQII")
#: the text form: a prefix naming the format, then unpadded base64url
PREFIX = "adi1."
#: no sink decodes more than this; a vector that large is not telemetry
MAX_BYTES = 1 << 20
MAX_NAME = 255


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


def encode(v: Vector) -> bytes:
    if len(v.trace_id) != 8:
        raise WireError("trace_id must be 8 bytes")
    width = v.words_per_hop
    flags = v.flags | (NAMES if v.names else 0)
    out = [_HEADER.pack(MAGIC, VERSION, flags, v.instructions, min(v.remaining, 0xFFFF), len(v.hops),
                        min(v.dropped, 0xFFFF), v.trace_id, v.t0, v.agent, v.task)]
    row = struct.Struct(f"!{width}I")
    for hop in v.hops:
        if len(hop) != width:
            raise WireError(f"a hop has {len(hop)} words; the instructions ask for {width}")
        out.append(row.pack(*hop))
    if v.names:
        out.append(struct.pack("!H", len(v.names)))
        for h, text in sorted(v.names.items()):
            data = text.encode("utf-8")[:MAX_NAME]
            # never cut a character in half
            data = data.decode("utf-8", "ignore").encode("utf-8")
            out.append(struct.pack("!IB", h, len(data)) + data)
    data = b"".join(out)
    if len(data) > MAX_BYTES:
        raise WireError(f"a vector of {len(data)} bytes is over the {MAX_BYTES}-byte limit")
    return data


def decode(data: bytes) -> Vector:
    if len(data) > MAX_BYTES:
        raise WireError(f"{len(data)} bytes is over the {MAX_BYTES}-byte limit")
    if len(data) < _HEADER.size:
        raise WireError("shorter than a header")
    (magic, version, flags, instructions, remaining, count, dropped, trace_id, t0, agent,
     task) = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise WireError("not an AgentDiff telemetry vector (bad magic)")
    if version != VERSION:
        raise WireError(f"version {version} is not one this reader knows ({VERSION})")
    width = bin(instructions).count("1")
    pos = _HEADER.size
    need = count * width * 4
    if len(data) < pos + need:
        raise WireError(f"{count} hops of {width} words need {need} bytes; {len(data) - pos} remain")
    row = struct.Struct(f"!{width}I")
    hops = [list(row.unpack_from(data, pos + i * row.size)) for i in range(count)]
    pos += need
    names: Dict[int, str] = {}
    if flags & NAMES:
        if len(data) < pos + 2:
            raise WireError("the name table is cut short")
        (n,) = struct.unpack_from("!H", data, pos)
        pos += 2
        for _ in range(n):
            if len(data) < pos + 5:
                raise WireError("a name entry is cut short")
            h, length = struct.unpack_from("!IB", data, pos)
            pos += 5
            if len(data) < pos + length:
                raise WireError("a name is cut short")
            names[h] = data[pos:pos + length].decode("utf-8", "replace")
            pos += length
    if pos != len(data):
        raise WireError(f"{len(data) - pos} trailing bytes after the vector")
    return Vector(trace_id=trace_id, instructions=instructions, t0=t0, agent=agent, task=task,
                  remaining=remaining, dropped=dropped, flags=flags & ~NAMES, hops=hops, names=names)


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
