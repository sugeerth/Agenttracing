"""Unit tests for the DeepCompare AI engine (stdlib unittest)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.align import align, step_similarity
from deepcompare.fleet import fleet_analysis
from deepcompare.metrics import aggregate, metrics_delta
from deepcompare.recommend import recommend
from deepcompare.report import render_html
from deepcompare.tooldiff import parse_args, token_diff, tool_diff


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


class TestTokenBasisSurvivesRoundTrip(unittest.TestCase):
    """An estimate must not become a measurement by being saved and reloaded.

    The recorder writes ``tokens_basis`` and ``token_accounting``; if the
    loader drops them, the very next comparison treats a len(text)/4 guess
    as a provider-reported count, and nothing downstream can tell.
    """

    def trajectory(self, **step_extra):
        step = {"index": 0, "type": "answer", "name": "final", "input": "x",
                "output": "x", "tokens": 4, "latency_s": 0.1}
        step.update(step_extra)
        return {
            "trace_id": "t", "agent": {"name": "a", "model": "m"},
            "task": {"id": "t1", "prompt": "p"},
            "outcome": {"success": True, "answer": "x"},
            "totals": {"input_tokens": 1, "output_tokens": 4,
                       "cost_usd": 0.0, "latency_s": 0.1},
            "steps": [step],
            "token_accounting": {"basis": "estimated", "estimator": "len(text)/4"},
        }

    def test_step_basis_survives_load(self):
        loaded = Trajectory.from_json(self.trajectory(tokens_basis="estimated"))
        self.assertEqual(loaded.steps[0].tokens_basis, "estimated")

    def test_step_basis_survives_a_save_and_reload(self):
        once = Trajectory.from_json(self.trajectory(tokens_basis="estimated"))
        twice = Trajectory.from_json(once.to_dict())
        self.assertEqual(twice.steps[0].tokens_basis, "estimated")

    def test_run_level_accounting_survives(self):
        loaded = Trajectory.from_json(self.trajectory())
        self.assertEqual(loaded.token_accounting["basis"], "estimated")
        self.assertEqual(
            Trajectory.from_json(loaded.to_dict()).token_accounting["estimator"],
            "len(text)/4")

    def test_absent_basis_stays_absent_rather_than_defaulting_to_measured(self):
        loaded = Trajectory.from_json(self.trajectory())
        self.assertIsNone(loaded.steps[0].tokens_basis)

    def test_an_invented_basis_is_refused(self):
        with self.assertRaises(ValueError):
            Trajectory.from_json(self.trajectory(tokens_basis="vibes"))


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


def fleet_traj(agent, task_id, success, cost, latency, tokens,
               extra_searches=0, bad_retrieve=False):
    steps = [
        make_step(0, "plan", "plan", f"solve {task_id}"),
        make_step(1, "search", "web_search", f"query for {task_id}"),
    ]
    if bad_retrieve:
        steps.append(make_step(2, "retrieve", "sketchy_forum",
                               "open forum result thread",
                               output="forum claims xyz", quality="bad"))
    else:
        steps.append(make_step(2, "retrieve", "official_site",
                               "open official result",
                               output="official data value", quality="good"))
    idx = 3
    for k in range(extra_searches):
        steps.append(make_step(idx, "search", "web_search",
                               f"re-check source number {k}"))
        idx += 1
    steps.append(make_step(idx, "answer", "final", "right" if success else "wrong"))
    return Trajectory.from_json(make_traj(
        agent, task_id, success, "right" if success else "wrong", steps,
        input_tokens=tokens, output_tokens=0, cost_usd=cost, latency_s=latency))


def fleet_fixture():
    """4 agents x 2 tasks with a known ranking: alpha (cheap, all-success),
    charlie (cheapest/fastest but fails f1), bravo (all-success but costly and
    undisciplined), delta (fails everything)."""
    tasks = ("f1", "f2")
    return {
        "alpha": [fleet_traj("alpha", t, True, 0.01, 5.0, 700) for t in tasks],
        "bravo": [fleet_traj("bravo", t, True, 0.05, 20.0, 3000,
                             extra_searches=2) for t in tasks],
        "charlie": [
            fleet_traj("charlie", "f1", False, 0.008, 4.0, 600, bad_retrieve=True),
            fleet_traj("charlie", "f2", True, 0.008, 4.0, 600),
        ],
        "delta": [fleet_traj("delta", t, False, 0.02, 10.0, 900,
                             extra_searches=1, bad_retrieve=True) for t in tasks],
    }


class TestToolDiff(unittest.TestCase):
    def test_parse_args_well_formed(self):
        parsed = parse_args("regex_extract(pattern='(Warning|Error)', source=log, first_match=true)")
        self.assertEqual(parsed, {"pattern": "(Warning|Error)",
                                  "source": "log", "first_match": "true"})

    def test_parse_args_quoted_comma(self):
        self.assertEqual(parse_args('send(msg="a, b", n=2)'), {"msg": "a, b", "n": "2"})

    def test_parse_args_non_call(self):
        self.assertIsNone(parse_args("search the web for acme revenue"))
        self.assertIsNone(parse_args(""))
        self.assertIsNone(parse_args("f(1, 2)"))  # positional args: not k=v
        self.assertEqual(parse_args("noop()"), {})

    def test_token_diff_roundtrip(self):
        a = "select the annual report now"
        b = "select the blog post now"
        diff = token_diff(a, b)
        self.assertTrue(all(op in ("eq", "del", "ins") for op, _ in diff))
        self.assertEqual("".join(t for op, t in diff if op in ("eq", "del")), a)
        self.assertEqual("".join(t for op, t in diff if op in ("eq", "ins")), b)
        # consecutive same-op runs are merged
        for prev, cur in zip(diff, diff[1:]):
            self.assertNotEqual(prev[0], cur[0])
        self.assertEqual(token_diff("same", "same"), [["eq", "same"]])

    def test_tool_diff_same_tool_bad_args(self):
        a, b = same_tool_bad_args_pair()
        td = tool_diff(a.steps[1], b.steps[1])
        self.assertIsNotNone(td)
        self.assertTrue(td["same_tool"])
        self.assertEqual(td["name_a"], "regex_extract")
        changed_keys = [c["key"] for c in td["changed"]]
        self.assertIn("pattern", changed_keys)
        self.assertEqual(td["only_a"], ["first_match"])
        self.assertTrue(len(td["raw_diff"]) > 1)

    def test_tool_diff_none_for_non_toolish(self):
        a, b = identical_pair()
        self.assertIsNone(tool_diff(a.steps[0], b.steps[0]))  # plan vs plan

    def test_compare_wires_tool_diff(self):
        report = compare(*same_tool_bad_args_pair())
        entry = next(e for e in report["alignment"] if e["a_index"] == 1)
        self.assertIn("tool_diff", entry)
        self.assertTrue(entry["tool_diff"]["same_tool"])
        # identical tool-ish steps get the cheap identical marker
        report2 = compare(*identical_pair())
        entry2 = next(e for e in report2["alignment"] if e["a_index"] == 1)
        self.assertEqual(entry2["tool_diff"], {"same_tool": True, "identical": True})


class TestStepEval(unittest.TestCase):
    def test_eval_present_with_delta_arithmetic(self):
        report = compare(*retrieval_pair())
        entry0 = report["alignment"][0]  # identical plan steps, matched
        self.assertIn("eval", entry0)
        ev = entry0["eval"]
        self.assertTrue(ev["similarity"]["type_match"])
        self.assertEqual(ev["similarity"]["name_jaccard"], 1.0)
        self.assertEqual(ev["similarity"]["input_jaccard"], 1.0)
        # both steps 100 tokens, 1.0s; cost = 100*(0.02/1200) - 100*(0.01/700)
        self.assertEqual(ev["delta"]["tokens"], 0)
        self.assertEqual(ev["delta"]["latency_s"], 0.0)
        self.assertEqual(ev["delta"]["cost_usd"], 0.000238)
        self.assertEqual(ev["quality"]["verdict"], "equal")

    def test_eval_divergent_step_quality_and_delta(self):
        report = compare(*retrieval_pair())
        ev = report["alignment"][2]["eval"]  # A annual_report vs B blog
        self.assertEqual(ev["quality"], {"a": "good", "b": "bad", "verdict": "b_degraded"})
        # B step 200 tokens vs A 100: cost = 200*(0.02/1200) - 100*(0.01/700)
        self.assertEqual(ev["delta"]["tokens"], 100)
        self.assertEqual(ev["delta"]["cost_usd"], 0.001905)
        self.assertEqual(ev["similarity"]["name_jaccard"], 0.0)

    def test_propagation_bounds_and_ordering(self):
        report = compare(*retrieval_pair())
        for entry in report["alignment"]:
            prop = entry.get("eval", {}).get("propagation")
            self.assertIsNotNone(prop)  # one agent failed, root set
            for side in ("a", "b"):
                self.assertGreaterEqual(prop[side], 0.0)
                self.assertLessEqual(prop[side], 1.0)
        pre = report["alignment"][0]["eval"]["propagation"]["b"]
        after_root = report["alignment"][3]["eval"]["propagation"]["b"]
        self.assertGreater(after_root, pre)
        self.assertEqual(after_root, 1.0)  # B step 3 input == B root output
        # no propagation key when both agents succeeded
        clean = compare(*identical_pair())
        for entry in clean["alignment"]:
            self.assertNotIn("propagation", entry["eval"])

    def test_answer_eval_verdicts_and_diff(self):
        report = compare(*retrieval_pair())
        ae = report["answer_eval"]
        self.assertEqual(ae["expected"], "5.2 billion")
        self.assertEqual(ae["a_vs_expected"], {"coverage": 1.0, "verdict": "match"})
        # B answered "12 billion": covers {billion} of {5.2, billion} = 0.5,
        # but the numeric token 5.2 is missing so the verdict caps at partial.
        self.assertEqual(ae["b_vs_expected"], {"coverage": 0.5, "verdict": "partial"})
        diff = ae["diff_ab"]
        self.assertEqual("".join(t for op, t in diff if op in ("eq", "del")), "5.2 billion")
        self.assertEqual("".join(t for op, t in diff if op in ("eq", "ins")), "12 billion")

    def test_answer_eval_expected_none_unknown(self):
        a, b = identical_pair()
        a.task.expected = None
        from deepcompare.steps_eval import answer_eval
        ae = answer_eval(a, b)
        self.assertIsNone(ae["expected"])
        self.assertEqual(ae["a_vs_expected"], {"coverage": None, "verdict": "unknown"})
        self.assertEqual(ae["b_vs_expected"], {"coverage": None, "verdict": "unknown"})


class TestSuccessAnalysis(unittest.TestCase):
    def test_outcome_winner_on_retrieval_pair(self):
        report = compare(*retrieval_pair())
        sa = report["success_analysis"]
        self.assertIsNotNone(sa)
        self.assertEqual(sa["winner"], "a")
        self.assertEqual(sa["basis"], "outcome")
        self.assertTrue(sa["winning_decisions"])
        d0 = sa["winning_decisions"][0]
        self.assertEqual(d0["agent"], "agent-a")
        self.assertEqual(d0["kind"], "retrieval")
        self.assertEqual(d0["step_index"], 2)
        # impact mirrors the divergence downstream (loser B side)
        self.assertEqual(d0["impact"]["avoided_extra_steps"], 0)
        self.assertEqual(d0["impact"]["avoided_tokens"], 300)
        self.assertTrue(d0["impact"]["avoided_failure"])
        self.assertIn("(agent-b)", d0["counterpart"])
        self.assertIn("won on outcome", sa["narrative"])
        self.assertIn("300", sa["narrative"])

    def test_efficiency_winner_on_detour_pair(self):
        report = compare(*detour_pair())
        sa = report["success_analysis"]
        self.assertIsNotNone(sa)
        self.assertEqual(sa["winner"], "a")
        self.assertEqual(sa["basis"], "efficiency")
        d0 = sa["winning_decisions"][0]
        # one-sided region: winner has no step there
        self.assertTrue(d0["decision"].startswith("proceeded directly"))
        self.assertEqual(d0["kind"], "stopping")
        self.assertEqual(d0["impact"]["avoided_extra_steps"], 2)
        self.assertEqual(d0["impact"]["avoided_tokens"], 800)
        self.assertFalse(d0["impact"]["avoided_failure"])
        self.assertIn("won on efficiency", sa["narrative"])

    def test_null_when_equivalent_or_both_failed(self):
        report = compare(*identical_pair())
        self.assertIsNone(report["success_analysis"])
        report2 = compare(*identical_pair("t9", success=False))
        self.assertIsNone(report2["success_analysis"])

    def test_playbook_groups_and_ordering(self):
        from deepcompare.success import playbook
        reports = [compare(*retrieval_pair("t1")), compare(*detour_pair("t5"))]
        habits = playbook(reports)
        self.assertEqual(len(habits), 2)
        # avoided-failures-first ordering: retrieval (1 failure) before stopping
        self.assertEqual(habits[0]["kind"], "retrieval")
        self.assertEqual(habits[0]["agents"], ["agent-a"])
        self.assertIn("t1", habits[0]["evidence"])
        self.assertIn("avoided 1 failure(s)", habits[0]["impact"])
        self.assertIn("300 tokens", habits[0]["impact"])
        self.assertEqual(habits[1]["kind"], "stopping")
        self.assertIn("800 tokens", habits[1]["impact"])
        self.assertIn("corroborate at most twice", habits[1]["habit"])

    def test_aggregate_carries_playbook(self):
        reports = [compare(*retrieval_pair("t1"))]
        agg = aggregate(reports)
        self.assertIn("playbook", agg)
        self.assertTrue(agg["playbook"])
        self.assertEqual(aggregate([])["playbook"], [])


def circular_pair(task_id="t7c"):
    """B corroborates its answer with a second source that merely cites the
    first — circular corroboration; A cites the primary source."""
    a_steps = [
        make_step(0, "search", "web_search", "acme revenue official filing"),
        make_step(1, "retrieve", "select_result", "open the official filing",
                  output="Selected https://ir.acmecorp.com/results — revenue $4.82 billion"),
        make_step(2, "answer", "final", "$4.82 billion", output="$4.82 billion"),
    ]
    b_steps = [
        make_step(0, "search", "web_search", "acme revenue"),
        make_step(1, "retrieve", "select_result", "open blog result",
                  output="financeblog.net says revenue was $4.5 billion"),
        make_step(2, "read", "open_page", "open second source",
                  output="moneymirror.com repeats: financeblog.net reported $4.5 billion"),
        make_step(3, "answer", "final", "$4.5 billion", output="$4.5 billion"),
    ]
    a = Trajectory.from_json(make_traj("agent-a", task_id, True, "$4.82 billion", a_steps))
    b = Trajectory.from_json(make_traj("agent-b", task_id, False, "$4.5 billion", b_steps))
    return a, b


class TestSemantic(unittest.TestCase):
    def test_claim_extraction_kinds_and_normalization(self):
        from deepcompare.semantic import extract_from_text

        def kinds(text):
            return extract_from_text(text)

        self.assertIn(("money", "$4.82 billion", "4.82e9"), kinds("$4.82 billion"))
        self.assertIn(("money", "$4.5B", "4.5e9"), kinds("about $4.5B in 2025"))
        money = [c for c in kinds("costs $11,700/yr today") if c[0] == "money"]
        self.assertEqual(money[0][2], "11700")
        self.assertIn(("percent", "4.1%", "4.1"), kinds("grew 4.1% YoY"))
        self.assertEqual([c[2] for c in kinds("about 4.1 percent") if c[0] == "percent"],
                         ["4.1"])
        self.assertEqual([c[2] for c in kinds("took 23 hours 45 minutes") if c[0] == "duration"],
                         ["1425"])
        self.assertEqual([c[2] for c in kinds("flight time 11h45m") if c[0] == "duration"],
                         ["705"])
        self.assertIn(("version", "2.14.1", "2.14.1"), kinds("libfoo 2.14.1 fixed it"))
        self.assertIn(("cve", "CVE-2025-1234", "CVE-2025-1234"), kinds("see CVE-2025-1234"))
        urls = [c[2] for c in kinds("per financeblog.net and https://ir.acmecorp.com/news")
                if c[0] == "url"]
        self.assertIn("financeblog.net", urls)
        self.assertIn("ir.acmecorp.com", urls)
        dates = [c[2] for c in kinds("on 10 June 2025, then 2025-05-22, then December 2024")
                 if c[0] == "date"]
        self.assertEqual(dates, ["2025-05-22", "2025-06-10", "2024-12"])
        self.assertEqual([c[2] for c in kinds("checked 3 sources") if c[0] == "number"],
                         ["3"])
        self.assertEqual(kinds("the answer is 42"), [])  # bare number: no claim

    def test_circular_corroboration_detected(self):
        report = compare(*circular_pair())
        entries = [e for e in report["semantic"]["independence"]
                   if e["agent"] == "b" and e["circular"]]
        self.assertTrue(entries)
        entry = entries[0]
        self.assertEqual(entry["sources"], ["financeblog.net", "moneymirror.com"])
        self.assertIn("financeblog.net", entry["evidence"])
        # A cites one primary source only: no multi-source entry, no circularity
        self.assertFalse([e for e in report["semantic"]["independence"]
                          if e["agent"] == "a"])

    def test_grounding_flags_ungrounded_claim(self):
        a, b = circular_pair("t7g")
        a.outcome.answer = "$4.82 billion with 7.5% growth"  # 7.5% never observed
        report = compare(a, b)
        g = report["semantic"]["grounding"]["a"]
        self.assertEqual(g["claims_total"], 2)
        self.assertEqual(g["claims_grounded"], 1)
        self.assertEqual(g["score"], 0.5)
        self.assertEqual(len(g["ungrounded"]), 1)
        self.assertIn("7.5%", g["ungrounded"][0]["value"])
        # B's $4.5 billion is grounded in its own step outputs
        self.assertEqual(report["semantic"]["grounding"]["b"]["score"], 1.0)

    def test_intent_classification_and_missing(self):
        a_steps = [
            make_step(0, "plan", "plan", "outline the approach"),
            make_step(1, "search", "web_search", "double-check the revenue figure"),
            make_step(2, "retrieve", "select_result", "Open result [1]: choose the filing"),
            make_step(3, "tool_call", "calculator", "4.82 * 1e9"),
            make_step(4, "answer", "final", "done"),
        ]
        b_steps = [
            make_step(0, "plan", "plan", "outline the approach"),
            make_step(1, "search", "web_search", "acme revenue"),
            make_step(2, "answer", "final", "done"),
        ]
        a = Trajectory.from_json(make_traj("agent-a", "ti", True, "done", a_steps))
        b = Trajectory.from_json(make_traj("agent-b", "ti", True, "done", b_steps))
        semantic = compare(a, b)["semantic"]
        a_intents = [e["intent"] for e in semantic["intents"]["a"]]
        self.assertEqual(a_intents, ["frame", "verify", "decide", "transform", "commit"])
        b_intents = [e["intent"] for e in semantic["intents"]["b"]]
        self.assertEqual(b_intents, ["frame", "acquire", "commit"])
        self.assertIn("verify", semantic["intents"]["missing"]["b"])
        self.assertIn("acquire", semantic["intents"]["missing"]["a"])

    def test_conflicts_on_retrieval_pair(self):
        semantic = compare(*retrieval_pair())["semantic"]
        money = [c for c in semantic["conflicts"] if c["kind"] == "money"]
        self.assertTrue(money)
        summary = money[0]["summary"]
        self.assertIn("5.2 billion", summary)
        self.assertIn("12 billion", summary)
        self.assertIn("expected: 5.2 billion", summary)
        by_norm = {c["normalized"]: c for c in semantic["claims"]}
        self.assertTrue(by_norm["5.2e9"]["matches_expected"])
        self.assertFalse(by_norm["1.2e10"]["matches_expected"])

    def test_rows_and_first_semantic_break(self):
        semantic = compare(*retrieval_pair())["semantic"]
        self.assertEqual(len(semantic["rows"]), 5)
        row0 = semantic["rows"][0]
        self.assertEqual(row0["lexical"], 1.0)
        self.assertEqual(row0["semantic"], 1.0)
        for row in semantic["rows"]:
            self.assertGreaterEqual(row["semantic"], 0.0)
            self.assertLessEqual(row["semantic"], 1.0)
        self.assertIsNotNone(semantic["first_semantic_break"])
        self.assertGreaterEqual(semantic["first_semantic_break"], 2)
        clean = compare(*identical_pair())["semantic"]
        self.assertIsNone(clean["first_semantic_break"])

    def test_contradiction_vs_comparison(self):
        # B asserts $5 billion in reasoning but answers $7 billion: contradiction.
        b_steps = [
            make_step(0, "reason", "reason", "estimate the total",
                      output="the total is $5 billion"),
            make_step(1, "answer", "final", "$7 billion", output="$7 billion"),
        ]
        a_steps = [
            make_step(0, "reason", "reason", "estimate the total",
                      output="the total is $7 billion"),
            make_step(1, "answer", "final", "$7 billion", output="$7 billion"),
        ]
        a = Trajectory.from_json(make_traj("agent-a", "tc", True, "$7 billion", a_steps))
        b = Trajectory.from_json(make_traj("agent-b", "tc", False, "$7 billion", b_steps))
        semantic = compare(a, b)["semantic"]
        found = [c for c in semantic["contradictions"] if c["agent"] == "b"]
        self.assertTrue(found)
        self.assertEqual(found[0]["kind"], "money")
        # values co-mentioned in one step are a comparison, not a contradiction
        c_steps = [
            make_step(0, "reason", "reason", "compare tiers",
                      output="plans cost $39 and $49 per month"),
            make_step(1, "answer", "final", "$39 and $49", output="$39 and $49"),
        ]
        c = Trajectory.from_json(make_traj("agent-a", "td", True, "$39 and $49", c_steps))
        d = Trajectory.from_json(make_traj("agent-b", "td", True, "$39 and $49",
                                           copy.deepcopy(c_steps)))
        semantic2 = compare(c, d)["semantic"]
        self.assertEqual(semantic2["contradictions"], [])

    def test_semantic_profile_in_aggregate(self):
        agg = aggregate([compare(*retrieval_pair())])
        sp = agg["semantic_profile"]
        for side in ("a", "b"):
            for key in ("verification_rate", "grounding",
                        "circular_incidents", "contradictions"):
                self.assertIn(key, sp[side])
        self.assertTrue(sp["narrative"])
        self.assertEqual(aggregate([])["semantic_profile"], {})


class TestCounterfactual(unittest.TestCase):
    def test_retrieval_pair_counterfactual(self):
        report = compare(*retrieval_pair())
        cf = report["counterfactual"]
        self.assertIsNotNone(cf)
        self.assertIn("step 2", cf["premise"])
        self.assertEqual(cf["splice"], {"prefix_steps": [0, 1],
                                        "adopted_from": "a",
                                        "adopted_steps": [2, 3, 4]})
        est = cf["estimate"]
        self.assertEqual(est["outcome"], "success")
        self.assertEqual(est["steps"], 5)
        self.assertEqual(est["steps_delta"], 0)
        # B prefix 100+100 tokens + A suffix 3x100 = 500 vs B's real 800
        self.assertEqual(est["tokens"], 500)
        self.assertEqual(est["tokens_delta"], -300)
        self.assertEqual(cf["confidence"], "high")
        self.assertIn("ends in success", cf["narrative"])

    def test_null_when_not_applicable(self):
        self.assertIsNone(compare(*detour_pair())["counterfactual"])
        self.assertIsNone(compare(*identical_pair())["counterfactual"])
        self.assertIsNone(
            compare(*identical_pair("tf0", success=False))["counterfactual"]
        )

    def test_medium_confidence_with_drifted_prefix(self):
        from deepcompare.counterfactual import counterfactual
        a, b = retrieval_pair()
        report = compare(a, b)
        report["alignment"][1]["op"] = "drift"  # constructed drifted prefix
        cf = counterfactual(report, a, b)
        self.assertEqual(cf["confidence"], "medium")


class TestGate(unittest.TestCase):
    @staticmethod
    def _write(dirpath, trajectories):
        dirpath.mkdir(parents=True, exist_ok=True)
        for t in trajectories:
            path = dirpath / f"{t.task.id}__{t.agent.name}.json"
            path.write_text(json.dumps(t.to_dict()), encoding="utf-8")

    @staticmethod
    def _run(argv):
        from deepcompare.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code

    def _pairs(self, regress):
        a1, b1 = retrieval_pair("g1") if regress else identical_pair("g1")
        a2, b2 = identical_pair("g2")
        for t in (a1, a2):
            t.agent.name = "base-v1"
        for t in (b1, b2):
            t.agent.name = "cand-v2"
        return [a1, a2], [b1, b2]

    def test_gate_fail_with_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, cand = self._pairs(regress=True)
            self._write(root / "base", base)
            self._write(root / "cand", cand)
            code = self._run(["gate", str(root / "base"), str(root / "cand"),
                              "-o", str(root / "out"), "--markdown", "gate.md"])
            self.assertEqual(code, 1)
            gate = json.loads((root / "out" / "gate.json").read_text())
            self.assertEqual(gate["verdict"], "fail")
            by_name = {c["name"]: c for c in gate["checks"]}
            self.assertFalse(by_name["success_rate_drop"]["pass"])
            self.assertFalse(by_name["new_failure_modes"]["pass"])
            self.assertIn("retrieval", by_name["new_failure_modes"]["candidate"])
            self.assertTrue(
                [s for s in gate["reports_summary"]
                 if s["task"] == "g1" and s["regressed"]]
            )
            md = (root / "out" / "gate.md").read_text(encoding="utf-8")
            self.assertIn("FAIL", md)
            self.assertIn("g1", md)
            self.assertIn("Counterfactual:", md)
            self.assertIn("estimated run", md)

    def test_gate_fail_allows_new_modes_but_still_fails_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, cand = self._pairs(regress=True)
            self._write(root / "base", base)
            self._write(root / "cand", cand)
            code = self._run(["gate", str(root / "base"), str(root / "cand"),
                              "-o", str(root / "out"), "--allow-new-failure-modes"])
            self.assertEqual(code, 1)
            gate = json.loads((root / "out" / "gate.json").read_text())
            by_name = {c["name"]: c for c in gate["checks"]}
            self.assertTrue(by_name["new_failure_modes"]["pass"])
            self.assertIn("disabled", by_name["new_failure_modes"]["detail"])

    def test_gate_pass_when_equivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, cand = self._pairs(regress=False)
            self._write(root / "base", base)
            self._write(root / "cand", cand)
            code = self._run(["gate", str(root / "base"), str(root / "cand"),
                              "-o", str(root / "out")])
            self.assertEqual(code, 0)
            gate = json.loads((root / "out" / "gate.json").read_text())
            self.assertEqual(gate["verdict"], "pass")
            self.assertTrue(all(c["pass"] for c in gate["checks"]))

    def test_gate_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(["gate", str(Path(tmp) / "missing"), tmp, "-o", tmp])
            self.assertEqual(code, 2)


class TestFleet(unittest.TestCase):
    def test_ranking_and_pareto(self):
        result = fleet_analysis(fleet_fixture())
        fleet, reports = result["fleet"], result["reports"]
        agents = {a["name"]: a for a in fleet["agents"]}
        self.assertEqual([a["name"] for a in fleet["agents"]],
                         ["alpha", "charlie", "bravo", "delta"])
        self.assertEqual([a["rank"] for a in fleet["agents"]], [1, 2, 3, 4])
        self.assertTrue(agents["alpha"]["pareto"])
        self.assertTrue(agents["charlie"]["pareto"])
        self.assertFalse(agents["bravo"]["pareto"])
        self.assertEqual(agents["bravo"]["dominated_by"], 1)
        self.assertEqual(agents["delta"]["dominated_by"], 2)
        # dimension scores bounded and best-in-fleet success at 1.0
        for a in fleet["agents"]:
            for score in a["dimension_scores"].values():
                self.assertGreaterEqual(score, 0.0)
                self.assertLessEqual(score, 1.0)
        self.assertEqual(agents["alpha"]["dimension_scores"]["success"], 1.0)
        # wasted tool calls vs the leanest successful run (1 search/task)
        self.assertEqual(agents["bravo"]["metrics"]["wasted_tool_calls"], 2.0)
        self.assertEqual(agents["alpha"]["metrics"]["wasted_tool_calls"], 0.0)
        # rationales cite rank drivers and the success gap
        self.assertIn("Ranked #1", agents["alpha"]["rationale"])
        self.assertIn("Ranked #2", agents["charlie"]["rationale"])
        self.assertIn("below the success leader", agents["charlie"]["rationale"])
        # failure fingerprints
        self.assertEqual(agents["charlie"]["failure_fingerprint"], {"retrieval": 1.0})
        self.assertEqual(agents["alpha"]["failure_fingerprint"], {})
        # spotlight pairs reference valid report indices
        self.assertTrue(fleet["spotlight_pairs"])
        names = set(agents)
        for pair in fleet["spotlight_pairs"]:
            self.assertIn(pair["a"], names)
            self.assertIn(pair["b"], names)
            self.assertTrue(pair["why"])
            for idx in pair["report_indices"]:
                self.assertGreaterEqual(idx, 0)
                self.assertLess(idx, len(reports))
        self.assertTrue(reports)

    def test_weights_override_changes_ranking(self):
        cost_only = {"success": 0.0, "cost": 1.0, "latency": 0.0,
                     "tool_discipline": 0.0, "step_economy": 0.0}
        result = fleet_analysis(fleet_fixture(), weights=cost_only)
        self.assertEqual(result["fleet"]["agents"][0]["name"], "charlie")

    def test_single_agent_fleet(self):
        solo = {"solo": [fleet_traj("solo", "f1", True, 0.01, 5.0, 700),
                         fleet_traj("solo", "f2", True, 0.01, 5.0, 700)]}
        result = fleet_analysis(solo)
        fleet = result["fleet"]
        self.assertEqual(len(fleet["agents"]), 1)
        agent = fleet["agents"][0]
        self.assertEqual(agent["rank"], 1)
        self.assertAlmostEqual(agent["score"], 1.0, places=4)
        self.assertTrue(agent["pareto"])
        self.assertEqual(agent["failure_fingerprint"], {})
        self.assertEqual(fleet["spotlight_pairs"], [])
        self.assertEqual(result["reports"], [])

    def test_mismatched_task_sets_rejected(self):
        bad = {
            "one": [fleet_traj("one", "f1", True, 0.01, 5.0, 700)],
            "two": [fleet_traj("two", "f2", True, 0.01, 5.0, 700)],
        }
        with self.assertRaisesRegex(ValueError, "task set"):
            fleet_analysis(bad)


if __name__ == "__main__":
    unittest.main()
