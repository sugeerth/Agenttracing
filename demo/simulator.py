"""Simulator framework for authoring scripted agent trajectories.

Produces trajectory dicts that exactly match SCHEMA.md (v1) and writes them
as pretty JSON. Pure stdlib, deterministic: every random source is seeded
with a constant derived from the trace id, and no wall-clock time is used.

Token model
-----------
Tokens are estimated as ``round(len(text) * TOKENS_PER_CHAR) + overhead``.
For accounting, text the agent *writes* (plans, queries, tool args,
reasoning, answers) counts as output tokens; text the agent *observes*
(search results, retrieved snippets, page contents, tool results) counts as
input tokens, plus a fixed per-step context-window overhead the model
re-reads on every step.

Cost model (per SCHEMA.md demo contract)
----------------------------------------
cost_usd = input_tokens * 3e-6 + output_tokens * 15e-6
"""

from __future__ import annotations

import json
import random
import zlib
from pathlib import Path

SCHEMA_VERSION = 1

STEP_TYPES = ("plan", "search", "retrieve", "read", "tool_call", "reason", "answer")

# ---- token model -----------------------------------------------------------
TOKENS_PER_CHAR = 0.25          # ~4 chars per token
INPUT_OVERHEAD_PER_STEP = 90    # context re-read / message framing per step
OUTPUT_OVERHEAD_PER_STEP = 8    # stop sequences, role tags, etc.

# Step types whose *output* field is produced by the agent itself; for all
# other types the output field is an observation coming back from the world.
_AGENT_AUTHORS_OUTPUT = {"plan", "reason", "answer"}

# ---- cost model ------------------------------------------------------------
COST_PER_INPUT_TOKEN = 3e-6
COST_PER_OUTPUT_TOKEN = 15e-6

