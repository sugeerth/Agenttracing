"""Where the tokens went: the budget section counts what the steps
recorded and labels what they labelled.

What this pins: the split by kind and by tool sums to the total; a
measured, an estimated and an unlabelled count are counted under their
own labels and never re-estimated; the totals' input/output tokens and
cost are read when recorded and unmeasurable with a reason when not;
the three wastes; the burn cap; the pair delta; the aggregate with its
ledger and cap; the same bytes twice; and that the section attaches to
a report and an aggregate under its key with nothing else moving.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import sections  # noqa: E402
from deepcompare.budget import BURN_CAP, KINDS, TOP_STEPS, budget_aggregate, budget_pair, budget_run  # noqa: E402
from deepcompare.commands._io import load_traces  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

DEMO = ROOT / "demo" / "traces"
TRAIN = ROOT / "demo" / "rl" / "train"


def step(i, type_, name="", tokens=0, basis=None, **extra):
    d = {"index": i, "type": type_, "name": name or type_, "input": extra.pop("input", f"in{i}"),
         "output": extra.pop("output", f"out{i}"), "tokens": tokens, "latency_s": extra.pop("latency_s", 0.0)}
    if basis:
        d["tokens_basis"] = basis
    d.update(extra)
    return d


def trace(steps, agent="probe", task="t", run_id="r1", totals=None, harness=None, success=True):
    d = {"schema_version": 1, "trace_id": f"{task}-{agent}-{run_id}", "run_id": run_id,
         "agent": {"name": agent, "model": "", "version": ""}, "task": {"id": task, "prompt": "p", "expected": None},
         "outcome": {"success": success, "answer": "a", "score": None, "termination": None},
         "totals": totals or {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}, "steps": steps}
    t = Trajectory.from_dict(d)
    if harness:
        t.harness = harness
    return t


class RunTest(unittest.TestCase):
    def setUp(self):
        self.atlas = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")

    def test_the_demo_run_sums_its_steps_by_kind_and_tool(self):
        b = budget_run(self.atlas)
        self.assertTrue(b["measurable"])
        tk = b["tokens"]
        self.assertEqual(tk["total"], sum(s.tokens for s in self.atlas.steps))
        self.assertEqual(sum(tk["by_kind"].values()), tk["total"])
        self.assertEqual(list(tk["by_kind"]), list(KINDS))
        self.assertEqual(tk["by_tool"], {"open_page": 184, "select_result": 154, "web_search": 183})
        # the demo traces carry no tokens_basis: every token is unknown, none is invented as measured
        self.assertEqual((tk["measured"], tk["estimated"], tk["unknown"]), (0, 0, tk["total"]))
        # the demo steps carry no input/output split, so the whole-run
        # figure is the fallback and says which it is
        self.assertEqual(b["io"], {"measurable": True, "reason": None, "input_tokens": 625, "output_tokens": 215,
                                   "steps": 0, "of_steps": len(self.atlas.steps), "source": "totals"})
        self.assertEqual(b["cost_usd"]["value"], 0.0051)
        self.assertEqual([row[4] for row in b["burn"]][-1], tk["total"])
        self.assertFalse(b["burn_capped"])
        self.assertEqual(b["top"][0]["index"], 3)
        self.assertLessEqual(len(b["top"]), TOP_STEPS)
        self.assertTrue(b["per_second"]["measurable"])
        self.assertIn("840 tokens over 5 steps", b["narrative"])

    def test_labels_are_counted_never_re_estimated(self):
        t = trace([step(0, "plan", tokens=10, basis="measured"), step(1, "search", "s", tokens=0, basis="estimated"),
                   step(2, "read", "r", tokens=7), step(3, "answer", tokens=3, basis="estimated")])
        tk = budget_run(t)["tokens"]
        self.assertEqual((tk["measured"], tk["estimated"], tk["unknown"], tk["total"]), (10, 3, 7, 20))
        # an estimated 0 stays 0: it is the trace's estimate, not ours
        self.assertEqual(tk["by_tool"], {"r": 7, "s": 0})

    def test_unrecorded_totals_are_unmeasurable_not_zero(self):
        b = budget_run(trace([step(0, "answer", tokens=1)]))
        self.assertFalse(b["io"]["measurable"])
        self.assertIn("unrecorded is not zero", b["io"]["reason"])
        self.assertFalse(b["cost_usd"]["measurable"])
        self.assertIsNone(b["cost_usd"]["value"])
        self.assertFalse(b["per_second"]["measurable"])
        self.assertEqual(b["per_second"]["reason"], "no step recorded a latency")

    def test_the_three_wastes(self):
        t = trace([step(0, "plan", tokens=5), step(1, "search", "s", tokens=10, reward=1.0, input="q"),
                   step(2, "search", "s", tokens=11, reward=-0.1, input="q"),
                   step(3, "read", "r", tokens=12, error=True, reward=-1.0), step(4, "answer", tokens=6)])
        w = budget_run(t)["waste"]
        self.assertEqual(w["after_last_evidence"], 23)     # steps 2 and 3, not the answer
        self.assertEqual(w["in_errored_calls"], 12)
        self.assertEqual(w["in_repeats"], 11)
        no_signal = budget_run(trace([step(0, "search", "s", tokens=4), step(1, "answer", tokens=1)]))["waste"]
        self.assertIsNone(no_signal["after_last_evidence"])
        self.assertIn("no evidence signal recorded", no_signal["basis"])

    def test_the_burn_is_capped_with_a_note_and_the_total_is_not(self):
        n = BURN_CAP + 5
        steps = [step(i, "reason", tokens=1) for i in range(n - 1)] + [step(n - 1, "answer", tokens=1)]
        b = budget_run(trace(steps))
        self.assertTrue(b["burn_capped"])
        self.assertEqual(len(b["burn"]), BURN_CAP)
        self.assertEqual(b["tokens"]["total"], n)
        self.assertIn(str(n), b["burn_note"])

    def test_a_report_side_reads_the_same_as_the_trajectory(self):
        bolt = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        report = compare(self.atlas, bolt)
        self.assertEqual(budget_run(report["a"]), budget_run(self.atlas))
        self.assertEqual(budget_pair(report), budget_pair(report, self.atlas, bolt))

    def test_synthetic_and_empty(self):
        t = trace([step(0, "answer", tokens=1)], harness={"adapter": "synthetic", "note": "SYNTHETIC: made up"})
        self.assertTrue(budget_run(t)["synthetic"])
        self.assertFalse(budget_run(trace([step(0, "answer", tokens=1)]))["synthetic"])
        empty = budget_run({"agent": {"name": "x"}, "steps": []})
        self.assertFalse(empty["measurable"])
        self.assertEqual(empty["reason"], "the run has no steps")


class PairTest(unittest.TestCase):
    def test_the_pair_section_attaches_with_the_delta_a_minus_b(self):
        a = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        report = compare(a, b)
        sec = report["budget"]
        self.assertEqual(list(sec)[:3], ["version", "measurable", "reason"])
        self.assertEqual(sec["delta"]["total"], sec["a"]["tokens"]["total"] - sec["b"]["tokens"]["total"])
        self.assertEqual(sec["cheaper"], "atlas-v2")
        self.assertIn("atlas-v2 was cheaper", sec["narrative"])
        keys = list(report)
        self.assertEqual(keys[keys.index("rl"):], ["rl", "budget", "fetches", "data"])
        self.assertIn("budget", sections.registered("pair"))

    def test_a_tie_names_nobody(self):
        a = trace([step(0, "answer", tokens=5)], agent="a")
        b = trace([step(0, "answer", tokens=5)], agent="b")
        sec = budget_pair({"a": {"agent": {"name": "a"}}, "b": {"agent": {"name": "b"}}}, a, b)
        self.assertIsNone(sec["cheaper"])
        self.assertIn("they cost the same", sec["narrative"])


class AggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trajs = [t for t in load_traces(TRAIN, run_ids=True) if t.task.id.startswith("rl01_")]

    def test_agents_tasks_heaviest_and_the_ledger(self):
        sec = budget_aggregate(self.trajs)
        self.assertTrue(sec["measurable"])
        self.assertEqual(sorted(sec["agents"]), ["policy-v1", "policy-v2"])
        ag = sec["agents"]["policy-v1"]
        self.assertEqual(ag["runs"], 8)
        self.assertEqual(ag["total"], sum(r["tokens"] for r in sec["runs"] if r["agent"] == "policy-v1"))
        self.assertEqual(ag["measured_share"], 1.0)
        self.assertIsNone(ag["cost_usd_total"])
        self.assertEqual(len(sec["runs"]), 16)
        self.assertEqual(sec["heaviest_runs"][0]["tokens"], max(r["tokens"] for r in sec["runs"]))
        self.assertEqual(sec["cap"], {"value": None, "source": "none given", "over": []})
        self.assertTrue(all(r["synthetic"] for r in sec["runs"]))

    def test_the_cap_lists_the_runs_over_it(self):
        sec = budget_aggregate(self.trajs, token_cap=3000)
        self.assertEqual(sec["cap"]["value"], 3000)
        self.assertEqual(sec["cap"]["source"], "--token-cap")
        self.assertEqual([r["tokens"] for r in sec["cap"]["over"]],
                         sorted((r["tokens"] for r in sec["runs"] if r["tokens"] > 3000), reverse=True))
        self.assertIn("over the cap of 3000", sec["narrative"])

    def test_the_section_reads_the_cap_from_the_context(self):
        agg = sections.attach("aggregate", {}, sections.AggregateContext(trajectories=self.trajs, extra={"token_cap": 2500}),
                              only=("budget",))
        self.assertEqual(agg["budget"]["cap"]["value"], 2500)
        empty = sections.attach("aggregate", {}, sections.AggregateContext(), only=("budget",))
        self.assertFalse(empty["budget"]["measurable"])

    def test_the_runs_analysis_hands_the_cap_through(self):
        from deepcompare.suite import analyse_runs
        capped = analyse_runs(self.trajs, extra={"token_cap": 3000})["aggregate"]["budget"]
        self.assertEqual(capped["cap"]["value"], 3000)
        self.assertTrue(all(r["tokens"] > 3000 for r in capped["cap"]["over"]))
        plain = analyse_runs(self.trajs)["aggregate"]["budget"]
        self.assertEqual(plain["cap"]["source"], "none given")
        # only the cap and the sentence that names it move
        self.assertEqual({k: v for k, v in plain.items() if k not in ("cap", "narrative")},
                         {k: v for k, v in capped.items() if k not in ("cap", "narrative")})

    def test_same_input_same_bytes(self):
        one = json.dumps(budget_aggregate(self.trajs), sort_keys=False)
        two = json.dumps(budget_aggregate(list(reversed(self.trajs))), sort_keys=False)
        self.assertEqual(one, two)


if __name__ == "__main__":
    unittest.main()


class CachedInputTest(unittest.TestCase):
    """What was re-sent, and what was actually paid for.

    Providers report how much of a prompt they served from their own cache
    and this harness dropped it, so `tokens.total` counted re-sent context
    at full price and a cache that was working looked exactly like a
    provider that had none. `Step.cached_tokens` carries it.

    `cached` is `None` until a step reports one — never `0` — for the same
    reason `timeline.overlap_s` is: nothing here can tell a working cache
    from a provider that does not mention caching, and a zero would claim
    it could.
    """

    @staticmethod
    def _traj(rows, totals=None):
        steps = []
        for i, row in enumerate(rows):
            step = {"index": i, "type": "reason", "name": "think", "input": "x", "output": "y",
                    "tokens": row["tokens"], "tokens_basis": "measured", "latency_s": 0.1}
            for key in ("cached_tokens", "input_tokens", "output_tokens"):
                if key in row:
                    step[key] = row[key]
            steps.append(step)
        steps[-1].update(type="answer", name="final")
        return Trajectory.from_dict({
            "trace_id": "x", "agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"},
            "totals": dict({"latency_s": 0.1 * len(rows)}, **(totals or {})),
            "outcome": {"answer": "done", "success": True, "termination": "agent_stop"},
            "steps": steps})

    def test_the_split_is_read_from_the_steps_and_the_source_is_named(self):
        """A per-step count says *where* the context went; a whole-run
        figure cannot. Both are reported with their source rather than the
        reader having to guess which they got."""
        p = budget_run(self._traj([{"tokens": 420, "input_tokens": 400, "output_tokens": 20},
                                   {"tokens": 60, "input_tokens": 50, "output_tokens": 10},
                                   {"tokens": 5}]))
        io = p["io"]
        self.assertTrue(io["measurable"])
        self.assertEqual(io["source"], "steps")
        self.assertEqual((io["input_tokens"], io["output_tokens"]), (450, 30))
        self.assertEqual((io["steps"], io["of_steps"]), (2, 3), "the step with no split was counted as splitting")
        self.assertIn("450 in and 30 out, over 2 steps that split them", p["narrative"])

    def test_the_totals_are_the_fallback_and_say_so(self):
        p = budget_run(self._traj([{"tokens": 100}], totals={"input_tokens": 90, "output_tokens": 10}))
        self.assertEqual(p["io"]["source"], "totals")
        self.assertEqual((p["io"]["input_tokens"], p["io"]["output_tokens"]), (90, 10))
        self.assertNotIn("that split them", p["narrative"], "a whole-run figure was reported as per-step")

    def test_neither_is_unmeasurable_rather_than_zero(self):
        p = budget_run(self._traj([{"tokens": 100}]))
        self.assertFalse(p["io"]["measurable"])
        self.assertIsNone(p["io"]["input_tokens"])
        self.assertIn("unrecorded is not zero", p["io"]["reason"])

    def test_a_provider_that_says_nothing_leaves_cached_unknown_not_zero(self):
        tk = budget_run(self._traj([{"tokens": 100}, {"tokens": 50}]))["tokens"]
        self.assertIsNone(tk["cached"], "a silent provider was read as a cache that saved nothing")
        self.assertEqual(tk["cached_steps"], 0)
        self.assertEqual(tk["total"], 150)

    def test_the_cached_input_is_summed_over_the_steps_that_reported_it(self):
        p = budget_run(self._traj([{"tokens": 400, "cached_tokens": 300},
                                   {"tokens": 420, "cached_tokens": 380},
                                   {"tokens": 30}]))
        tk = p["tokens"]
        self.assertEqual(tk["cached"], 680)
        self.assertEqual(tk["cached_steps"], 2, "the step that said nothing was counted as reporting zero")
        self.assertEqual(tk["total"], 850)
        self.assertIn("680 of the input came from the provider's cache", p["narrative"])
        self.assertIn("2 steps that said so", p["narrative"])

    def test_a_reported_zero_is_a_measurement_and_is_kept(self):
        """Zero cached tokens is a provider saying the cache missed. That
        is information, and it is not the same as saying nothing."""
        tk = budget_run(self._traj([{"tokens": 100, "cached_tokens": 0}]))["tokens"]
        self.assertEqual(tk["cached"], 0)
        self.assertEqual(tk["cached_steps"], 1)

    def test_a_negative_or_fractional_count_is_refused(self):
        for bad in (-1, 2.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._traj([{"tokens": 10, "cached_tokens": bad}])

    def test_an_unreported_count_is_absent_from_the_trace_not_null(self):
        for step in self._traj([{"tokens": 10}]).to_dict()["steps"]:
            self.assertNotIn("cached_tokens", step)

    def test_both_provider_shapes_are_read(self):
        """OpenAI puts the cache read under `prompt_tokens_details`, inside
        the prompt count; the Anthropic API reports it beside an input
        count that excludes it. Both reach a step as one field, and a
        provider that says nothing about caching yields no key at all."""
        from deepcompare.harness.providers import anthropic_usage, openai_usage

        self.assertEqual(
            openai_usage({"prompt_tokens": 400, "completion_tokens": 20,
                          "prompt_tokens_details": {"cached_tokens": 300}}),
            {"input_tokens": 400, "output_tokens": 20, "cached_input_tokens": 300})
        self.assertEqual(
            anthropic_usage({"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300}),
            {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 300})
        for parse, quiet in ((openai_usage, {"prompt_tokens": 9, "completion_tokens": 1}),
                             (anthropic_usage, {"input_tokens": 9, "output_tokens": 1})):
            got = parse(quiet)
            self.assertNotIn("cached_input_tokens", got, "a silent provider produced a cache figure")
            self.assertEqual(got["input_tokens"], 9)
        self.assertEqual(openai_usage(None), {})
        self.assertEqual(anthropic_usage({"input_tokens": "lots"}), {},
                         "a non-integer count reached the trace as a measurement")
