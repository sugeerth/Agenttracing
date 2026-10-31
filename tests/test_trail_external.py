"""The TRAIL converter (demo/external/trail.py): a span tree's LLM and TOOL spans and a code agent's executed
steps become the trace's steps in order; an annotated error lands on its step, never on the trace; and the
reasoning step that decided a tool call (whowhen.decided_by) is scored beside the reading's pointer.

The fixture is hand-written in TRAIL's shape and labelled synthetic; the real corpus is not in this repository."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "external"))

from agentdiff.trace import Trajectory  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "demo" / "external" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trail = _load("trail")
whowhen = _load("whowhen")


def _span(span_id, kind=None, name="span", children=(), **attrs):
    a = dict(attrs)
    if kind:
        a["openinference.span.kind"] = kind
    return {"span_id": span_id, "span_name": name, "duration": "PT1.5S", "status_code": "Ok",
            "span_attributes": a, "child_spans": list(children)}


def _doc(i=0):
    """A code agent: plan, then one step whose LLM call wrote code that ran, then a sub-agent's final_answer tool
    and the run's own final answer."""
    step = _span("chain1", "CHAIN", "Step 1", [
        _span("llm2", "LLM", "LiteLLMModel.__call__", **{"llm.output_messages.0.message.content": "Thought: search"}),
        _span("tool3", "TOOL", "web_search", **{"tool.name": "web_search", "input.value": "q", "output.value": "r"}),
    ], **{"output.value": "Execution logs:\nError: page not found"})
    root = _span("root", "AGENT", "CodeAgent.run", [
        _span("llm1", "LLM", "LiteLLMModel.__call__", **{"llm.output_messages.0.message.content": "Plan"}),
        step,
        _span("tool4", "TOOL", "final_answer", **{"tool.name": "final_answer", "input.value": "sub-agent result"}),
        _span("llm5", "LLM", "LiteLLMModel.__call__", **{"llm.output_messages.0.message.content": f"FINAL {i}"}),
    ], **{"input.value": "the task"})
    return {"trace_id": f"t{i}", "spans": [root]}


ANNOTATION = {"errors": [{"category": "Tool-related", "location": "chain1", "impact": "HIGH"},
                         {"category": "Goal Deviation", "location": "llm5", "impact": "LOW"},
                         {"category": "Formatting", "location": "nowhere", "impact": "LOW"}]}


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.trace, self.labels = trail.convert(_doc(), ANNOTATION, "t0")
        self.traj = Trajectory.from_dict(self.trace)

    def test_steps_in_order_with_the_executed_code(self):
        self.assertEqual([(s.type, s.name) for s in self.traj.steps],
                         [("reason", "LiteLLMModel.__call__"), ("reason", "LiteLLMModel.__call__"),
                          ("tool_call", "web_search"), ("tool_call", "code_execution"),
                          ("tool_call", "final_answer"), ("answer", "LiteLLMModel.__call__")])
        self.assertTrue(self.traj.steps[3].error, "the execution log reports an error")
        self.assertEqual(self.traj.steps[4].type, "tool_call", "a sub-agent's final_answer is not the run's answer")

    def test_errors_land_on_steps_and_stay_off_the_trace(self):
        self.assertEqual([e["step"] for e in self.labels["errors"]], [1, 5], "a container's error: its first step")
        self.assertEqual(self.labels["first_error"], 1)
        self.assertEqual(self.labels["unplaced_errors"], 1)
        text = json.dumps(self.trace)
        for word in ("Tool-related", "Goal Deviation", "chain1", "llm5"):
            self.assertNotIn(word, text)

    def test_decided_by_is_the_reasoning_step_behind_a_tool_call(self):
        self.assertEqual(whowhen.decided_by(self.traj, 3), 1, "back across consecutive tool calls")
        self.assertEqual(whowhen.decided_by(self.traj, 1), 1)
        self.assertIsNone(whowhen.decided_by(self.traj, None))


class MainTest(unittest.TestCase):
    def test_the_validation_scores_both_policies_beside_the_floors(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "benchmarking"
            for sub, ann in trail.SUBSETS.items():
                (data / "data" / sub).mkdir(parents=True)
                (data / ann).mkdir(parents=True)
            for i in range(4):
                (data / "data" / "GAIA" / f"{i}.json").write_text(json.dumps(_doc(i)))
                (data / "processed_annotations_gaia" / f"{i}.json").write_text(json.dumps(ANNOTATION))
            (data / "data" / "GAIA" / "bad.json").write_text("{not json")
            (data / "processed_annotations_gaia" / "bad.json").write_text("{}")
            with redirect_stdout(io.StringIO()) as out:
                trail.main(["--data", str(data), "-o", str(Path(tmp) / "out")])
            result = json.loads((Path(tmp) / "out" / "trail_validation.json").read_text())
        self.assertEqual(result["traces"], 4)
        self.assertEqual(result["skipped"], {"not valid JSON": 1})
        self.assertIn("always_first_step", result["floors"])
        self.assertIn("first_error_exact", result["decided_by"])
        self.assertIn("the reasoning step that decided it", out.getvalue())
        self.assertIn("recorded as failing", result["note"])


if __name__ == "__main__":
    unittest.main()
