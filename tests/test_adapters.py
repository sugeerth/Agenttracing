"""Tests for the foreign-trace adapters (SCHEMA.md v9).

These cover the shapes real exporters actually emit — OTLP camelCase
timestamps, list-form attributes, and the several spellings vendors use for
tool arguments and results — because a key miss there silently produces
empty steps that corrupt every downstream analysis.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.adapters import from_openai_messages, from_otel_genai

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestOtelAdapter(unittest.TestCase):
    def convert_fixture(self):
        data = load_fixture("otel_sample.json")
        meta = data["meta"]
        return from_otel_genai(
            data["spans"],
            agent=meta["agent"],
            task=meta["task"],
            outcome={"success": meta["success"], "answer": meta["answer"]},
        )

    def test_fixture_converts_and_validates(self):
        traj, warnings = self.convert_fixture()
        Trajectory.from_json(traj)  # raises on schema violation
        self.assertEqual(warnings, [])
        self.assertEqual(traj["agent"]["name"], "bolt-v3")
        self.assertEqual(traj["steps"][-1]["type"], "answer")

    def test_camel_case_timestamps_produce_latency(self):
        # OTLP JSON exports use camelCase; snake_case-only parsing would
        # silently zero every step's latency and destroy the ordering key.
        traj, _ = self.convert_fixture()
        latencies = [s["latency_s"] for s in traj["steps"]]
        self.assertTrue(all(l > 0 for l in latencies), latencies)
        self.assertAlmostEqual(traj["totals"]["latency_s"], sum(latencies), places=3)

    def test_tool_arguments_and_results_are_captured(self):
        # gen_ai.tool.call.arguments / .result is the semantic-convention
        # spelling; dropping it leaves steps with empty text.
        traj, _ = self.convert_fixture()
        tool_steps = [s for s in traj["steps"] if s["type"] in ("search", "read")]
        self.assertTrue(tool_steps)
        for step in tool_steps:
            self.assertTrue(step["input"].strip(), step)
            self.assertTrue(step["output"].strip(), step)
        self.assertIn("financeblog.net", tool_steps[-1]["input"])

    def test_step_types_from_tool_names(self):
        traj, _ = self.convert_fixture()
        by_name = {s["name"]: s["type"] for s in traj["steps"]}
        self.assertEqual(by_name["web_search"], "search")
        self.assertEqual(by_name["fetch_page"], "read")

    def test_otlp_list_attributes_and_string_timestamps(self):
        # The OTLP wire form: attributes as a list of {key, value:{...}}
        # entries and timestamps as strings.
        spans = [
            {
                "name": "execute_tool web_search",
                "startTimeUnixNano": "1000000000",
                "endTimeUnixNano": "2500000000",
                "attributes": [
                    {"key": "gen_ai.operation.name",
                     "value": {"stringValue": "execute_tool"}},
                    {"key": "gen_ai.tool.name", "value": {"stringValue": "web_search"}},
                    {"key": "gen_ai.tool.call.arguments",
                     "value": {"stringValue": "acme fy2025 revenue"}},
                    {"key": "gen_ai.tool.call.result",
                     "value": {"stringValue": "ir.acmecorp.com results"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 10}},
                ],
            },
            {
                "name": "chat",
                "startTimeUnixNano": "2500000000",
                "endTimeUnixNano": "3500000000",
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.completion",
                     "value": {"stringValue": "Revenue was $4.82 billion."}},
                ],
            },
        ]
        traj, warnings = from_otel_genai(spans, agent="a1", task="t1")
        Trajectory.from_json(traj)
        self.assertEqual(warnings, [])
        self.assertEqual(traj["steps"][0]["type"], "search")
        self.assertEqual(traj["steps"][0]["input"], "acme fy2025 revenue")
        self.assertAlmostEqual(traj["steps"][0]["latency_s"], 1.5, places=3)
        self.assertEqual(traj["steps"][-1]["type"], "answer")

    def test_unmapped_span_warns_rather_than_fails(self):
        spans = [
            {"name": "database.query", "startTimeUnixNano": 0,
             "endTimeUnixNano": 1000000, "attributes": {"db.system": "postgres"}},
            {"name": "chat", "startTimeUnixNano": 1000000, "endTimeUnixNano": 2000000,
             "attributes": {"gen_ai.operation.name": "chat",
                            "gen_ai.completion": "done"}},
        ]
        traj, warnings = from_otel_genai(spans, agent="a1", task="t1")
        Trajectory.from_json(traj)
        self.assertEqual(len(warnings), 1)
        self.assertIn("database.query", warnings[0])
        self.assertEqual(len(traj["steps"]), 1)

    def test_no_mappable_spans_raises(self):
        with self.assertRaises(ValueError):
            from_otel_genai([{"name": "db.query", "attributes": {}}],
                            agent="a1", task="t1")


class TestOpenAIAdapter(unittest.TestCase):
    def convert_fixture(self):
        data = load_fixture("openai_sample.json")
        return from_openai_messages(data["messages"], data["meta"])

    def test_fixture_converts_and_validates(self):
        traj, warnings = self.convert_fixture()
        Trajectory.from_json(traj)
        self.assertEqual(warnings, [])
        self.assertEqual(traj["agent"]["name"], "atlas-v2")
        self.assertTrue(traj["outcome"]["success"])

    def test_tool_calls_typed_and_results_attached(self):
        traj, _ = self.convert_fixture()
        types = [s["type"] for s in traj["steps"]]
        self.assertIn("search", types)
        self.assertIn("read", types)
        self.assertEqual(types[-1], "answer")
        search = next(s for s in traj["steps"] if s["type"] == "search")
        self.assertIn("ACME", search["input"])
        self.assertIn("ir.acmecorp.com", search["output"])

    def test_task_prompt_from_first_user_message(self):
        traj, _ = self.convert_fixture()
        self.assertIn("ACME", traj["task"]["prompt"])


class TestCrossFormatComparison(unittest.TestCase):
    """The point of adapters: traces from different stacks compare cleanly."""

    def test_openai_vs_otel_compare(self):
        otel_data = load_fixture("otel_sample.json")
        otel_meta = otel_data["meta"]
        b_dict, _ = from_otel_genai(
            otel_data["spans"], agent=otel_meta["agent"], task=otel_meta["task"],
            outcome={"success": otel_meta["success"], "answer": otel_meta["answer"]},
        )
        oai_data = load_fixture("openai_sample.json")
        a_dict, _ = from_openai_messages(oai_data["messages"], oai_data["meta"])

        report = compare(Trajectory.from_json(a_dict), Trajectory.from_json(b_dict))
        self.assertEqual(report["attribution"]["failed_agent"], "b")
        # The wrong figure must surface as a semantic claim conflict.
        conflicts = report.get("semantic", {}).get("conflicts", [])
        self.assertTrue(any(c.get("kind") == "money" for c in conflicts), conflicts)


if __name__ == "__main__":
    unittest.main()
