"""How a vector crosses a boundary: into a subprocess, across HTTP, and back.

In-band telemetry rides the packet from switch to switch. Here the packet
is the agent's run and the switches are its tools, some of which are other
processes or services. A carrier hands the vector to the callee and takes
back the hops the callee stamped:

- subprocess: the vector goes in ``AGENTDIFF_INT``; each callee writes the
  vector it extended as its own file in the private directory named in
  ``AGENTDIFF_INT_OUT``. Several callees can share one hand-off (a shell
  that exports the variable once and runs many tools), and none overwrites
  another's hops.
- HTTP: the vector goes in the ``AgentDiff-INT`` request header; the
  service returns the extended vector in the same response header.

The callee side is one call, :func:`attach`: a probe continuing the vector
when one came, else a :class:`NullProbe`, so a tool instrumented once
runs the same either way. A malformed vector never breaks the tool; it
gets the null probe.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Optional, Tuple, Union

from .probe import Handoff, NullProbe, Probe
from .wire import WireError

__all__ = ["ENV_VECTOR", "ENV_RETURN", "HEADER", "subprocess_env", "attach", "outbound", "inbound", "reply"]

ENV_VECTOR = "AGENTDIFF_INT"
ENV_RETURN = "AGENTDIFF_INT_OUT"
HEADER = "AgentDiff-INT"

AnyProbe = Union[Probe, NullProbe]


@contextmanager
def subprocess_env(probe: AnyProbe, base: Optional[Mapping[str, str]] = None) -> Iterator[dict]:
    """An environment for a child process that carries the vector; on exit,
    the hops the child stamped are taken back into ``probe``::

        with subprocess_env(probe) as env:
            subprocess.run(["my-tool", ...], env=env)
    """
    env = dict(os.environ if base is None else base)
    handoff = probe.handover()
    if not handoff.text:
        yield env
        return
    back = tempfile.mkdtemp(prefix="agentdiff-int-")
    env[ENV_VECTOR], env[ENV_RETURN] = handoff.text, back
    try:
        yield env
    finally:
        try:
            for path in sorted(Path(back).glob("*.int")):
                try:
                    text = path.read_text(encoding="ascii").strip()
                except (OSError, UnicodeDecodeError):
                    continue
                if text:
                    try:
                        probe.absorb(text, handoff)
                    except WireError:
                        continue   # one callee's bad write must not lose the others'
        finally:
            shutil.rmtree(back, ignore_errors=True)


def attach(env: Optional[Mapping[str, str]] = None, *, node: Optional[str] = None,
           at_exit: bool = True) -> AnyProbe:
    """The callee's side: continue the vector this process was given.

    With ``at_exit`` the extended vector is written back when the process
    ends; :meth:`Probe.flush` writes it sooner.
    """
    env = os.environ if env is None else env
    text = env.get(ENV_VECTOR)
    if not text:
        return NullProbe()
    try:
        probe = Probe.resume(text, node=node)
    except WireError:
        return NullProbe()
    out = env.get(ENV_RETURN)
    # this callee's own file in the return directory: flushing again replaces it
    mine = f"{os.getpid()}-{secrets.token_hex(4)}.int"

    def flush() -> None:
        if not out or not Path(out).is_dir():
            return
        tmp = Path(out) / (mine + ".tmp")
        tmp.write_text(probe.text(), encoding="ascii")
        os.replace(tmp, Path(out) / mine)
    probe.flush = flush  # type: ignore[attr-defined]
    if at_exit and out:
        atexit.register(flush)
    return probe


def outbound(probe: AnyProbe) -> Tuple[dict, Handoff]:
    """Headers for a request to an instrumented service, and the hand-off
    to give :meth:`Probe.absorb` with the response's header."""
    handoff = probe.handover()
    return ({HEADER: handoff.text} if handoff.text else {}), handoff


def inbound(headers: Mapping[str, str], *, node: Optional[str] = None) -> AnyProbe:
    """A service's side of a request: continue the vector it carried."""
    text = next((v for k, v in headers.items() if k.lower() == HEADER.lower()), "")
    if not text:
        return NullProbe()
    try:
        return Probe.resume(text, node=node)
    except WireError:
        return NullProbe()


def reply(probe: AnyProbe) -> dict:
    """The response header that returns the extended vector."""
    text = probe.text()
    return {HEADER: text} if text else {}
