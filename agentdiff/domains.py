"""Built-in domain specifications: what a good run in a domain is expected
to do, so a golden task can say ``"domain": "coding"`` instead of spelling
every rule out.

The five domains are the ones the 2025–26 agent benchmarks and products
organise themselves around — coding (SWE-bench, Terminal-Bench, Claude
Code), deep research (BrowseComp, GAIA), customer support (τ-bench's
tool–user–policy domains), data/analytics, and computer use (OSWorld).
Each spec names the tool *families* a run in that domain is expected to
draw on (as lower-case ``fnmatch`` patterns over tool names), the process
rules that make sense there (read before write, verify after write), the
tools a run must not touch, the tools that leave the sandbox, stop rules,
and milestone templates a task author fills with evidence.

:func:`infer` guesses a run's domain from the tools it used — a share of
families matched, reported with its confidence, ``None`` when nothing
matched. :func:`apply` completes a golden task from a spec without
overriding anything the task states. Deterministic, stdlib only.
"""

from __future__ import annotations

import copy
from fnmatch import fnmatchcase
from typing import Optional

TOOLISH = ("tool_call", "search", "read", "retrieve")

SPECS: dict = {
    "coding": {
        "name": "coding",
        "description": "Software engineering in a repository: read the code, change it, run the checks (SWE-bench, Terminal-Bench, Claude Code).",
        "expected_tool_families": {
            "read_code": ["read_file", "read", "cat", "view", "view_file", "open_file", "glob", "ls", "list_dir", "list_files", "tree"],
            "search_code": ["grep", "rg", "search_code", "code_search", "find", "find_files", "search_files", "*symbol*"],
            "edit": ["write_file", "write", "edit", "edit_file", "multiedit", "str_replace*", "apply_patch", "patch", "create_file", "notebookedit", "insert"],
            "run": ["bash", "shell", "run", "exec", "execute", "run_command", "terminal", "python", "run_python", "execute_python"],
            "test": ["run_tests", "test", "pytest", "npm_test", "lint", "typecheck", "build", "compile", "make"],
            "vcs": ["git*", "gh*", "commit", "diff", "ci_get_log", "get_ci_log", "create_pull_request", "*pull_request*"],
        },
        "must_read_before_write": True,
        "verify_after_write": True,
        "forbidden_tools": ["send_email", "delete_repository", "force_push", "drop_database"],
        "external_tools": ["web_search", "webfetch", "websearch", "open_page", "fetch_url", "http_get"],
        "stop_rules": {"max_identical_retries": 3, "must_answer": True},
        "milestone_templates": [
            {"id": "located", "label": "the relevant code located", "evidence_hint": "the file or symbol named in the task appears in a read or search output"},
            {"id": "changed", "label": "a change written", "evidence_hint": "a write/edit step whose input names the file"},
            {"id": "verified", "label": "the checks pass after the change", "evidence_hint": "a test or lint output saying passed / 0 failed, after the last write"},
        ],
    },
    "research": {
        "name": "research",
        "description": "Deep research: search the web, open sources, extract facts, answer with citations (BrowseComp, GAIA, DRBench).",
        "expected_tool_families": {
            "search": ["web_search", "websearch", "search", "google", "bing", "search_web", "scholar_search", "arxiv_search", "search_*"],
            "browse": ["open_page", "open_url", "fetch", "fetch_url", "webfetch", "browse", "visit", "visit_page", "read_url", "get_page", "http_get", "scrape", "select_result", "click_result"],
            "extract": ["extract", "summarize", "summarise", "regex_extract", "parse", "read_pdf", "pdf_*"],
            "compute": ["calculator", "calc", "datetime_diff", "date_diff", "convert_units", "python", "execute_python"],
        },
        "must_read_before_write": False,
        "verify_after_write": False,
        "forbidden_tools": ["write_file", "delete_file", "send_email", "shell", "bash"],
        "external_tools": ["web_search", "open_page", "fetch_url", "webfetch", "websearch", "browse", "visit"],
        "stop_rules": {"max_identical_retries": 2, "must_answer": True},
        "milestone_templates": [
            {"id": "source_found", "label": "a primary source opened", "evidence_hint": "a browse step whose output carries the fact asked for"},
            {"id": "corroborated", "label": "the fact corroborated by a second source", "evidence_hint": "the same value in a second, distinct source"},
            {"id": "answered", "label": "the answer states the value with its source", "evidence_hint": "the expected value and a source name in the final answer"},
        ],
    },
    "support": {
        "name": "support",
        "description": "Customer support over a database and a policy: look the user up, act on their account, follow the rules (τ-bench airline / retail / telecom).",
        "expected_tool_families": {
            "lookup": ["get_user*", "find_user*", "get_order*", "get_reservation*", "get_product*", "lookup*", "search_*", "list_*", "get_customer*", "get_account*", "get_*_details"],
            "act": ["cancel_*", "modify_*", "update_*", "book_*", "exchange_*", "return_*", "refund*", "send_certificate", "transfer_*", "create_*", "change_*"],
            "policy": ["calculate", "get_policy", "check_policy", "think"],
            "escalate": ["transfer_to_human*", "escalate*", "handoff*"],
        },
        "must_read_before_write": True,
        "verify_after_write": True,
        "forbidden_tools": ["shell", "bash", "delete_user", "delete_account", "web_search"],
        "external_tools": ["send_email", "send_sms", "transfer_to_human_agents"],
        "stop_rules": {"max_identical_retries": 2, "must_answer": True},
        "milestone_templates": [
            {"id": "identified", "label": "the user identified before any action", "evidence_hint": "a lookup output carrying the user's id before the first write"},
            {"id": "policy_checked", "label": "the policy consulted for the requested action", "evidence_hint": "a policy or calculate step before the write"},
            {"id": "acted", "label": "the requested change made", "evidence_hint": "an act step whose output confirms the new state"},
        ],
    },
    "data": {
        "name": "data",
        "description": "Data and analytics: query tables, run code over the result, chart or report it (text-to-SQL, notebook agents).",
        "expected_tool_families": {
            "query": ["sql", "run_sql", "query", "execute_query", "run_query", "bigquery*", "snowflake*", "duckdb*", "sqlite*", "db_query", "get_schema", "list_tables", "describe_table"],
            "compute": ["python", "execute_python", "run_python", "pandas*", "notebook*", "code_interpreter", "calculator", "execute_code"],
            "load": ["read_csv", "load_csv", "read_parquet", "read_excel", "load_data", "read_file", "open_file"],
            "present": ["plot", "chart", "make_chart", "plot_*", "create_chart", "render_table", "write_report", "export_*"],
        },
        "must_read_before_write": True,
        "verify_after_write": False,
        "forbidden_tools": ["drop_table", "delete_table", "truncate", "send_email", "shell"],
        "external_tools": ["web_search", "open_page", "send_email"],
        "stop_rules": {"max_identical_retries": 3, "must_answer": True},
        "milestone_templates": [
            {"id": "schema_known", "label": "the schema inspected before querying", "evidence_hint": "a schema/list_tables output before the first query"},
            {"id": "queried", "label": "the data queried", "evidence_hint": "a query output with rows"},
            {"id": "reported", "label": "the result stated with the number asked for", "evidence_hint": "the expected value in the final answer"},
        ],
    },
    "computer_use": {
        "name": "computer_use",
        "description": "Computer and browser use: see the screen, act with mouse and keyboard, check the result (OSWorld, WebArena).",
        "expected_tool_families": {
            "observe": ["screenshot", "take_screenshot", "get_screen", "computer", "accessibility_tree", "get_dom", "read_screen", "zoom"],
            "point": ["click", "left_click", "right_click", "double_click", "mouse_move", "move_mouse", "drag", "scroll", "hover", "tap"],
            "type": ["type", "type_text", "key", "keypress", "press_key", "hotkey", "send_keys"],
            "navigate": ["navigate", "goto", "open_url", "open_app", "launch", "switch_window", "back", "forward", "wait"],
        },
        "must_read_before_write": True,
        "verify_after_write": True,
        "forbidden_tools": ["shell", "bash", "delete_file", "send_email", "purchase", "submit_payment"],
        "external_tools": ["navigate", "goto", "open_url"],
        "stop_rules": {"max_identical_retries": 3, "must_answer": True},
        "milestone_templates": [
            {"id": "target_visible", "label": "the target UI element seen", "evidence_hint": "a screenshot/observe output naming the element"},
            {"id": "acted", "label": "the action performed", "evidence_hint": "a click or type step aimed at the element"},
            {"id": "confirmed", "label": "the result seen on screen", "evidence_hint": "an observe step after the last action showing the new state"},
        ],
    },
}

