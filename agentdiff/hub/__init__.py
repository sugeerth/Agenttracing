"""AgentDiff as a platform: one process, no database, behind a sign-in.

- :mod:`.config`    every setting, in layers (defaults, file, environment, flags)
- :mod:`.auth`      users, salted password hashes, the demo account
- :mod:`.sessions`  signed-in sessions, form tokens, a login throttle
- :mod:`.catalog`   every run under the root, found on disk by detectors
- :mod:`.ingest`    in-band telemetry agents post, kept as runs
- :mod:`.views`     the pages
- :mod:`.app`       request in, response out: every route, no sockets

The network adapter is :mod:`agentdiff.harness.hub_server`, the only part
that listens, as the engine's rule requires.
"""

from .app import App, Request, Response
from .config import HubConfig, load

__all__ = ["App", "Request", "Response", "HubConfig", "load"]
