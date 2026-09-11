"""Tool behaviour: per-tool profiles that are counts over the steps, the
agents that touched each tool, and prompt suggestions derived from the
contrast — each with its evidence and labelled a hypothesis."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.toolprofile import HYPOTHESIS, MAX_SUGGESTIONS, dossier, profile_run, suggest, tool_pair

ROOT = Path(__file__).resolve().parents[1]


def step(i, type_, name, inp, out="", **kw):
    d = {"index": i, "type": type_, "name": name, "input": inp, "output": out, "tokens": 10, "latency_s": 1.0}
    d.update(kw)
    return d


def report(a_steps, b_steps, a_success=False, b_success=True, wasted_a=(), errors_a=()):
    def run(name, steps, ok):
        return {"agent": {"name": name}, "outcome": {"success": ok, "answer": "x"}, "steps": steps}
    rows = {"a": [{"index": s["index"], "latency_s": s["latency_s"], "wasted": ("repeat" if s["index"] in wasted_a else None)} for s in a_steps],
            "b": [{"index": s["index"], "latency_s": s["latency_s"], "wasted": None} for s in b_steps]}
    return {"task": {"id": "t"}, "a": run("fail-v1", a_steps, a_success), "b": run("pass-v1", b_steps, b_success),
            "timing": {"a": {"steps": rows["a"]}, "b": {"steps": rows["b"]}},
            "reading": {"a": {"what_happened": []}, "b": {"what_happened": [{"step": 1, "feeds_answer": True}]}},
            "attribution": {"failed_agent": "a", "chain": [2, 3]},
            "diagnosis": {"subject": "a", "decisive_step": {"step": 3}}}


class ProfileTest(unittest.TestCase):
    def test_counts_repeats_runs_agents_and_marks(self):
        a = [step(0, "reason", "reason", "think"),
             step(1, "search", "web_search", "acme revenue", span={"id": "s1", "agent": "researcher"}),
             step(2, "tool_call", "run_tests", "pytest -q", "1 failed", error=True, span={"id": "s2", "agent": "coder"}),
             step(3, "tool_call", "run_tests", "pytest -q", "1 failed", error=True, span={"id": "s2", "agent": "coder"}),
             step(4, "tool_call", "run_tests", "pytest -q", "1 failed", error=True),
             step(5, "tool_call", "write_file", "write x.py", "ok", effect="write"),
             step(6, "answer", "final", "x", "x")]
        r = report(a, [step(0, "search", "web_search", "acme", "ir.acme.com"), step(1, "read", "open_page", "ir.acme.com", "…"), step(2, "answer", "final", "x", "x")], wasted_a=(3, 4))
        p = profile_run(r, "a")
        self.assertTrue(p["measurable"])
        rt = p["tools"]["run_tests"]
        self.assertEqual((rt["calls"], rt["distinct_inputs"], rt["repeats"], rt["max_identical_run"]), (3, 1, 2, 3))
        self.assertEqual(rt["identical_run_at"], {"from": 2, "to": 4, "input": "pytest -q"})
        self.assertEqual((rt["errors"], rt["error_rate"], rt["wasted_calls"], rt["wasted_s"]), (3, 1.0, 2, 2.0))
        self.assertEqual(rt["agents"], {"coder": 2, "fail-v1": 1})
        self.assertEqual((rt["first_step"], rt["last_step"], rt["fault_calls"], rt["decisive"]), (2, 4, 2, True))
        self.assertEqual(p["tools"]["write_file"]["effects"], {"write": 1})
        self.assertTrue(p["tools"]["web_search"]["external"])
        self.assertFalse(rt["external"])
        self.assertEqual(p["order"][0], "run_tests")
        t = p["totals"]
        self.assertEqual((t["tool_calls"], t["distinct_tools"], t["repeats"], t["errors"], t["external_calls"], t["agents_touching"], t["last_write_step"]), (5, 3, 2, 3, 1, 3, 5))
        b = profile_run(r, "b")
        self.assertEqual(b["tools"]["web_search"]["fed_answer"], 0)
        self.assertEqual(b["tools"]["open_page"]["fed_answer"], 1)

    def test_no_tools_is_measurable_false(self):
        r = report([step(0, "reason", "reason", "x"), step(1, "answer", "final", "x", "x")], [step(0, "answer", "final", "x", "x")])
        self.assertFalse(profile_run(r, "a")["measurable"])
        self.assertIn("Neither run called a tool", tool_pair(r)["narrative"])
        self.assertEqual(tool_pair(r)["suggestions"], [])


class SuggestTest(unittest.TestCase):
    def test_rules_fire_with_evidence_and_are_ordered(self):
        a = [step(0, "tool_call", "run_tests", "pytest -q", "1 failed", error=True),
             step(1, "tool_call", "run_tests", "pytest -q", "1 failed", error=True),
             step(2, "tool_call", "run_tests", "pytest -q", "1 failed", error=True),
             step(3, "tool_call", "write_file", "write x.py", "ok", effect="write"),
             step(4, "search", "web_search", "q1", "nothing"), step(5, "search", "web_search", "q2", "nothing"),
             step(6, "search", "web_search", "q3", "nothing"), step(7, "search", "web_search", "q4", "nothing"),
             step(8, "answer", "final", "x", "x")]
        b = [step(0, "read", "read_file", "read x.py", "…"), step(1, "tool_call", "write_file", "write x.py", "ok", effect="write"),
             step(2, "tool_call", "run_tests", "pytest -q", "12 passed"), step(3, "answer", "final", "x", "x")]
        r = report(a, b, wasted_a=(4, 5, 6, 7))
        s = suggest(r, profile_run(r, "a"), profile_run(r, "b"))
        kinds = [x["kind"] for x in s]
        self.assertIn("identical_retries", kinds)
        self.assertIn("tool_errors", kinds)
        self.assertIn("unproductive_tool", kinds)
        self.assertIn("missing_tool", kinds)
        self.assertIn("verify_after_write", kinds)
        self.assertIn("external_overuse", kinds)
        self.assertLessEqual(len(s), MAX_SUGGESTIONS)
        self.assertEqual([x["weight"] for x in s], sorted([x["weight"] for x in s], reverse=True))
        first = next(x for x in s if x["kind"] == "identical_retries")
        self.assertEqual(first["tool"], "run_tests")
        self.assertEqual(first["for"], "fail-v1")
        self.assertEqual(first["status"], HYPOTHESIS)
        self.assertIn("repeated `pytest -q` 3× in a row at steps 0–2", first["text"])
        self.assertEqual(first["evidence"]["run"], 3)
        missing = next(x for x in s if x["kind"] == "missing_tool")
        self.assertEqual(missing["tool"], "read_file")
        self.assertIn("pass-v1 called it 1×", missing["text"])
        verify = next(x for x in s if x["kind"] == "verify_after_write")
        self.assertEqual(verify["evidence"]["last_write_step"], 3)
        self.assertEqual(verify["evidence"]["passing_verifier"], "run_tests")

    def test_no_failing_side_means_no_suggestions(self):
        a = [step(0, "search", "web_search", "q", "r"), step(1, "answer", "final", "x", "x")]
        r = report(a, a, a_success=True, b_success=True)
        r["diagnosis"] = {}
        self.assertEqual(suggest(r, profile_run(r, "a"), profile_run(r, "b")), [])

    def test_pair_rows_and_dossier(self):
        a = [step(0, "search", "web_search", "q", "r", span={"id": "s1", "agent": "researcher"}), step(1, "answer", "final", "x", "x")]
        b = [step(0, "read", "read_file", "f", "c"), step(1, "answer", "final", "x", "x")]
        r = report(a, b)
        pair = tool_pair(r)
        self.assertEqual([row["name"] for row in pair["tools"]], ["read_file", "web_search"])
        ws = next(row for row in pair["tools"] if row["name"] == "web_search")
        self.assertEqual((ws["only"], ws["delta_calls"], ws["agents"]), ("a", 1, ["researcher"]))
        self.assertIn("only fail-v1 used web_search; pass-v1 used read_file", pair["narrative"])
        r["tools_profile"] = pair
        d = dossier(r, "web_search")
        self.assertTrue(d["found"])
        self.assertIsNone(d["b"])
        self.assertEqual(d["a"]["calls"], 1)
        self.assertFalse(dossier(r, "nope")["found"])


class DemoTest(unittest.TestCase):
    def test_the_long_demo_reads_the_ledger_retries_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", tmp], cwd=str(ROOT), check=True, capture_output=True)
            rep = json.loads((Path(tmp) / "report_h02_migrate_service.json").read_text(encoding="utf-8"))
        self.assertIn("tools_profile", rep)
        tp = rep["tools_profile"]
        b = tp["b"]["tools"]["run_tests"]
        self.assertGreaterEqual(b["max_identical_run"], 10)
        self.assertIn("migrator-ledger.tests", b["agents"])
        kinds = [s["kind"] for s in tp["suggestions"]]
        self.assertIn("identical_retries", kinds)
        first = next(s for s in tp["suggestions"] if s["kind"] == "identical_retries")
        self.assertEqual(first["tool"], "run_tests")
        self.assertEqual(first["for"], "comet-lh")
        self.assertIn("pytest -q tests/ledger", first["text"])
        self.assertEqual(json.dumps(tool_pair(rep), sort_keys=True), json.dumps(tool_pair(rep), sort_keys=True))
        touched = max(tp["tools"], key=lambda r: len(r["agents"]))
        self.assertGreater(len(touched["agents"]), 5)


if __name__ == "__main__":
    unittest.main()
