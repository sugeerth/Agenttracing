"""Tests for the pluggable trace-format registry (v17).

Format handling is where a tool meets the messy outside world, so the tests
lean on the failure modes that actually hurt: picking the wrong format
silently, converting to steps with no text (which looks like success and
poisons every downstream analysis), and being unable to add a new stack
without touching the engine.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.registry import (
    convert,
    detect_format,
    dry_run,
    formats,
    register,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestDetection(unittest.TestCase):
    def test_otel_fixture_detected(self):
        result = detect_format(fixture("otel_sample.json"))
        self.assertEqual(result["best"], "otel")
        self.assertGreater(result["confidence"], 0.5)

    def test_openai_fixture_detected(self):
        result = detect_format(fixture("openai_sample.json"))
        self.assertEqual(result["best"], "openai")
        self.assertGreater(result["confidence"], 0.5)

    def test_anthropic_content_blocks_detected(self):
        data = {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "find revenue"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "web_search",
                 "input": {"query": "acme revenue"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "ir.acmecorp.com — $4.82 billion"}]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Revenue was $4.82 billion."}]},
        ]}
        result = detect_format(data)
        self.assertEqual(result["best"], "anthropic")

    def test_schema_trajectory_detected_with_full_confidence(self):
        traj = {
            "schema_version": 1, "trace_id": "x",
            "agent": {"name": "a", "model": "m", "version": "v"},
            "task": {"id": "t", "prompt": "p", "expected": None},
            "outcome": {"success": True, "answer": "ok", "score": 1.0},
            "totals": {"input_tokens": 1, "output_tokens": 1,
                       "cost_usd": 0.0, "latency_s": 0.1},
            "steps": [{"index": 0, "type": "answer", "name": "final",
                       "input": "i", "output": "o", "tokens": 1,
                       "latency_s": 0.1, "quality": None, "note": None}],
        }
        result = detect_format(traj)
        self.assertEqual(result["best"], "schema")
        self.assertEqual(result["confidence"], 1.0)

    def test_every_candidate_is_scored_with_a_reason(self):
        result = detect_format(fixture("otel_sample.json"))
        self.assertEqual(len(result["candidates"]), len(formats()))
        for candidate in result["candidates"]:
            self.assertIn("reason", candidate)
            self.assertTrue(candidate["reason"])

    def test_candidates_are_ranked(self):
        result = detect_format(fixture("openai_sample.json"))
        scores = [c["confidence"] for c in result["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_unrecognised_payload_matches_nothing(self):
        result = detect_format({"totally": "unrelated"})
        self.assertIsNone(result["best"])

    def test_conversion_of_unknown_format_names_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            convert({"nothing": "here"})
        message = str(caught.exception)
        for name in ("otel", "openai", "anthropic", "schema"):
            self.assertIn(name, message)
        self.assertIn("register", message)


class TestConversion(unittest.TestCase):
    def test_auto_conversion_produces_a_valid_trajectory(self):
        for name in ("otel_sample.json", "openai_sample.json"):
            result = convert(fixture(name))
            Trajectory.from_json(result["trajectory"])

    def test_explicit_format_overrides_detection(self):
        result = convert(fixture("openai_sample.json"), "openai")
        self.assertEqual(result["format"], "openai")

    def test_anthropic_blocks_become_typed_steps(self):
        data = {
            "meta": {"agent": {"name": "claude-agent", "model": "claude-sonnet-5"},
                     "task": {"id": "t1", "prompt": "find revenue"},
                     "success": True},
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "find revenue"}]},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "web_search",
                     "input": {"query": "acme fy2025 revenue"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1",
                     "content": "ir.acmecorp.com reports $4.82 billion"}]},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Revenue was $4.82 billion."}]},
            ],
        }
        result = convert(data)
        self.assertEqual(result["format"], "anthropic")
        trajectory = result["trajectory"]
        Trajectory.from_json(trajectory)
        types = [s["type"] for s in trajectory["steps"]]
        self.assertIn("search", types)
        self.assertEqual(types[-1], "answer")
        search = next(s for s in trajectory["steps"] if s["type"] == "search")
        self.assertIn("acme", search["input"].lower())
        self.assertIn("4.82", search["output"])

    def test_schema_passthrough_is_lossless(self):
        original = fixture("otel_sample.json")
        converted = convert(original)["trajectory"]
        again = convert(converted)
        self.assertEqual(again["format"], "schema")
        self.assertEqual(again["trajectory"], converted)


class TestDryRun(unittest.TestCase):
    def test_reports_fidelity_counters(self):
        report = dry_run(fixture("otel_sample.json"))
        self.assertTrue(report["ok"])
        self.assertEqual(report["format"], "otel")
        self.assertGreater(report["steps"], 0)
        fidelity = report["fidelity"]
        self.assertEqual(fidelity["steps_with_text"], report["steps"])
        self.assertGreater(fidelity["steps_with_timing"], 0)

    def test_flags_a_mapping_that_produced_no_text(self):
        # The dangerous silent failure: steps exist but carry nothing to compare.
        spans = [
            {"name": "execute_tool", "startTimeUnixNano": 0,
             "endTimeUnixNano": 1,
             "attributes": {"gen_ai.operation.name": "execute_tool",
                            "gen_ai.tool.name": "mystery"}},
            {"name": "chat", "startTimeUnixNano": 1, "endTimeUnixNano": 2,
             "attributes": {"gen_ai.operation.name": "chat"}},
        ]
        report = dry_run({"spans": spans, "agent": "a", "task": "t"})
        self.assertTrue(report["ok"])
        self.assertLess(report["fidelity"]["steps_with_text"], report["steps"])
        self.assertTrue(any("no real text" in note for note in report["notes"]))
        self.assertEqual(report["fidelity"]["steps_with_output"], 0)

    def test_flags_missing_timing_and_tokens(self):
        data = {
            "meta": {"agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"}},
            "messages": [
                {"role": "user", "content": "do it"},
                {"role": "assistant", "content": "done"},
            ],
        }
        report = dry_run(data)
        self.assertTrue(any("timing" in note for note in report["notes"]))

    def test_unmatched_payload_reports_not_ok(self):
        report = dry_run({"unknown": True})
        self.assertFalse(report["ok"])
        self.assertIn("error", report)

    def test_dry_run_does_not_write_anything(self):
        # It returns a plan; the only contract is that it is pure.
        before = sorted(p.name for p in FIXTURES.iterdir())
        dry_run(fixture("otel_sample.json"))
        after = sorted(p.name for p in FIXTURES.iterdir())
        self.assertEqual(before, after)


class TestExtensibility(unittest.TestCase):
    """The point of the registry: a new stack without touching the engine."""

    def test_a_third_party_format_can_be_registered_and_used(self):
        def detect(data):
            if isinstance(data, dict) and data.get("myFormat") == 1:
                return 0.99, "myFormat marker present"
            return 0.0, "no marker"

        def convert_mine(data):
            steps = [
                {"index": i, "type": entry["kind"], "name": entry["label"],
                 "input": entry.get("in", ""), "output": entry.get("out", ""),
                 "tokens": entry.get("tok", 0), "latency_s": entry.get("sec", 0.0),
                 "quality": None, "note": None}
                for i, entry in enumerate(data["events"])
            ]
            trajectory = {
                "schema_version": 1, "trace_id": "mine",
                "agent": {"name": "custom", "model": "any-model", "version": "1"},
                "task": {"id": "t1", "prompt": "p", "expected": None},
                "outcome": {"success": True, "answer": steps[-1]["output"],
                            "score": 1.0},
                "totals": {"input_tokens": 0,
                           "output_tokens": sum(s["tokens"] for s in steps),
                           "cost_usd": 0.0,
                           "latency_s": sum(s["latency_s"] for s in steps)},
                "steps": steps,
            }
            return trajectory, []

        register("myformat", detect, convert_mine, "a bespoke in-house format")
        try:
            data = {"myFormat": 1, "events": [
                {"kind": "plan", "label": "plan", "in": "think", "out": "planned",
                 "tok": 10, "sec": 0.5},
                {"kind": "answer", "label": "final", "in": "compose",
                 "out": "the answer", "tok": 20, "sec": 0.4},
            ]}
            self.assertEqual(detect_format(data)["best"], "myformat")
            result = convert(data)
            Trajectory.from_json(result["trajectory"])
            self.assertEqual(result["format"], "myformat")
            self.assertIn("myformat", [f["name"] for f in formats()])
        finally:
            from deepcompare import registry
            registry._ADAPTERS.pop("myformat", None)

    def test_a_broken_detector_cannot_break_discovery(self):
        def detect(data):
            raise RuntimeError("boom")

        register("broken", detect, lambda d: ({}, []), "always raises")
        try:
            result = detect_format(fixture("otel_sample.json"))
            self.assertEqual(result["best"], "otel")
            broken = next(c for c in result["candidates"]
                          if c["format"] == "broken")
            self.assertEqual(broken["confidence"], 0.0)
            self.assertIn("detector error", broken["reason"])
        finally:
            from deepcompare import registry
            registry._ADAPTERS.pop("broken", None)


if __name__ == "__main__":
    unittest.main()
