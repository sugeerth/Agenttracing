"""In-band agent telemetry: the trace travels with the run.

In-band network telemetry has each switch a packet crosses append a few
words of its own state to the packet, and a sink at the end read the
path off it. Here the packet is an agent's run, and the switches are its
tool calls, sub-agents and the processes and services they reach. Each
one stamps a fixed-size record (which tool, when, how long, how it ended,
how many bytes and tokens, which process) onto one compact byte vector
that travels with the run. A sink reads the whole path back, as hops or
as a SCHEMA trace every other AgentDiff command reads.

Light by construction: standard library only, about 40 bytes a hop, no
content (sizes, never text), and nothing to run. The vector moves through
a function call, an environment variable or an HTTP header.

- :mod:`.fields`   what a hop may write (a registry: add a field, change nothing else)
- :mod:`.wire`     the bytes, and their text form
- :mod:`.probe`    stamping hops: ``Probe``, ``NullProbe``
- :mod:`.carriers` handing the vector to a subprocess or a service, and back
- :mod:`.sink`     reading it back: rows, a summary, a trajectory
- :mod:`.bridge`   any SCHEMA trace (every adapter's output) compacted to a vector
"""

from .bridge import from_trajectory
from .carriers import ENV_RETURN, ENV_VECTOR, HEADER, attach, inbound, outbound, reply, subprocess_env
from .fields import DEFAULT_INSTRUCTIONS, FIELDS, RL_INSTRUCTIONS, Field, Registry
from .probe import Handoff, Hop, NullProbe, Probe, size_of
from .sink import read, rows, summary, to_trajectory
from .wire import Vector, WireError, decode, encode, from_text, to_text

__all__ = [
    "Probe", "NullProbe", "Hop", "Handoff", "size_of",
    "Field", "Registry", "FIELDS", "DEFAULT_INSTRUCTIONS", "RL_INSTRUCTIONS",
    "Vector", "WireError", "encode", "decode", "to_text", "from_text",
    "subprocess_env", "attach", "outbound", "inbound", "reply", "ENV_VECTOR", "ENV_RETURN", "HEADER",
    "read", "rows", "summary", "to_trajectory", "from_trajectory",
]
