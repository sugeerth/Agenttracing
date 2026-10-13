"""Which agent framework and which protocols a SCHEMA trace came through.

A trace does not say what produced it, but the conventions the frameworks
use leak into it: an MCP tool is named ``mcp__<server>__<tool>`` (Claude
Code, the Claude Agent SDK, the hooks' matchers); an OpenAI Agents SDK
handoff is a tool call named ``transfer_to_<agent>``; Claude Code's own
tools are the capitalised ``Bash`` / ``Read`` / ``Edit`` / ``Agent`` set;
a converter or harness that knows its source stamps ``harness.adapter``
or ``source.format``. :func:`detect` reads those and nothing else.

Conservative by design: a plain tool name never names a framework, an
unknown trace returns ``framework: None``, and the function never raises
— an unreadable input is reported as a signal, not an exception. Every
signal that fed a verdict is listed so a reader can check it.

Deterministic: no randomness, no network, no model.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: tool-ish step types: the ones whose names are tool names.
TOOLISH = ("tool_call", "search", "read", "retrieve")

#: Claude Code's built-in tools. Capitalised names are the convention; two
#: distinct ones from the core set are a medium signal, the adapter or a
#: source stamp makes it high.
CLAUDE_CODE_CORE = frozenset({"Bash", "Read", "Edit", "Write", "MultiEdit", "Glob", "Grep",
                              "Agent", "Task", "NotebookEdit", "LS"})
CLAUDE_CODE_OTHER = frozenset({"WebFetch", "WebSearch", "TodoWrite", "TodoRead", "Skill",
                               "AskUserQuestion", "ExitPlanMode", "EnterPlanMode", "KillShell",
                               "BashOutput", "SlashCommand"})

#: explicit framework markers, matched as substrings of ``harness.adapter``
#: and ``source.format`` (high) or of ``agent.version`` / ``agent.name``
#: (low). Order is the deterministic tiebreak.
FRAMEWORK_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("claude-code", ("claude-code", "claude_code", "claude-agent-sdk", "claude_agent_sdk")),
    ("openai-agents", ("openai-agents", "openai_agents", "agents-sdk", "agents_sdk")),
    ("langgraph", ("langgraph", "langchain")),
    ("google-adk", ("google-adk", "google_adk", "adk", "gcp.vertex.agent")),
    ("autogen", ("autogen", "ag2", "microsoft-agent-framework")),
    ("crewai", ("crewai", "crew-ai", "crew_ai")),
    ("pydantic-ai", ("pydantic-ai", "pydantic_ai", "pydanticai")),
    ("temporal", ("temporal",)),
    ("smolagents", ("smolagents",)),
]

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}

_MCP_RE = re.compile(r"^mcp__(?P<server>.+?)__(?P<tool>[^_].*)$")
_DOTTED_RE = re.compile(r"^(?P<server>[A-Za-z][\w-]*)\.(?P<tool>[A-Za-z_][\w-]*)$")
_HANDOFF_RE = re.compile(r"^transfer_to_(?P<agent>.+)$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DENIED_RE = re.compile(r"permission(?:decision)?\W{0,3}(?:den|deny|denied)|denied by (?:the )?(?:user|permission|policy|hook)"
                        r"|user (?:denied|declined|rejected)|not permitted|blocked by (?:a )?(?:hook|policy|guardrail)",
                        re.IGNORECASE)
_ASKED_RE = re.compile(r"permission(?:decision)?\W{0,3}ask|asked (?:the user )?for permission|permission prompt"
                       r"|requires? (?:user )?approval|awaiting approval|approval requested", re.IGNORECASE)
_GUARDRAIL_RE = re.compile(r"guardrail", re.IGNORECASE)


def _empty() -> dict:
    return {"framework": None, "confidence": None, "signals": [], "mcp_servers": [],
            "handoffs": 0, "guardrails": 0, "protocols": [], "permissions": {"asked": 0, "denied": 0}}


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _steps(run: dict) -> list:
    steps = run.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _tool_steps(run: dict) -> list:
    return [s for s in _steps(run) if s.get("type") in TOOLISH]


def _declared_mcp(run: dict) -> dict:
    """Tool declarations that mark themselves MCP: name -> server (or "")."""
    out: dict = {}
    tools = run.get("tools")
    if not isinstance(tools, list):
        return out
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = _str(t.get("name"))
        if not name:
            continue
        mcp = t.get("mcp")
        server = t.get("server") or t.get("mcp_server") or (mcp.get("server") if isinstance(mcp, dict) else None)
        if server or mcp is True or _str(t.get("protocol")).lower() == "mcp":
            out[name] = _str(server)
    return out


def _mcp_servers(run: dict, signals: list) -> list:
    servers: dict = {}
    declared = _declared_mcp(run)
    for s in _tool_steps(run):
        name = _str(s.get("name"))
        m = _MCP_RE.match(name)
        if m:
            servers.setdefault(m.group("server"), 0)
            servers[m.group("server")] += 1
            continue
        # an explicit per-step marker (a recorder that knows the server)
        mcp = s.get("mcp")
        if isinstance(mcp, dict) and mcp.get("server"):
            servers[_str(mcp["server"])] = servers.get(_str(mcp["server"]), 0) + 1
            continue
        if name in declared:
            # the dotted <server>.<tool> form counts only when the trace says
            # the tool is MCP-backed; a bare dotted name is just a namespace.
            dotted = _DOTTED_RE.match(name)
            server = declared[name] or (dotted.group("server") if dotted else name)
            servers[server] = servers.get(server, 0) + 1
    if servers:
        signals.append("mcp: " + ", ".join(f"{k} ×{v}" for k, v in sorted(servers.items())))
    return sorted(servers)


def _handoffs(run: dict, signals: list) -> int:
    count = 0
    steps = _steps(run)
    for pos, s in enumerate(steps):
        if s.get("type") not in TOOLISH:
            continue
        m = _HANDOFF_RE.match(_str(s.get("name")))
        if not m:
            continue
        count += 1
        target = m.group("agent")
        # did the span's agent change to the target right after the call?
        after = next((t for t in steps[pos + 1:] if isinstance(t.get("span"), dict)), None)
        before = s.get("span") if isinstance(s.get("span"), dict) else None
        if after and _str(after["span"].get("agent")).lower() == target.lower() and (not before or before.get("agent") != after["span"].get("agent")):
            signals.append(f"handoff at step {s.get('index', pos)}: span agent became {target!r} after transfer_to_{target}")
        else:
            signals.append(f"handoff at step {s.get('index', pos)}: transfer_to_{target}")
    return count


def _guardrails(run: dict, signals: list) -> int:
    count = 0
    for s in _steps(run):
        name = _str(s.get("name"))
        if s.get("type") in TOOLISH and _GUARDRAIL_RE.search(name):
            count += 1
            signals.append(f"guardrail at step {s.get('index')}: {name}")
        elif isinstance(s.get("guardrail"), dict):
            count += 1
            signals.append(f"guardrail at step {s.get('index')}: " + (_str(s["guardrail"].get("name")) or "unnamed")
                           + (" (triggered)" if s["guardrail"].get("triggered") else ""))
    return count


def _permissions(run: dict, signals: list) -> dict:
    asked = denied = 0
    for s in _steps(run):
        perm = s.get("permission")
        if isinstance(perm, dict):
            decision = _str(perm.get("decision") or perm.get("permissionDecision")).lower()
            if decision == "deny":
                denied += 1
                signals.append(f"permission denied at step {s.get('index')}")
                continue
            if decision == "ask":
                asked += 1
                signals.append(f"permission asked at step {s.get('index')}")
                continue
        text = " ".join(_str(s.get(k)) for k in ("note", "output"))
        if not text:
            continue
        if _DENIED_RE.search(text):
            denied += 1
            signals.append(f"permission denied at step {s.get('index')} (from the step's text)")
        elif _ASKED_RE.search(text):
            asked += 1
            signals.append(f"permission asked at step {s.get('index')} (from the step's text)")
    return {"asked": asked, "denied": denied}


def _a2a(run: dict, signals: list) -> bool:
    """Spans naming a remote agent by URL, or an explicit A2A marker."""
    found = False
    for s in _steps(run):
        span = s.get("span")
        if not isinstance(span, dict):
            continue
        agent = _str(span.get("agent"))
        url = _str(span.get("url") or span.get("agent_url") or span.get("endpoint"))
        if _URL_RE.match(agent) or _URL_RE.match(url) or span.get("agent_card"):
            found = True
            signals.append(f"a2a: remote agent {agent or url!r} at step {s.get('index')}")
            break
        if _str(span.get("protocol")).lower() == "a2a":
            found = True
            signals.append(f"a2a: span at step {s.get('index')} declares the protocol")
            break
    return found


def _markers(run: dict) -> tuple[list, list]:
    """(strong, weak) marker texts: adapter/source stamps vs identity fields."""
    harness = run.get("harness") if isinstance(run.get("harness"), dict) else {}
    source = run.get("source") if isinstance(run.get("source"), dict) else {}
    agent = run.get("agent") if isinstance(run.get("agent"), dict) else {}
    strong = [_str(harness.get("adapter")), _str(harness.get("framework")), _str(source.get("format")),
              _str(source.get("framework")), _str(run.get("framework"))]
    weak = [_str(agent.get("version")), _str(agent.get("name")), _str(agent.get("framework"))]
    return [m.lower() for m in strong if m], [m.lower() for m in weak if m]


def _claude_code_tools(run: dict) -> tuple[set, set]:
    names = {_str(s.get("name")) for s in _tool_steps(run)}
    return names & CLAUDE_CODE_CORE, names & CLAUDE_CODE_OTHER


def detect(run: dict) -> dict:
    """Framework, confidence, protocols, MCP servers, handoffs and permission
    counts for one SCHEMA trace dict. Never raises; ``framework`` is None
    when no convention matched."""
    out = _empty()
    try:
        if not isinstance(run, dict):
            out["signals"].append("not a trace dict")
            return out
        signals = out["signals"]
        candidates: list[tuple[str, str, str]] = []   # (framework, confidence, why)

        # --- explicit stamps
        strong, weak = _markers(run)
        for name, needles in FRAMEWORK_MARKERS:
            hit = next((m for m in strong if any(n in m for n in needles)), None)
            if hit:
                candidates.append((name, "high", f"adapter/source says {hit!r}"))
                continue
            hit = next((m for m in weak if any(n in m for n in needles)), None)
            if hit:
                candidates.append((name, "low", f"agent identity says {hit!r}"))
        if any(m in ("otel", "otel-genai", "opentelemetry", "otlp") or m.startswith("otel") for m in strong):
            out["protocols"].append("otel-genai")
            signals.append("adapter/source is OpenTelemetry GenAI")

        # --- conventions in the steps
        out["mcp_servers"] = _mcp_servers(run, signals)
        if out["mcp_servers"]:
            out["protocols"].append("mcp")
        out["handoffs"] = _handoffs(run, signals)
        out["guardrails"] = _guardrails(run, signals)
        out["permissions"] = _permissions(run, signals)
        if _a2a(run, signals):
            out["protocols"].append("a2a")

        core, other = _claude_code_tools(run)
        if len(core) >= 2:
            explicit = any(c[0] == "claude-code" for c in candidates)
            conf = "high" if explicit else "medium"
            candidates.append(("claude-code", conf, "tool set " + ", ".join(sorted(core | other))))
        elif core and any(c[0] == "claude-code" for c in candidates):
            candidates.append(("claude-code", "high", "tool " + ", ".join(sorted(core))))
        if out["handoffs"] and not any(c[0] == "openai-agents" and c[1] == "high" for c in candidates):
            candidates.append(("openai-agents", "medium", f"{out['handoffs']} transfer_to_* handoff(s)"))
        if out["guardrails"] and any(c[0] == "openai-agents" for c in candidates):
            candidates.append(("openai-agents", "medium", f"{out['guardrails']} guardrail step(s)"))

        # --- the verdict: the best-supported framework, ties by marker order
        order = [name for name, _ in FRAMEWORK_MARKERS]
        best: Optional[tuple[str, str]] = None
        for name, conf, why in candidates:
            signals.append(f"{name} ({conf}): {why}")
            if best is None or _CONF_RANK[conf] > _CONF_RANK[best[1]] or (
                    _CONF_RANK[conf] == _CONF_RANK[best[1]] and order.index(name) < order.index(best[0])):
                best = (name, conf)
        if best:
            out["framework"], out["confidence"] = best
            # two independent signals for one framework lift it one notch
            distinct = {why for n, _, why in candidates if n == best[0]}
            if len(distinct) >= 2 and best[1] != "high":
                out["confidence"] = "high" if best[1] == "medium" else "medium"
                signals.append(f"{best[0]}: {len(distinct)} independent signals, confidence raised to {out['confidence']}")
        out["protocols"] = sorted(set(out["protocols"]))
        return out
    except Exception as exc:  # noqa: BLE001 — detection must never break a pipeline
        fallback = _empty()
        fallback["signals"].append(f"detection failed: {type(exc).__name__}: {exc}")
        return fallback


def render_line(path: str, det: dict, dom: Optional[dict] = None) -> str:
    """One terminal line per trace, the shape the CLI prints."""
    fw = f"{det['framework']} ({det['confidence']})" if det.get("framework") else "unknown"
    parts = [f"framework={fw}",
             "protocols=" + (",".join(det.get("protocols") or []) or "-"),
             "mcp=" + (",".join(det.get("mcp_servers") or []) or "-"),
             f"handoffs={det.get('handoffs', 0)}"]
    perms = det.get("permissions") or {}
    if perms.get("asked") or perms.get("denied"):
        parts.append(f"permissions=asked:{perms.get('asked', 0)}/denied:{perms.get('denied', 0)}")
    if dom is not None:
        parts.append("domain=" + (f"{dom['domain']} ({dom['confidence']})" if dom.get("domain") else "unknown"))
    return f"{path}: " + " ".join(parts)


__all__ = ["detect", "render_line", "FRAMEWORK_MARKERS", "CLAUDE_CODE_CORE", "TOOLISH"]
