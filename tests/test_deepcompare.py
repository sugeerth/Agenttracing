"""Unit tests for the DeepCompare AI engine (stdlib unittest)."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.align import align, step_similarity
from deepcompare.metrics import aggregate, metrics_delta
from deepcompare.recommend import recommend
from deepcompare.report import render_html


def make_step(index, type, name="", input="", output="", tokens=100,
              latency_s=1.0, quality=None):
    return {
        "index": index,
        "type": type,
        "name": name,
        "input": input,
        "output": output,
        "tokens": tokens,
        "latency_s": latency_s,
        "quality": quality,
        "note": None,
    }


def make_traj(agent_name, task_id, success, answer, steps,
              input_tokens=500, output_tokens=200, cost_usd=0.01, latency_s=5.0):
    return {
        "schema_version": 1,
        "trace_id": f"{agent_name}-{task_id}",
        "agent": {"name": agent_name, "model": "test-model", "version": "v1"},
        "task": {"id": task_id, "prompt": "What was Acme's 2023 revenue?",
                 "expected": "5.2 billion"},
        "outcome": {"success": success, "answer": answer, "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                   "cost_usd": cost_usd, "latency_s": latency_s},
        "steps": steps,
    }


def retrieval_pair(task_id="t1"):
    """A synthetic pair with a known retrieval divergence at step 2.

    Steps 0-1 are identical; at step 2 agent A retrieves the annual report
    (quality good) while agent B retrieves a blog post (quality bad), which
    propagates through step 3 into a wrong final answer. B fails.
    """
    common = [
        make_step(0, "plan", "plan", "find acme 2023 revenue in official filings"),
        make_step(1, "search", "web_search", "acme 2023 annual report revenue"),
    ]
    a_steps = common + [
        make_step(2, "retrieve", "annual_report", "acme 2023 annual report pdf",
                  output="revenue was 5.2 billion per annual report", quality="good"),
        make_step(3, "read", "annual_report_p12",
                  input="revenue was 5.2 billion per annual report",
                  output="confirmed revenue 5.2 billion"),
        make_step(4, "answer", "final", "5.2 billion",
                  output="Acme 2023 revenue was 5.2 billion"),
    ]
    b_steps = copy.deepcopy(common) + [
        make_step(2, "retrieve", "blog", "random tech gossip post",
                  output="blog claims revenue was 12 billion", quality="bad",
                  tokens=200),
        make_step(3, "read", "blog_post", input="blog claims revenue was 12 billion",
                  output="the blog says 12 billion", tokens=200),
        make_step(4, "answer", "final", "12 billion",
                  output="Acme 2023 revenue was 12 billion", tokens=200),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, True,
                                       "5.2 billion", a_steps,
                                       input_tokens=500, output_tokens=200,
                                       cost_usd=0.01, latency_s=5.0))
    b = Trajectory.from_json(make_traj("agent-b", task_id, False,
                                       "12 billion", b_steps,
                                       input_tokens=800, output_tokens=400,
                                       cost_usd=0.02, latency_s=9.0))
    return a, b


def tool_pair(task_id="t2"):
    """A pair whose first divergence is a tool_call step; B fails."""
    a_steps = [
        make_step(0, "plan", "plan", "compute the answer with a calculator"),
        make_step(1, "tool_call", "calculator", "5.2 * 1000"),
        make_step(2, "answer", "final", "5200"),
    ]
    b_steps = [
        make_step(0, "plan", "plan", "compute the answer with a calculator"),
        make_step(1, "tool_call", "python_exec", "run untested script"),
        make_step(2, "answer", "final", "error"),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, True, "5200", a_steps))
    b = Trajectory.from_json(make_traj("agent-b", task_id, False, "error", b_steps))
    return a, b


def same_tool_bad_args_pair(task_id="t4"):
    """A pair that is lexically near-identical: same tool at step 1, subtly
    wrong arguments on A's side (annotated bad).  A fails."""
    a_steps = [
        make_step(0, "plan", "plan", "extract the first fatal error from the log"),
        make_step(1, "tool_call", "regex_extract",
                  "regex_extract(pattern='(Warning|Error)', source=log, first_match=true)",
                  output="DeprecationWarning: old api", quality="bad"),
        make_step(2, "answer", "final", "the DeprecationWarning is the cause"),
    ]
    b_steps = [
        make_step(0, "plan", "plan", "extract the first fatal error from the log"),
        make_step(1, "tool_call", "regex_extract",
                  "regex_extract(pattern='^E\\\\s+\\\\w+Error', source=log)",
                  output="E ImportError: no module named foo", quality="good"),
        make_step(2, "answer", "final", "the ImportError is the cause"),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, False, "wrong", a_steps))
    b = Trajectory.from_json(make_traj("agent-b", task_id, True, "right", b_steps))
    return a, b


