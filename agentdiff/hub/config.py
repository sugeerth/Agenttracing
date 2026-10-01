"""The hub's settings: one place, in layers.

Every setting has its default here and nowhere else. A file
(``.agentdiff-hub/hub.json`` under the root, or ``--config``) overrides
the defaults, ``AGENTDIFF_HUB_<NAME>`` environment variables override the
file, and command-line flags override everything. Nothing the hub does
reads a literal it could have read from here.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Mapping, Optional

__all__ = ["HubConfig", "load", "STATE_DIR"]

#: where the hub keeps its own state, under the root it serves
STATE_DIR = ".agentdiff-hub"


@dataclass(frozen=True)
class HubConfig:
    root: str = "."
    host: str = "127.0.0.1"
    port: int = 8790
    title: str = "AgentDiff"
    #: the demo account: on by default on this machine only (see `effective_demo`)
    demo: Optional[bool] = None
    demo_user: str = "demo"
    demo_password: str = "demo"
    session_hours: float = 12.0
    #: failed logins for one name before it must wait, and how long
    login_attempts: int = 5
    login_lockout_s: float = 60.0
    #: how deep under the root the catalog looks for runs
    scan_depth: int = 3
    max_entries: int = 500
    #: the largest telemetry post accepted, in bytes
    max_post_bytes: int = 2_000_000
    password_iterations: int = 240_000

    @property
    def state_dir(self) -> Path:
        return Path(self.root) / STATE_DIR

    def effective_demo(self, loopback: bool) -> bool:
        """The demo account exists on this machine unless turned off, and
        beyond it only when turned on explicitly: a known password on a
        network is no login at all."""
        return loopback if self.demo is None else bool(self.demo)

    def public(self) -> dict:
        """The settings safe to show: never a password."""
        return {k: v for k, v in asdict(self).items() if "password" not in k}


def _coerce(name: str, raw, default):
    kind = type(default) if default is not None else (bool if name == "demo" else str)
    if kind is bool or name == "demo":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        return kind(raw)
    except (TypeError, ValueError):
        raise ValueError(f"hub setting {name}: {raw!r} is not a {kind.__name__}") from None


def load(root: str = ".", *, file: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
         overrides: Optional[dict] = None) -> HubConfig:
    """Defaults, then the file, then the environment, then ``overrides``."""
    env = os.environ if env is None else env
    base = HubConfig(root=root)
    known = {f.name: f.default for f in fields(HubConfig)}
    values: dict = {}
    path = Path(file) if file else base.state_dir / "hub.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: hub settings are a JSON object")
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise ValueError(f"{path}: unknown hub setting(s): {', '.join(unknown)}")
        values.update({k: _coerce(k, v, known[k]) for k, v in data.items()})
    for name, default in known.items():
        key = f"AGENTDIFF_HUB_{name.upper()}"
        if key in env:
            values[name] = _coerce(name, env[key], default)
    for name, value in (overrides or {}).items():
        if value is not None:
            if name not in known:
                raise ValueError(f"unknown hub setting {name!r}")
            values[name] = value
    values.setdefault("root", root)
    return replace(base, **values)