#: which spec keys :func:`apply` copies onto a golden task, and under what name.
_APPLY_KEYS = ("forbidden_tools", "external_tools", "expected_tool_families", "stop_rules", "milestone_templates")


def names() -> list:
    return list(SPECS)


def spec(name: str) -> dict:
    """A deep copy of the named spec; ``ValueError`` names the known ones."""
    key = str(name).strip().lower().replace("-", "_")
    if key not in SPECS:
        raise ValueError(f"unknown domain {name!r}; known: {', '.join(SPECS)}")
    return copy.deepcopy(SPECS[key])


def _tool_names(run: dict) -> list:
    steps = run.get("steps") if isinstance(run, dict) else None
    if not isinstance(steps, list):
        return []
    seen: list = []
    for s in steps:
        if isinstance(s, dict) and s.get("type") in TOOLISH:
            name = s.get("name")
            if isinstance(name, str) and name and name not in seen:
                seen.append(name)
    return seen


def _family_of(tool: str, families: dict) -> Optional[str]:
    """The first family whose patterns match the tool (case-insensitive;
    an MCP name ``mcp__server__tool`` is matched on its tool part)."""
    low = tool.lower()
    if low.startswith("mcp__") and "__" in low[5:]:
        low = low[5:].split("__", 1)[1]
    for family, patterns in families.items():
        for pat in patterns:
            if fnmatchcase(low, pat.lower()):
                return family
    return None