def detour_pair(task_id="t5"):
    """Both agents succeed, but B runs two redundant trailing searches before
    answering — a non-fatal stopping detour."""
    a_steps = [
        make_step(0, "plan", "plan", "find the answer"),
        make_step(1, "search", "web_search", "the answer"),
        make_step(2, "answer", "final", "done"),
    ]
    b_steps = [
        make_step(0, "plan", "plan", "find the answer"),
        make_step(1, "search", "web_search", "the answer"),
        make_step(2, "search", "web_search", "double-check corroborating sources",
                  tokens=400, latency_s=3.0),
        make_step(3, "search", "web_search", "triple-check yet more sources",
                  tokens=400, latency_s=3.0),
        make_step(4, "answer", "final", "done"),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, True, "done", a_steps))
    b = Trajectory.from_json(make_traj("agent-b", task_id, True, "done", b_steps))
    return a, b


def identical_pair(task_id="t3", success=True):
    steps = [
        make_step(0, "plan", "plan", "do the thing"),
        make_step(1, "search", "web_search", "the thing"),
        make_step(2, "answer", "final", "done"),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, success, "done", steps))
    b = Trajectory.from_json(make_traj("agent-b", task_id, success, "done",
                                       copy.deepcopy(steps)))
    return a, b


class TestValidation(unittest.TestCase):
    def valid(self):
        a, _ = identical_pair()
        return a.to_dict()

    def test_valid_roundtrip(self):
        t = Trajectory.from_json(self.valid())
        self.assertEqual(t.agent.name, "agent-a")
        self.assertEqual(len(t.steps), 3)

    def test_missing_steps(self):
        data = self.valid()
        del data["steps"]
        with self.assertRaisesRegex(ValueError, "steps"):
            Trajectory.from_json(data)

    def test_empty_steps(self):
        data = self.valid()
        data["steps"] = []
        with self.assertRaisesRegex(ValueError, "at least one step"):
            Trajectory.from_json(data)

    def test_missing_agent_name(self):
        data = self.valid()
        del data["agent"]["name"]
        with self.assertRaisesRegex(ValueError, "name"):
            Trajectory.from_json(data)

    def test_last_step_not_answer(self):
        data = self.valid()
        data["steps"][-1]["type"] = "reason"
        with self.assertRaisesRegex(ValueError, "last step.*answer"):
            Trajectory.from_json(data)

    def test_bad_step_type(self):
        data = self.valid()
        data["steps"][1]["type"] = "hallucinate"
        with self.assertRaisesRegex(ValueError, "invalid step type"):
            Trajectory.from_json(data)

    def test_bad_quality(self):
        data = self.valid()
        data["steps"][0]["quality"] = "amazing"
        with self.assertRaisesRegex(ValueError, "quality"):
            Trajectory.from_json(data)

    def test_from_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.json"
            path.write_text(json.dumps(self.valid()), encoding="utf-8")
            t = Trajectory.from_json(path)
            self.assertEqual(t.task.id, "t3")


class TestAlignment(unittest.TestCase):
    def test_identical_all_match(self):
        a, b = identical_pair()
        alignment = align(a, b)
        self.assertEqual(len(alignment), 3)
        for i, entry in enumerate(alignment):
            self.assertEqual(entry["op"], "match")
            self.assertEqual(entry["a_index"], i)
            self.assertEqual(entry["b_index"], i)
            self.assertAlmostEqual(entry["similarity"], 1.0)

    def test_similarity_bounds_and_types(self):
        a, b = retrieval_pair()
        plan, search = a.steps[0], a.steps[1]
        self.assertEqual(step_similarity(plan, plan), 1.0)
        # different, non-adjacent types score 0
        self.assertEqual(step_similarity(plan, a.steps[3]), 0.0)
        # search vs retrieve are adjacent: base 0.3
        sim = step_similarity(search, a.steps[2])
        self.assertGreaterEqual(sim, 0.3)
        self.assertLess(sim, 0.75)

    def test_drift_detected(self):
        a, b = retrieval_pair()
        alignment = align(a, b)
        ops = [e["op"] for e in alignment]
        self.assertEqual(ops[:2], ["match", "match"])
        self.assertEqual(alignment[2]["op"], "drift")
        self.assertEqual(alignment[2]["a_index"], 2)
        self.assertEqual(alignment[2]["b_index"], 2)