# ---- latency model ---------------------------------------------------------
BASE_LATENCY_S = {
    "plan": 0.40,
    "search": 1.90,     # network round trip to a search backend
    "retrieve": 0.30,
    "read": 1.30,       # fetch + parse a document
    "tool_call": 0.80,
    "reason": 0.35,
    "answer": 0.50,
}
SECONDS_PER_OUTPUT_TOKEN = 0.021   # ~48 tok/s decode speed
SECONDS_PER_INPUT_KTOKEN = 0.09    # prefill cost per 1k observed tokens


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: roughly proportional to text length."""
    if not text:
        return 0
    return max(1, round(len(text) * TOKENS_PER_CHAR))


class TrajectoryBuilder:
    """Fluent builder for a single agent trajectory.

    Usage::

        t = TrajectoryBuilder(agent={"name": ..., "model": ..., "version": ...},
                              task={"id": ..., "prompt": ..., "expected": ...})
        t.plan("1) ... 2) ...")
        t.search("acme fy2025 revenue", "[1] ir.acmecorp.com ...")
        t.answer("The revenue was ...", success=True)
        traj = t.build()
    """

    def __init__(self, agent: dict, task: dict):
        self.agent = {
            "name": agent["name"],
            "model": agent["model"],
            "version": agent["version"],
        }
        self.task = {
            "id": task["id"],
            "prompt": task["prompt"],
            "expected": task.get("expected"),
        }
        self.trace_id = f"{self.task['id']}__{self.agent['name']}"
        # Constant seed derived from the trace id -> stable across runs.
        self._rng = random.Random(zlib.crc32(self.trace_id.encode("utf-8")))
        self.steps: list[dict] = []
        self.outcome: dict | None = None
        self._input_tokens = 0
        self._output_tokens = 0
        self._latency_s = 0.0

    # ---- core step author --------------------------------------------------

    def step(
        self,
        type: str,
        name: str,
        input: str,
        output: str,
        quality: str | None = "good",
        note: str | None = None,
    ) -> "TrajectoryBuilder":
        if type not in STEP_TYPES:
            raise ValueError(f"unknown step type: {type!r}")
        if quality not in ("good", "weak", "bad", None):
            raise ValueError(f"unknown quality: {quality!r}")

        if type in _AGENT_AUTHORS_OUTPUT:
            authored, observed = input + output, ""
        else:
            authored, observed = input, output

        out_tok = estimate_tokens(authored) + OUTPUT_OVERHEAD_PER_STEP
        in_tok = estimate_tokens(observed) + INPUT_OVERHEAD_PER_STEP

        latency = (
            BASE_LATENCY_S[type]
            + out_tok * SECONDS_PER_OUTPUT_TOKEN
            + (in_tok / 1000.0) * SECONDS_PER_INPUT_KTOKEN
            + self._rng.uniform(0.02, 0.35)
        )
        latency = round(latency, 2)

        self.steps.append(
            {
                "index": len(self.steps),
                "type": type,
                "name": name,
                "input": input,
                "output": output,
                "tokens": in_tok + out_tok,
                "latency_s": latency,
                "quality": quality,
                "note": note,
            }
        )
        self._input_tokens += in_tok
        self._output_tokens += out_tok
        self._latency_s += latency
        return self

    # ---- convenience authors (one per schema step type) --------------------

    def plan(self, plan_text: str, output: str = "Plan set. Executing step 1.", **kw):
        return self.step("plan", kw.pop("name", "plan"), plan_text, output, **kw)

    def search(self, query: str, results: str, name: str = "web_search", **kw):
        return self.step("search", name, query, results, **kw)

    def retrieve(self, selection: str, result: str, name: str = "select_result", **kw):
        return self.step("retrieve", name, selection, result, **kw)

    def read(self, source: str, content: str, name: str = "open_page", **kw):
        return self.step("read", name, source, content, **kw)

    def tool_call(self, name: str, args: str, result: str, **kw):
        return self.step("tool_call", name, args, result, **kw)

    def reason(self, about: str, thought: str, name: str = "reason", **kw):
        return self.step("reason", name, about, thought, **kw)

    def answer(
        self,
        text: str,
        success: bool,
        score: float | None = None,
        input: str = "Compose the final answer from the verified findings.",
        **kw,
    ):
        self.step("answer", kw.pop("name", "final_answer"), input, text, **kw)
        self.outcome = {
            "success": bool(success),
            "answer": text,
            "score": float(score if score is not None else (1.0 if success else 0.0)),
        }
        return self

    # ---- assembly ----------------------------------------------------------

    def build(self) -> dict:
        if self.outcome is None:
            raise ValueError(f"{self.trace_id}: trajectory has no answer step")
        if self.steps[-1]["type"] != "answer":
            raise ValueError(f"{self.trace_id}: last step must be type 'answer'")
        traj = {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "agent": self.agent,
            "task": self.task,
            "outcome": self.outcome,
            "totals": {
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "cost_usd": round(
                    self._input_tokens * COST_PER_INPUT_TOKEN
                    + self._output_tokens * COST_PER_OUTPUT_TOKEN,
                    6,
                ),
                "latency_s": round(self._latency_s, 2),
            },
            "steps": self.steps,
        }
        validate_trajectory(traj)
        return traj


def validate_trajectory(traj: dict) -> None:
    """Light structural check against SCHEMA.md v1. Raises ValueError."""
    for key in ("schema_version", "trace_id", "agent", "task", "outcome", "totals", "steps"):
        if key not in traj:
            raise ValueError(f"trajectory missing key: {key}")
    for key in ("name", "model", "version"):
        if key not in traj["agent"]:
            raise ValueError(f"agent missing key: {key}")
    for key in ("id", "prompt", "expected"):
        if key not in traj["task"]:
            raise ValueError(f"task missing key: {key}")
    for key in ("success", "answer", "score"):
        if key not in traj["outcome"]:
            raise ValueError(f"outcome missing key: {key}")
    for key in ("input_tokens", "output_tokens", "cost_usd", "latency_s"):
        if key not in traj["totals"]:
            raise ValueError(f"totals missing key: {key}")
    if not traj["steps"]:
        raise ValueError("trajectory has no steps")
    for i, s in enumerate(traj["steps"]):
        for key in ("index", "type", "name", "input", "output", "tokens", "latency_s", "quality", "note"):
            if key not in s:
                raise ValueError(f"step {i} missing key: {key}")
        if s["index"] != i:
            raise ValueError(f"step {i} has index {s['index']}")
        if s["type"] not in STEP_TYPES:
            raise ValueError(f"step {i} has unknown type {s['type']!r}")
    if traj["steps"][-1]["type"] != "answer":
        raise ValueError("last step must be type 'answer'")


def write_trajectory(traj: dict, path: str | Path) -> Path:
    """Write a trajectory as pretty, stable JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(traj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