def match(run: dict, spec_: dict) -> dict:
    """How a run's tools fit a spec: ``{"families": {family: [tools]},
    "matched_tools", "unmatched_tools", "family_share", "tool_share"}``."""
    tools = _tool_names(run)
    families = spec_.get("expected_tool_families") or {}
    hit: dict = {}
    unmatched: list = []
    for t in tools:
        fam = _family_of(t, families)
        if fam:
            hit.setdefault(fam, []).append(t)
        else:
            unmatched.append(t)
    matched = [t for ts in hit.values() for t in ts]
    return {"families": hit, "matched_tools": matched, "unmatched_tools": unmatched,
            "family_share": round(len(hit) / len(families), 4) if families else 0.0,
            "tool_share": round(len(matched) / len(tools), 4) if tools else 0.0}


def infer(run: dict) -> dict:
    """The domain a run's tools point at: ``{"domain", "confidence",
    "signals"}``. None when no family of any spec matched. Never raises."""
    out = {"domain": None, "confidence": None, "signals": []}
    try:
        tools = _tool_names(run)
        if not tools:
            out["signals"].append("no tool-ish steps")
            return out
        scored = []
        for name in SPECS:
            m = match(run, SPECS[name])
            if m["families"]:
                scored.append((len(m["families"]), m["tool_share"], name, m))
        if not scored:
            out["signals"].append(f"none of {len(tools)} tool name(s) matched a domain family")
            return out
        # most families, then most tools; ties keep the spec order (stable sort)
        scored.sort(key=lambda x: (-x[0], -x[1]))
        fams, share, name, m = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None
        tied = runner_up is not None and runner_up[0] == fams and runner_up[1] == share
        if tied:
            conf = "low"
        elif fams >= 2 and share >= 0.6:
            conf = "high"
        elif fams >= 2 or share >= 0.5:
            conf = "medium"
        else:
            conf = "low"
        out["domain"], out["confidence"] = name, conf
        for fam, ts in m["families"].items():
            out["signals"].append(f"{name}.{fam}: " + ", ".join(ts))
        if m["unmatched_tools"]:
            out["signals"].append("unmatched: " + ", ".join(m["unmatched_tools"]))
        if runner_up:
            out["signals"].append(f"runner-up {runner_up[2]}: {runner_up[0]} famil{'y' if runner_up[0] == 1 else 'ies'}, "
                                  f"{runner_up[1]:.0%} of tools" + (" (tie)" if tied else ""))
        return out
    except Exception as exc:  # noqa: BLE001
        return {"domain": None, "confidence": None, "signals": [f"inference failed: {type(exc).__name__}: {exc}"]}


def apply(golden_task: dict, spec_: dict) -> dict:
    """The golden task completed from the spec: keys the task lacks are
    filled (``forbidden_tools``, ``external_tools``, ``expected_tool_families``,
    ``stop_rules``, ``milestone_templates``, and a ``policy`` block with
    ``write_requires_read`` / ``verify_after_write``); keys the task states
    are kept exactly. The task's ``domain`` is recorded. Returns a new dict."""
    task = copy.deepcopy(golden_task) if isinstance(golden_task, dict) else {}
    for key in _APPLY_KEYS:
        if key not in task and key in spec_:
            task[key] = copy.deepcopy(spec_[key])
    policy = task.get("policy") if isinstance(task.get("policy"), dict) else {}
    policy = dict(policy)
    policy.setdefault("write_requires_read", bool(spec_.get("must_read_before_write")))
    policy.setdefault("verify_after_write", bool(spec_.get("verify_after_write")))
    task["policy"] = policy
    task.setdefault("domain", spec_.get("name"))
    return task


__all__ = ["SPECS", "TOOLISH", "names", "spec", "match", "infer", "apply"]