class TestDivergence(unittest.TestCase):
    def test_retrieval_divergence_at_step_2(self):
        a, b = retrieval_pair()
        report = compare(a, b)
        divs = report["divergences"]
        self.assertGreaterEqual(len(divs), 1)
        first = divs[0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["a_index"], 2)
        self.assertEqual(first["b_index"], 2)
        self.assertEqual(first["kind"], "retrieval")
        self.assertTrue(first["downstream"]["caused_failure"])
        # B spends more tokens downstream (200 vs 100 per step over 3 steps)
        self.assertEqual(first["downstream"]["extra_tokens_b"], 300)
        self.assertIn("lower-quality", first["summary"])

    def test_no_divergences_when_identical(self):
        a, b = identical_pair()
        report = compare(a, b)
        self.assertEqual(report["divergences"], [])

    def test_quality_demotion_catches_semantic_divergence(self):
        # Lexically the tool_call steps align as a match; the bad-quality
        # annotation on A's side must demote the pair to drift and classify
        # the divergence as tool_execution (same tool, different usage).
        a, b = same_tool_bad_args_pair()
        alignment = align(a, b)
        pair = next(e for e in alignment if e["a_index"] == 1 and e["b_index"] == 1)
        self.assertEqual(pair["op"], "drift")
        report = compare(a, b)
        self.assertGreaterEqual(len(report["divergences"]), 1)
        first = report["divergences"][0]
        self.assertEqual(first["kind"], "tool_execution")
        self.assertTrue(first["downstream"]["caused_failure"])
        self.assertEqual(report["attribution"]["failed_agent"], "a")
        self.assertEqual(report["attribution"]["category"], "tool_execution")


class TestAttribution(unittest.TestCase):
    def test_failed_agent_chain_and_category(self):
        a, b = retrieval_pair()
        report = compare(a, b)
        attribution = report["attribution"]
        self.assertEqual(attribution["failed_agent"], "b")
        self.assertEqual(attribution["root_cause_step"], 2)
        self.assertEqual(attribution["category"], "retrieval")
        self.assertTrue(attribution["chain"])
        self.assertIn(2, attribution["chain"])
        # propagation: B step 3 input == B step 2 output
        self.assertIn(3, attribution["chain"])
        # final answer step always terminates the chain
        self.assertEqual(attribution["chain"][-1], 4)
        self.assertIn("diverged at step 2", attribution["explanation"])

    def test_both_succeeded_no_attribution(self):
        a, b = identical_pair()
        report = compare(a, b)
        attribution = report["attribution"]
        self.assertIsNone(attribution["failed_agent"])
        self.assertEqual(attribution["chain"], [])
        self.assertIn("Both agents succeeded", attribution["explanation"])


class TestMetrics(unittest.TestCase):
    def test_metrics_delta_arithmetic(self):
        a, b = retrieval_pair()
        delta = metrics_delta(a, b)
        self.assertEqual(delta["steps"], {"a": 5, "b": 5})
        self.assertEqual(delta["tokens"], {"a": 700, "b": 1200})
        self.assertEqual(delta["cost_usd"], {"a": 0.01, "b": 0.02})
        self.assertEqual(delta["latency_s"], {"a": 5.0, "b": 9.0})
        self.assertEqual(delta["tool_calls"], {"a": 0, "b": 0})
        self.assertEqual(delta["searches"], {"a": 1, "b": 1})

    def test_tool_calls_counted(self):
        a, b = tool_pair()
        delta = metrics_delta(a, b)
        self.assertEqual(delta["tool_calls"], {"a": 1, "b": 1})


class TestAggregate(unittest.TestCase):
    def make_reports(self):
        reports = []
        for tid in ("t1", "t1b"):
            a, b = retrieval_pair(tid)
            reports.append(compare(a, b))
        a, b = tool_pair("t2")
        reports.append(compare(a, b))
        a, b = identical_pair("t3")
        reports.append(compare(a, b))
        return reports

    def test_failure_origins_sum_to_one(self):
        agg = aggregate(self.make_reports())
        origins = agg["failure_origins"]
        self.assertTrue(origins)
        self.assertAlmostEqual(sum(origins.values()), 1.0, places=3)
        self.assertAlmostEqual(origins["retrieval"], 2 / 3, places=3)
        self.assertAlmostEqual(origins["tool_selection"], 1 / 3, places=3)

    def test_success_rates_and_means(self):
        agg = aggregate(self.make_reports())
        self.assertEqual(agg["tasks"], 4)
        self.assertEqual(agg["agents"], {"a": "agent-a", "b": "agent-b"})
        self.assertAlmostEqual(agg["success_rate"]["a"], 1.0)
        self.assertAlmostEqual(agg["success_rate"]["b"], 0.25)
        self.assertGreater(agg["means"]["b"]["tokens"], agg["means"]["a"]["tokens"])

    def test_empty(self):
        agg = aggregate([])
        self.assertEqual(agg["tasks"], 0)
        self.assertEqual(agg["failure_origins"], {})


