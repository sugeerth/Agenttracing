# In-band agent telemetry

In-band network telemetry (INT) has every switch a packet crosses append
a few words of its own state (which switch, how long the packet queued,
how full the queue was) to the packet itself. At the end of the path a
sink takes the stack off the packet and reads the whole route: where the
time went, and which hop was unhealthy. Nothing polls the switches, and
no switch keeps a log.

AgentDiff does the same for an agent. The packet is the run. The
switches are its tool calls, sub-agents, and the processes and services
they reach. Each one stamps a fixed-size record onto one compact byte
vector that travels with the run:
- which tool
- when it started and how long it took
- how it ended
- how many bytes went in and came out
- how many tokens it spent
- which process stamped it

A sink reads the path back as hops, or as a SCHEMA trace that every
other command reads.

```python
from agentdiff.telemetry import Probe, subprocess_env

probe = Probe(agent="mine", task="fix-parser")

with probe.hop("claude-sonnet", kind="reason") as hop:   # a model call
    reply = model(messages)
    hop.tokens(prompt=reply.usage.input, completion=reply.usage.output)

@probe.tool(effect="read")                                # a tool: a hop per call
def read_file(path): ...

with subprocess_env(probe) as env:                        # a tool in another process
    subprocess.run(["agentdiff", "telemetry", "wrap", "--", "pytest", "-q"], env=env)

print(probe.text())        # adi1.…  the whole path, a few hundred characters
```

## Why in-band

- **Light.** Standard library only, nothing to run, no collector. A hop
  is one 32-bit word per field it records, about 40 bytes with the
  default fields. A real recorded Claude Code run of 8 steps over 13
  seconds is a 730-character vector.
- **It goes where the run goes.** The vector crosses a function call,
  an environment variable (`AGENTDIFF_INT`) or an HTTP header
  (`AgentDiff-INT`). A tool three processes away stamps its hop on the
  same path, on the same clock.
- **Safe to carry.** A hop records sizes, never content: not the
  prompt, not the file it read, not what the tool returned. A vector can
  sit in a header or a log line without leaking what the agent saw. Full
  content is the trace `Recorder`'s job; the two meet in the sink.
- **Open.** Each field is one word, so a reader that does not know a
  newer field skips its word and reads the rest correctly. A field is
  added by registering it, and nothing else changes.

## The vector

Big-endian throughout. Every length is checked on the way in, because a
sink decodes vectors it did not write.

| part | size | what |
|---|---|---|
| magic, version | 3 B | `AD`, 1 |
| flags | 1 B | `OVERFLOW` (hops were refused at the budget), `CLAMPED` (a value did not fit its word), `NAMES` (a name table follows) |
| instructions | 4 B | bitmap of the fields every hop writes |
| remaining | 2 B | hops the vector may still take (INT's remaining hop count) |
| hop count, dropped | 4 B | hops on the vector; hops refused once `remaining` reached 0 |
| trace id | 8 B | random |
| t0 | 8 B | the run's start, epoch milliseconds: a hop from another process places itself on the run's clock by it |
| agent, task | 8 B | name hashes |
| hops | 4 B × fields × hops | one word per bit set, in bit order |
| names | optional | `hash → name` for every hash used, each name once |

The text form is `adi1.` followed by unpadded base64url.

## Fields

`agentdiff telemetry fields` lists them. The defaults are marked ●.

| bit | field | unit |
|---|---|---|
| 0 ● | tool | name hash |
| 1 ● | start | ms from the run's start |
| 2 ● | latency | µs (clamps at about 71 minutes) |
| 3 ● | status | ok, error, timeout, denied, cancelled |
| 4 ● | kind | the SCHEMA step type |
| 5 ● | bytes_in | the arguments' size |
| 6 ● | bytes_out | the result's size |
| 7 ● | tokens_in | prompt tokens |
| 8 ● | tokens_out | completion tokens |
| 9 ● | node | which process stamped it (INT's switch id) |
| 10 | effect | read, write |
| 11 | attempt | the harness's retry number |
| 12 | span | the sub-agent acting |
| 13 | wait | ms queued before the call ran (a rate limit) |
| 14 | cost | millionths of a US dollar |

Ask for more with `Probe(instructions=DEFAULT_INSTRUCTIONS + ("effect", "cost"))`.
A deployment adds its own:

```python
from agentdiff.telemetry import FIELDS, Field
FIELDS.register(Field(20, "gpu_ms", "GPU time, ms", lambda v: int(v or 0), int))
with probe.hop("render") as hop:
    hop.set("gpu_ms", 41)
```

## Carriers

| boundary | out | back |
|---|---|---|
| subprocess | `subprocess_env(probe)` puts the vector in `AGENTDIFF_INT` | each callee writes its extended vector to its own file in `AGENTDIFF_INT_OUT` (a private directory); every one is taken back on exit |
| HTTP service | `outbound(probe)` gives the `AgentDiff-INT` request header | the service calls `inbound(headers)`, stamps its hops, and returns `reply(probe)`; the client calls `probe.absorb(text, handoff)` |
| any command | `agentdiff telemetry wrap -- CMD` | one hop for the command (name, time, exit status, bytes printed); an instrumented command nests its own hops inside it |

The callee side is always one call, `attach()` (or `inbound()`). It
returns a probe continuing the vector, or a `NullProbe` when none came
or it was malformed. The two have the same methods (a test holds them to
it), so a tool instrumented once runs unchanged with or without
telemetry.

Hand-offs are marked (`Handoff(text, base, dropped)`), so taking a
callee's hops back never loses hops this side added meanwhile, from a
parallel call. Several callees can share one hand-off. A shell that
exports `AGENTDIFF_INT` once and runs many tools loses none of them.

## The sink

```bash
agentdiff telemetry decode adi1.…          # the hops, as a table; --json for rows and a summary
agentdiff telemetry trace adi1.… -o t.json # a SCHEMA trace: batch, duel, forge and the page read it
agentdiff telemetry encode trace.json      # any trace AgentDiff reads, compacted to a vector
agentdiff telemetry send adi1.…            # post it to a hub (docs/HUB.md)
```

What the vector did not carry is never invented. Inputs and outputs are
sizes; a name left out of the table is shown as its hash. Hops refused
at the budget, and values clamped to a word, are stated in the summary,
in the trace's `source` block, and on the hub's page.

## Layout

| module | one job |
|---|---|
| `telemetry/fields.py` | the registry: what a hop may write |
| `telemetry/wire.py` | the bytes and their text form |
| `telemetry/probe.py` | stamping hops: `Probe`, `NullProbe`, `Hop`, `Handoff` |
| `telemetry/carriers.py` | crossing a process or a service, and back |
| `telemetry/sink.py` | rows, a summary, a trajectory |
| `telemetry/bridge.py` | a trajectory to a vector |