class TestReportRendering(unittest.TestCase):
    def test_html_marker_injection(self):
        a, b = identical_pair()
        report = compare(a, b)
        agg = aggregate([report])
        template = (
            "<h1>viewer</h1>\n"
            "<script>\n"
            "  window.DEEPCOMPARE_DATA = null; // replaced by the engine\n"
            "</script>\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "viewer.html"
            tpl.write_text(template, encoding="utf-8")
            out = Path(tmp) / "report.html"
            render_html([report], agg, tpl, out)
            html = out.read_text(encoding="utf-8")
        self.assertIn("  window.DEEPCOMPARE_DATA = {", html)
        data_line = next(
            line for line in html.splitlines() if "window.DEEPCOMPARE_DATA" in line
        )
        payload = data_line.split("=", 1)[1].strip().rstrip(";").replace("<\\/", "</")
        data = json.loads(payload)
        self.assertEqual(len(data["reports"]), 1)
        self.assertIn("aggregate", data)

    def test_marker_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "viewer.html"
            tpl.write_text("<h1>no marker here</h1>\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marker"):
                render_html([], {}, tpl, Path(tmp) / "out.html")

    def test_compare_rejects_task_mismatch(self):
        a, _ = identical_pair("tX")
        _, b = identical_pair("tY")
        with self.assertRaisesRegex(ValueError, "different tasks"):
            compare(a, b)


class TestRecommend(unittest.TestCase):
    def test_retrieval_failure_recommendation(self):
        a, b = retrieval_pair("t1")
        recs = recommend([compare(a, b)])
        self.assertTrue(recs)
        rec = recs[0]
        self.assertEqual(rec["category"], "retrieval")
        self.assertEqual(rec["agent"], "agent-b")
        self.assertEqual(rec["severity"], "critical")
        self.assertIn("t1", rec["finding"])
        self.assertEqual(rec["evidence_tasks"], ["t1"])
        self.assertTrue(rec["suggested_prompt"])
        # instantiated with the real bad/good source names from the steps
        self.assertIn("blog", rec["suggested_prompt"])
        self.assertIn("annual_report", rec["suggested_prompt"])
        self.assertIn("wasted tokens", rec["expected_gain"])

    def test_efficiency_recommendation_for_detour(self):
        a, b = detour_pair("t5")
        recs = recommend([compare(a, b)])
        self.assertTrue(recs)
        rec = recs[0]
        self.assertEqual(rec["category"], "efficiency")
        self.assertEqual(rec["agent"], "agent-b")
        self.assertEqual(rec["severity"], "moderate")  # 800 extra tokens > 500
        self.assertIn("t5", rec["finding"])
        self.assertIn("800", rec["finding"])
        self.assertIn("search", rec["suggested_prompt"].lower())
        self.assertIn("800", rec["expected_gain"])

    def test_identical_pair_yields_no_recommendations(self):
        a, b = identical_pair("t3")
        self.assertEqual(recommend([compare(a, b)]), [])
        self.assertEqual(recommend([]), [])

    def test_aggregate_contains_recommendations(self):
        reports = []
        for maker, tid in ((retrieval_pair, "t1"), (tool_pair, "t2"),
                           (identical_pair, "t3"), (detour_pair, "t5")):
            x, y = maker(tid)
            reports.append(compare(x, y))
        agg = aggregate(reports)
        self.assertIn("recommendations", agg)
        recs = agg["recommendations"]
        self.assertTrue(recs)
        # critical failure recs sort before efficiency detours
        self.assertEqual(recs[0]["severity"], "critical")
        severities = [r["severity"] for r in recs]
        self.assertEqual(severities, sorted(severities, key=["critical", "moderate", "minor"].index))
        self.assertEqual(aggregate([])["recommendations"], [])


if __name__ == "__main__":
    unittest.main()
