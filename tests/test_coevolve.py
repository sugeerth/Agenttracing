"""The co-evolving eval: features, the spec language, values and deltas,
each probe's trigger on a synthetic step view, each validator on a
constructed case, the batch rule that picks one representative per
redundancy class, the walk on the two demo lineages with every real
result pinned (never eased), hindsight with learned flags apart from
base ones, the recommendation under both evals, the eval's integrity,
the flow graph, the section through ``evolve``, ``fail_on``, the
command end to end, and the runtime bound on the demo.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from deepcompare import coevolve as co  # noqa: E402
from deepcompare import evolve as ev  # noqa: E402
from deepcompare import sections  # noqa: E402
from deepcompare.cli import main  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402
from test_evolve import G0_ART, _agent, _trace, standard_gens, write_lineage  # noqa: E402

DEMO = ROOT / "demo" / "evolve" / "lineage"
DEMO_B = ROOT / "demo" / "evolve" / "lineage_b"
SAMPLES = 200


# ---------------------------------------------------------------- helpers

def ep(task: str, run: str, **vals) -> dict:
    """One feature-table row with readable defaults."""
    values = {"return": 1.0, "success": 1, "steps": 5, "tool_calls": 3, "tool_errors": 0, "distinct_tools": 2,
              "check_calls": 1, "verified": 1, "rewarded_tool_steps": 0, "retries": 0, "seconds": 3.0,
              "answer_chars": 10, "claims": 0, "unverified_claim": 0, "answer_share": 0.5, "errors_per_call": 0.0,
              "steps_after_last_tool": 1, "critic_error": 0.5, "tokens": 30, "uses:grep": 2, "uses:check": 1}
    values.update(vals)
    return {"id": f"{task}-{run}", "run": run, "task": task, "values": values}


def gen_rows(fn, tasks=("ta", "tb"), runs=("r1", "r2", "r3", "r4", "r5")) -> list:
    """``fn(task, k) -> dict of feature values`` for run k of each task."""
    return [ep(t, r, **fn(t, k)) for t in tasks for k, r in enumerate(runs)]


def base_metrics() -> dict:
    return {s["id"]: co._metric(dict(s, origin={"probe": "base"}), "base", {"probe": "base"}, None, None)
            for s in co.BASE_SPECS}


def make_view(gens: list, index: int, step=None, metrics=None, vocabulary=None, candidates=(), protected=()) -> co.StepView:
    """A step view over hand-built feature rows, ``gens[index - 1]`` the parent."""
    table = [{"id": f"g{i}", "index": i, "episodes": rows} for i, rows in enumerate(gens)]
    cache = co._Cache(table, SAMPLES)
    ids = [row["id"] for row in table]
    tools = [{k[5:] for e in rows for k, v in e["values"].items() if k.startswith("uses:") and v} for rows in gens]
    claims = [any(e["values"].get("claims") for e in rows) for rows in gens]
    step = dict({"from": ids[index - 1], "to": ids[index], "index": index, "verdict": "flat", "flags": [],
                 "effect": {"measurable": True, "axes_disagree": False, "forgotten": [],
                            "pass_rate": {"delta": 0.0}}, "gaming": {"sign_only": False}}, **(step or {}))
    return co.StepView(index=index, frm=ids[index - 1], to=ids[index], step=step, parent=gens[index - 1], child=gens[index],
                       so_far=cache.episodes(ids[index]), gens_so_far=ids[:index + 1], metrics=metrics or base_metrics(),
                       tools_child=tools[index], tools_earlier=tools[:index], claims_child=claims[index],
                       claims_earlier=any(claims[:index]), candidates=list(candidates), cache=cache,
                       vocabulary=vocabulary or co._vocabulary(table, index), protected=list(protected))


def spec(sid, feature, agg, direction="up", where=None):
    return co.parse_spec({"id": sid, "feature": feature, "agg": agg, "where": where, "direction": direction},
                         list(co.FEATURES) + ["uses:grep", "uses:check", "uses:x"])


class _Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coevolve-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- features

class FeatureTest(_Temp):
    def test_every_feature_on_a_hand_built_episode(self):
        t = Trajectory.from_dict(_trace("g0", "ta", "r1", True, check=True))
        f = co.features(t, ["config.checks", "tools.check"])
        self.assertEqual({k: f[k] for k in co.FEATURES if k not in ("answer_share", "seconds")},
                         {"return": 4.8, "success": 1, "steps": 4, "tool_calls": 2, "tool_errors": 0, "distinct_tools": 2,
                          "check_calls": 1, "verified": 1, "rewarded_tool_steps": 0, "retries": 0, "answer_chars": 18,
                          "claims": 1, "unverified_claim": 0, "errors_per_call": 0.0, "steps_after_last_tool": 1,
                          "critic_error": None, "tokens": 40})
        self.assertAlmostEqual(f["seconds"], 4.0)
        self.assertAlmostEqual(f["answer_share"], 5.0 / 5.2, places=6)
        self.assertEqual((f["uses:check"], f["uses:grep"]), (1, 1))
        self.assertEqual(sorted(k for k in f if k.startswith("uses:")), ["uses:check", "uses:grep"])

    def test_the_gamed_episode_reads_as_it_was_built(self):
        t = Trajectory.from_dict(_trace("g2", "ta", "r1", True, check=False, greps=8, grep_reward=1.0))
        f = co.features(t, ["tools.check"])
        self.assertEqual((f["return"], f["tool_calls"], f["check_calls"], f["verified"], f["rewarded_tool_steps"],
                          f["retries"], f["unverified_claim"], f["distinct_tools"]), (13.0, 8, 0, 0, 8, 7, 1, 1))
        self.assertNotIn("uses:check", f)

    def test_a_protected_tool_counts_as_a_check_whatever_its_name(self):
        d = _trace("g0", "ta", "r1", True, check=True)
        for st in d["steps"]:
            if st["name"] == "check":
                st["name"] = "audit"
        t = Trajectory.from_dict(d)
        self.assertEqual(co.features(t, [])["check_calls"], 0)
        self.assertEqual(co.features(t, ["tools.audit"])["check_calls"], 1)
        self.assertEqual(co.features(t, ["tools.audit"])["verified"], 1)

    def test_errors_and_the_ratio(self):
        t = Trajectory.from_dict(_trace("g0", "ta", "r2", False, check=True, greps=2, error_at_grep=True))
        f = co.features(t, [])
        self.assertEqual((f["tool_errors"], f["tool_calls"]), (1, 3))
        self.assertAlmostEqual(f["errors_per_call"], 1 / 3)
        self.assertEqual(f["success"], 0)

    def test_what_cannot_be_read_is_none_not_zero(self):
        d = {"schema_version": 1, "trace_id": "x", "run_id": "r1", "agent": {"name": "a"},
             "task": {"id": "t", "prompt": "p"}, "outcome": {"success": False, "answer": ""},
             "totals": {}, "steps": [{"index": 0, "type": "reason", "name": "think"}, {"index": 1, "type": "answer", "name": "final"}]}
        f = co.features(Trajectory.from_dict(d), [])
        self.assertIsNone(f["return"], "no step records a reward")
        self.assertIsNone(f["tokens"], "no step carries a token count")
        self.assertIsNone(f["critic_error"], "no step carries a value")
        self.assertIsNone(f["answer_chars"], "no answer text")
        self.assertIsNone(f["claims"])
        self.assertIsNone(f["unverified_claim"])
        self.assertIsNone(f["answer_share"])
        self.assertIsNone(f["errors_per_call"], "no tool call")
        self.assertIsNone(f["steps_after_last_tool"], "no tool step")
        self.assertEqual((f["steps"], f["tool_calls"], f["verified"], f["success"]), (2, 0, 0, 0))

    def test_critic_error_is_the_mean_absolute_residual_against_the_discounted_return_to_go(self):
        from deepcompare.rl import GAMMA
        from deepcompare.rlaudit import discounted_to_go
        d = _trace("g0", "ta", "r1", True, check=True)
        d["steps"][0]["value"] = 3.0
        d["steps"][2]["value"] = 1.0
        t = Trajectory.from_dict(d)
        to_go = discounted_to_go([s["reward"] for s in d["steps"]], GAMMA)
        self.assertAlmostEqual(co.features(t, [])["critic_error"], (abs(3.0 - to_go[0]) + abs(1.0 - to_go[2])) / 2, places=6)

    def test_the_feature_table_fills_every_dynamic_tool_across_the_lineage(self):
        lineage = ev.read_lineage(write_lineage(self.tmp / "lin", standard_gens()))
        table = co.feature_table(lineage)
        self.assertEqual([row["id"] for row in table], ["g0", "g1", "g2"])
        self.assertTrue(all(len(row["episodes"]) == 6 for row in table))
        g2 = table[2]["episodes"][0]
        self.assertEqual(g2["values"]["uses:check"], 0, "g2 never calls check, so the dynamic feature is 0 there")
        self.assertEqual(g2["values"]["uses:grep"], 8)
        self.assertEqual(co._vocabulary(table), list(co.FEATURES) + ["uses:check", "uses:grep"])
        self.assertEqual(co._vocabulary(table, 0), list(co.FEATURES) + ["uses:check", "uses:grep"])
        self.assertEqual({e["task"] for e in table[0]["episodes"]}, {"ta", "tb"})

    @unittest.skipUnless(DEMO.is_dir(), "the evolve demo lineage is not generated")
    def test_demo_episodes_read_as_the_lineage_was_built(self):
        lineage = ev.read_lineage(DEMO)
        table = co.feature_table(lineage)
        g0, g3 = table[0]["episodes"], table[3]["episodes"]
        self.assertTrue(all(e["values"]["verified"] == 1 and e["values"]["uses:run_check"] > 0 for e in g0))
        self.assertTrue(all(e["values"]["verified"] == 0 and e["values"]["uses:run_check"] == 0 for e in g3))
        self.assertTrue(all(e["values"]["critic_error"] is not None and e["values"]["tokens"] for e in g0))
        self.assertTrue(all(e["values"]["claims"] == 0 for row in table for e in row["episodes"]))


# ---------------------------------------------------------------- the spec language

class SpecTest(unittest.TestCase):
    def test_a_valid_spec_is_normalised_in_a_fixed_order(self):
        s = co.parse_spec({"id": "verified_rate", "feature": "verified", "agg": "rate", "direction": "up",
                           "where": {"feature": "success", "op": "==", "value": True}})
        self.assertEqual(list(s), ["id", "name", "feature", "agg", "where", "direction", "origin"])
        self.assertEqual(s["name"], "verification rate")
        self.assertEqual(s["where"], {"feature": "success", "op": "==", "value": 1}, "a bool value is 1 / 0")
        self.assertIsNone(s["origin"])
        self.assertEqual(co.parse_spec({"id": "x", "feature": "return", "agg": "iqm"})["direction"], "neutral")
        self.assertEqual(co.parse_spec({"id": "x", "feature": "success", "agg": "task_min"})["name"], "worst task pass")

    def test_every_refusal_names_its_reason(self):
        cases = [
            ("a list", "must be an object"),
            ({"id": "bad id!", "feature": "return", "agg": "mean"}, "spec id must match"),
            ({"feature": "return", "agg": "mean"}, "spec id must match"),
            ({"id": "x", "feature": "nope", "agg": "mean"}, "unknown feature \"nope\""),
            ({"id": "x", "feature": "return", "agg": "median"}, "unknown aggregation \"median\""),
            ({"id": "x", "feature": "return", "agg": "mean", "direction": "sideways"}, "unknown direction"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": "success"}, "where must be an object"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": {"feature": "success", "op": "~", "value": 1}},
             "unknown operator '~'"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": {"feature": "ghost", "op": "==", "value": 1}},
             "unknown feature 'ghost'"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": {"feature": "success", "op": "==", "value": "yes"}},
             "finite number as value"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": {"all": []}}, "where.all must be a non-empty list"),
            ({"id": "x", "feature": "return", "agg": "mean", "where": {"all": [{"feature": "success", "op": ">", "value": 1e400}]}},
             "finite number"),
            ({"id": "x", "feature": "uses:grep", "agg": "mean"}, "unknown feature \"uses:grep\""),
            ({"id": "x", "feature": "return", "agg": "mean", "name": 3}, "name must be a string"),
            ({"id": "x", "feature": "return", "agg": "mean", "origin": "me"}, "origin must be an object"),
        ]
        for obj, reason in cases:
            with self.subTest(obj=obj):
                with self.assertRaises(ValueError) as caught:
                    co.parse_spec(obj)
                self.assertIn(reason, str(caught.exception))

    def test_a_dynamic_feature_is_known_once_the_vocabulary_lists_it(self):
        s = co.parse_spec({"id": "x", "feature": "uses:grep", "agg": "rate"}, list(co.FEATURES) + ["uses:grep"])
        self.assertEqual(s["name"], "calls of grep rate")

    def test_the_filter_semantics(self):
        one = {"feature": "success", "op": "==", "value": 1}
        both = {"all": [one, {"feature": "tool_calls", "op": "<=", "value": 3}]}
        self.assertTrue(co._matches(None, {}))
        self.assertTrue(co._matches(one, {"success": 1}))
        self.assertFalse(co._matches(one, {"success": 0}))
        self.assertFalse(co._matches(one, {}), "an unreadable feature does not satisfy a predicate")
        self.assertTrue(co._matches(both, {"success": 1, "tool_calls": 3}))
        self.assertFalse(co._matches(both, {"success": 1, "tool_calls": 4}))
        for op, ok in (("!=", True), (">", False), ("<", True), (">=", False)):
            self.assertEqual(co._matches({"feature": "x", "op": op, "value": 2}, {"x": 1}), ok, op)
        self.assertTrue(co._outcome_spec(spec("a", "success", "rate")))
        self.assertTrue(co._conditioned_on_outcome(spec("a", "tool_calls", "mean", where=one)))
        self.assertFalse(co._outcome_spec(spec("a", "tool_calls", "mean")))
        self.assertTrue(co._episode_level(spec("a", "tool_calls", "mean")))
        self.assertFalse(co._episode_level(spec("a", "tool_calls", "mean", where=one)))
        self.assertFalse(co._episode_level(spec("a", "success", "task_min")))
        self.assertEqual(co._spec_key(spec("a", "success", "rate")), co._spec_key(spec("b", "success", "rate")))


# ---------------------------------------------------------------- values and deltas

class ValueDeltaTest(unittest.TestCase):
    def setUp(self):
        # ta: 4/5 pass, tool calls 1..5; tb: 1/5 pass, tool calls 6..10
        self.eps = gen_rows(lambda t, k: {"success": (1 if k < 4 else 0) if t == "ta" else (1 if k == 0 else 0),
                                          "tool_calls": k + 1 + (5 if t == "tb" else 0), "return": float(k)})

    def test_points_by_hand_for_every_aggregation(self):
        self.assertAlmostEqual(co.value(spec("a", "success", "rate"), self.eps, SAMPLES)["point"], 0.5)
        self.assertAlmostEqual(co.value(spec("a", "tool_calls", "mean"), self.eps, SAMPLES)["point"], 5.5)
        self.assertAlmostEqual(co.value(spec("a", "success", "task_mean"), self.eps, SAMPLES)["point"], 0.5)
        self.assertAlmostEqual(co.value(spec("a", "success", "task_min"), self.eps, SAMPLES)["point"], 0.2)
        self.assertAlmostEqual(co.value(spec("a", "success", "task_spread"), self.eps, SAMPLES)["point"], 0.6)
        # the task-balanced IQM trims one of five from each end: mean of the middle three
        self.assertAlmostEqual(co.value(spec("a", "return", "iqm"), self.eps, SAMPLES)["point"], 2.0)
        self.assertAlmostEqual(co.value(spec("a", "tool_calls", "rate"), self.eps, SAMPLES)["point"], 1.0, msg="a rate of a count is the fraction positive")

    def test_the_interval_is_an_interval_and_the_stream_is_seeded_by_the_spec_id(self):
        v = co.value(spec("a", "success", "rate"), self.eps, SAMPLES)
        self.assertEqual(list(v)[:2], ["measurable", "reason"])
        self.assertTrue(v["measurable"])
        self.assertLess(v["lo"], v["point"])
        self.assertGreater(v["hi"], v["point"])
        self.assertEqual((v["n"], v["tasks"], v["coverage"]), (10, 2, 1.0))
        self.assertIn("stratified bootstrap", v["basis"])
        self.assertEqual(v, co.value(spec("a", "success", "rate"), self.eps, SAMPLES), "deterministic")
        other = co.value(spec("b", "success", "rate"), self.eps, SAMPLES)
        self.assertEqual(other["point"], v["point"])
        self.assertNotEqual((other["lo"], other["hi"]), (v["lo"], v["hi"]), "another id, another stream")

    def test_stratification_keeps_a_constant_task_constant(self):
        eps = gen_rows(lambda t, k: {"success": 0 if t == "ta" else k % 2})
        v = co.value(spec("a", "success", "task_min"), eps, SAMPLES)
        self.assertEqual((v["point"], v["lo"], v["hi"]), (0.0, 0.0, 0.0), "ta is all fails in every resample, so the worst task never moves")
        by_task = {"ta": [0.0] * 5, "tb": [0.0, 1.0, 0.0, 1.0, 0.0]}
        boots = co._bootstrap("task_mean", by_task, 50, co.rng(1, "x", section="coevolve"))
        self.assertEqual(len(boots), 50)
        self.assertTrue(all(0.0 <= b <= 0.5 for b in boots), "ta contributes 0 to every resample's task mean")

    def test_a_filter_narrows_n_and_says_so(self):
        v = co.value(spec("a", "tool_calls", "mean", where={"feature": "success", "op": "==", "value": 1}), self.eps, SAMPLES)
        self.assertEqual(v["n"], 5)
        self.assertAlmostEqual(v["point"], (1 + 2 + 3 + 4 + 6) / 5)
        self.assertIn("among the episodes where success == 1", v["basis"])

    def test_unmeasurable_under_min_n_and_under_coverage(self):
        few = co.value(spec("a", "success", "rate"), self.eps[:3], SAMPLES)
        self.assertFalse(few["measurable"])
        self.assertIn("3 episodes after the filter, under the 4 needed", few["reason"])
        self.assertEqual((few["point"], few["lo"], few["hi"], few["n"]), (None, None, None, 3))
        eps = [dict(e, values=dict(e["values"], critic_error=None if k < 3 else 1.0)) for k, e in enumerate(self.eps)]
        v = co.value(spec("a", "critic_error", "mean"), eps, SAMPLES)
        self.assertFalse(v["measurable"])
        self.assertIn("unreadable on 3 of 10 episodes (coverage 0.7 under 0.8)", v["reason"])

    def test_delta_excludes_zero_and_widens_with_the_level(self):
        child = gen_rows(lambda t, k: {"success": 1, "tool_calls": k + 1})
        d = co.delta(spec("a", "success", "rate"), self.eps, child, SAMPLES, co.ALPHA)
        self.assertTrue(d["measurable"])
        self.assertAlmostEqual(d["point"], 0.5)
        self.assertTrue(d["excludes_zero"])
        self.assertEqual((d["n_from"], d["n_to"], d["alpha"]), (10, 10, co.ALPHA))
        self.assertEqual((d["from"], d["to"]), (0.5, 1.0))
        self.assertLess(d["lo"], d["hi"])
        strict = co.delta(spec("a", "success", "rate"), self.eps, child, SAMPLES, 0.005)
        self.assertLessEqual(strict["lo"], d["lo"])
        flat = co.delta(spec("a", "tool_calls", "mean"), self.eps, self.eps, SAMPLES, co.ALPHA)
        self.assertFalse(flat["excludes_zero"])
        self.assertEqual(flat["point"], 0.0)
        gone = co.delta(spec("a", "success", "rate"), self.eps, child[:2], SAMPLES, co.ALPHA)
        self.assertFalse(gone["measurable"])
        self.assertIn("unmeasurable on the child", gone["reason"])

    def test_the_cache_returns_the_same_numbers_as_the_public_functions(self):
        table = [{"id": "g0", "index": 0, "episodes": self.eps},
                 {"id": "g1", "index": 1, "episodes": gen_rows(lambda t, k: {"success": 1})}]
        cache = co._Cache(table, SAMPLES)
        s = spec("a", "success", "rate")
        self.assertEqual(cache.value(s, "g0"), co.value(s, self.eps, SAMPLES))
        self.assertEqual(cache.delta(s, "g0", "g1", 0.01), co.delta(s, self.eps, table[1]["episodes"], SAMPLES, 0.01))
        self.assertIs(cache.value(s, "g0"), cache.value(s, "g0"), "computed once")
        self.assertEqual(len(cache.episodes("g1")), 20)


# ---------------------------------------------------------------- probes

class ProbeTriggerTest(unittest.TestCase):
    def setUp(self):
        self.g0 = gen_rows(lambda t, k: {"success": k % 2, "verified": 1, "check_calls": 2, "uses:check": 2})
        self.g1 = gen_rows(lambda t, k: {"success": (k + 1) % 2, "verified": 1, "check_calls": 2, "uses:check": 2})
        self.probes = {p.name: p for p in co.PROBES}
        self.assertEqual([p.name for p in co.PROBES],
                         ["axes", "ceiling", "novelty", "forgetting", "goodhart", "redundancy", "external"])

    def test_axes_fires_on_disagreement_sign_only_or_gamed(self):
        axes = self.probes["axes"]
        self.assertFalse(axes.trigger(make_view([self.g0, self.g1], 1)))
        self.assertTrue(axes.trigger(make_view([self.g0, self.g1], 1, step={"effect": {"measurable": True, "axes_disagree": True, "forgotten": [], "pass_rate": {"delta": 0}}})))
        self.assertTrue(axes.trigger(make_view([self.g0, self.g1], 1, step={"gaming": {"sign_only": True}})))
        self.assertTrue(axes.trigger(make_view([self.g0, self.g1], 1, step={"verdict": "gamed"})))

    def test_axes_proposes_unadopted_features_ranked_by_standardised_shift(self):
        child = gen_rows(lambda t, k: {"success": 0, "verified": 0, "check_calls": 0, "uses:check": 0, "retries": k})
        view = make_view([self.g0, child], 1, step={"verdict": "gamed", "effect": {"measurable": True, "axes_disagree": True,
                                                                                    "forgotten": [], "pass_rate": {"delta": -0.5}}})
        shifts = co.feature_shifts(view)
        self.assertEqual([r["feature"] for r in shifts if r["separates"]], ["check_calls", "uses:check", "verified"])
        self.assertEqual(shifts[0]["standardised"], None)
        self.assertEqual(shifts[0]["sign_vs_outcome"], "up", "the feature fell with the outcome")
        proposed = self.probes["axes"].propose(view)
        self.assertEqual([p["id"] for p in proposed], ["check_calls_mean", "uses_check_mean", "verified_rate"])
        self.assertEqual([p["agg"] for p in proposed], ["mean", "mean", "rate"], "a bool is a rate, the rest a mean")
        self.assertTrue(all(p["direction"] == "up" for p in proposed))
        self.assertEqual(len(proposed), co.PROBE_TOP)
        for f in ("return", "success", "tool_calls", "tool_errors"):
            self.assertNotIn(f, [p["feature"] for p in proposed], "a base feature is already adopted")
        # a feature that moved against the outcome is proposed as down
        self.assertEqual(next(r for r in shifts if r["feature"] == "retries")["sign_vs_outcome"], "down")

    def test_ceiling_fires_on_a_saturated_rate_or_a_flat_interval(self):
        full = gen_rows(lambda t, k: {"success": 1, "tool_calls": k + 1})
        self.assertFalse(self.probes["ceiling"].trigger(make_view([self.g0, self.g1], 1)))
        view = make_view([self.g0, full], 1)
        self.assertTrue(self.probes["ceiling"].trigger(view))
        self.assertEqual(co._saturated(view, view.metrics["pass_rate"]), "pass_rate is 1 on g1")
        per_task = gen_rows(lambda t, k: {"success": 1 if t == "ta" else 0})
        v2 = make_view([self.g0, per_task], 1)
        self.assertEqual(co._saturated(v2, v2.metrics["pass_rate"]), "pass_rate is 0 or 1 on every task of g1")
        proposed = self.probes["ceiling"].propose(view)
        self.assertEqual([p["id"] for p in proposed], ["verified_pass_rate", "clean_pass_rate", "frugal_pass_rate"])
        self.assertEqual(proposed[2]["where"], {"feature": "tool_calls", "op": "<=", "value": 3.0}, "the parent's median, fixed")
        self.assertTrue(all(p["feature"] == "success" and p["agg"] == "rate" for p in proposed))
        self.assertEqual(proposed[0]["rank"]["because"], ["pass_rate is 1 on g1"])

    def test_novelty_fires_on_a_tool_that_appears_or_vanishes_or_a_claim(self):
        novelty = self.probes["novelty"]
        self.assertFalse(novelty.trigger(make_view([self.g0, self.g1], 1)))
        appeared = [dict(e, values=dict(e["values"], **{"uses:x": 1})) for e in self.g1]
        v = make_view([self.g0, appeared], 1)
        self.assertTrue(novelty.trigger(v))
        self.assertEqual([p["id"] for p in novelty.propose(v)], ["uses_x_rate"])
        self.assertEqual(novelty.propose(v)[0]["direction"], "neutral")
        vanished = [dict(e, values=dict(e["values"], **{"uses:check": 0})) for e in self.g1]
        v = make_view([self.g0, vanished], 1)
        self.assertTrue(novelty.trigger(v))
        self.assertEqual(novelty.propose(v)[0]["feature"], "uses:check")
        claims = [dict(e, values=dict(e["values"], claims=1)) for e in self.g1]
        v = make_view([self.g0, claims], 1)
        self.assertTrue(novelty.trigger(v))
        self.assertEqual([(p["id"], p["direction"]) for p in novelty.propose(v)], [("claims_rate", "down")])
        # a tool one ancestor dropped and another used is not "every earlier generation called"
        self.assertFalse(novelty.trigger(make_view([self.g0, vanished, self.g1], 2)))

    def test_forgetting_fires_on_a_forgotten_task(self):
        forgetting = self.probes["forgetting"]
        self.assertFalse(forgetting.trigger(make_view([self.g0, self.g1], 1)))
        v = make_view([self.g0, self.g1], 1, step={"effect": {"measurable": True, "axes_disagree": False, "forgotten": ["tb"], "pass_rate": {"delta": 0}}})
        self.assertTrue(forgetting.trigger(v))
        self.assertEqual([(p["id"], p["agg"], p["direction"]) for p in forgetting.propose(v)],
                         [("worst_task_pass", "task_min", "up"), ("pass_task_spread", "task_spread", "down")])

    def test_goodhart_demotes_a_metric_the_agent_moved_twice_without_the_outcome(self):
        g0 = gen_rows(lambda t, k: {"success": k % 2, "retries": 0.0})
        g1 = gen_rows(lambda t, k: {"success": k % 2, "retries": 3.0 + 0.1 * k})
        g2 = gen_rows(lambda t, k: {"success": k % 2, "retries": 6.0 + 0.1 * k})
        metrics = base_metrics()
        s = spec("retries_mean", "retries", "mean", "up")
        metrics["retries_mean"] = co._metric(s, "adopted", {"probe": "axes"}, None, {"step": "g0→g1", "index": 1, "eval_gen": "e1", "ledger": 0})
        goodhart = self.probes["goodhart"]
        self.assertFalse(goodhart.trigger(make_view([g0, g1, g2], 1, metrics=metrics)), "no previous step yet")
        v = make_view([g0, g1, g2], 2, metrics=metrics)
        self.assertTrue(goodhart.trigger(v))
        proposed = goodhart.propose(v)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["id"], "retries_mean_on_pass")
        self.assertEqual(proposed[0]["where"], {"feature": "success", "op": "==", "value": 1})
        self.assertEqual(proposed[0]["rank"]["demotes"], "retries_mean")
        # the outcome rising on either step, or the metric being an outcome metric, clears it
        up = gen_rows(lambda t, k: {"success": 1, "retries": 6.0 + 0.1 * k})
        self.assertFalse(goodhart.trigger(make_view([g0, g1, up], 2, metrics=metrics)))
        metrics["retries_mean"]["status"] = "demoted"
        self.assertFalse(goodhart.trigger(v))

    def test_external_fires_only_with_candidates_for_the_step(self):
        ext = self.probes["external"]
        self.assertFalse(ext.trigger(make_view([self.g0, self.g1], 1)))
        c = [{"at": None, "spec": {"id": "x", "feature": "retries", "agg": "mean"}}]
        v = make_view([self.g0, self.g1], 1, candidates=c)
        self.assertTrue(ext.trigger(v))
        self.assertEqual(ext.propose(v), c)
        self.assertEqual(co._candidates_for([{"at": "g0→g1", "spec": {}}, {"at": "g5→g6", "spec": {}}, {"at": None, "spec": {}}, 7], "g0→g1"),
                         [{"at": "g0→g1", "spec": {}, "entry": 0}, {"at": None, "spec": {}, "entry": 2},
                          {"at": "g0→g1", "rejected": "not an object: 7", "source": None, "entry": 3}])


# ---------------------------------------------------------------- validators

class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.g0 = gen_rows(lambda t, k: {"success": k % 2, "verified": 1, "distinct_tools": 2, "retries": k,
                                         "critic_error": None if k == 0 else 1.0})
        self.g1 = gen_rows(lambda t, k: {"success": 0, "verified": 0, "distinct_tools": 1, "retries": 5 + k,
                                         "critic_error": None if k == 0 else 1.0, "uses:check": 0})
        self.view = make_view([self.g0, self.g1], 1, step={"verdict": "gamed"}, protected=["tools.check"])
        self.assertEqual([n for n, _ in co.VALIDATORS], ["computable", "informative", "distinct", "linked", "not_already"])

    def test_computable_needs_coverage_and_both_sides(self):
        ok = co._v_computable(spec("a", "verified", "rate"), "axes", self.view, 0.05)
        self.assertTrue(ok["pass"])
        self.assertEqual(ok["coverage"], 1.0)
        self.assertEqual((ok["from"]["point"], ok["to"]["point"]), (1.0, 0.0))
        thin = co._v_computable(spec("a", "critic_error", "mean"), "axes", self.view, 0.05)
        self.assertTrue(thin["pass"], "readable on 16 of 20 is exactly the 0.8 floor")
        eps = [dict(e, values=dict(e["values"], tokens=None if k % 2 else 1)) for k, e in enumerate(self.g0)]
        v = make_view([eps, eps], 1)
        bad = co._v_computable(spec("a", "tokens", "mean"), "axes", v, 0.05)
        self.assertFalse(bad["pass"])
        self.assertIn("readable on 10 of 20 episodes so far (0.5 under 0.8)", bad["note"])
        side = co._v_computable(spec("a", "success", "rate", where={"feature": "verified", "op": "==", "value": 1}), "ceiling", self.view, 0.05)
        self.assertFalse(side["pass"])
        self.assertIn("unmeasurable on g1: 0 episodes after the filter", side["note"])

    def test_informative_is_the_delta_at_the_adjusted_level_and_says_when_noise_hides_it(self):
        r = co._v_informative(spec("a", "verified", "rate"), "axes", self.view, 0.05)
        self.assertTrue(r["pass"])
        self.assertEqual(r["delta"], {"point": -1.0, "lo": -1.0, "hi": -1.0, "excludes_zero": True})
        self.assertIn("at level 0.95 excludes zero", r["note"])
        same = make_view([self.g0, self.g0], 1)
        r = co._v_informative(spec("a", "retries", "mean"), "axes", same, 0.01)
        self.assertFalse(r["pass"])
        self.assertIn("at level 0.99 includes zero: at 5 runs per task mean retries cannot be told from noise at the adjusted level", r["note"])
        # ceiling candidates are judged on the child's point instead
        full = gen_rows(lambda t, k: {"success": 1})
        v = make_view([self.g0, full], 1)
        r = co._v_informative(spec("a", "success", "rate"), "ceiling", v, 0.05)
        self.assertFalse(r["pass"])
        self.assertIn("is at a bound or has no width", r["note"])
        r = co._v_informative(spec("a", "success", "rate"), "ceiling", self.view.__class__(**{**self.view.__dict__, "to": "g0", "child": self.g0}), 0.05)
        self.assertTrue(r["pass"])
        self.assertIn("sits strictly inside (0, 1) with width", r["note"])

    def test_distinct_against_adopted_metrics_on_the_right_basis(self):
        metrics = base_metrics()
        metrics["verified_rate"] = co._metric(spec("verified_rate", "verified", "rate"), "adopted", {"probe": "axes"}, None,
                                              {"step": "g0→g1", "index": 1, "eval_gen": "e1", "ledger": 0})
        view = make_view([self.g0, self.g1], 1, metrics=metrics)
        r = co._v_distinct(spec("a", "distinct_tools", "mean"), "axes", view, 0.05)
        self.assertFalse(r["pass"])
        self.assertEqual(r["max_rho"], 1.0)
        self.assertIn("max |ρ| 1 against verified_rate, at or over 0.9", r["note"])
        by = {a["metric"]: a for a in r["against"]}
        self.assertEqual(by["verified_rate"]["basis"], "episodes")
        self.assertEqual(by["return_iqm"]["basis"], "generations")
        self.assertFalse(by["return_iqm"]["testable"])
        self.assertIn("not testable yet: 2 generations measurable on both, 4 needed", by["return_iqm"]["note"])
        r = co._v_distinct(spec("a", "retries", "mean"), "axes", view, 0.05)
        self.assertTrue(r["pass"])
        # a task-level candidate has no adopted task-level metric to test against until four generations exist
        r = co._v_distinct(spec("a", "success", "task_min"), "forgetting", view, 0.05)
        self.assertTrue(r["pass"])
        self.assertEqual(r["note"], "not testable yet")
        gens = [self.g0, self.g1, self.g0, self.g1]
        v4 = make_view(gens, 3, metrics=metrics)
        r = co._v_distinct(spec("a", "success", "task_min"), "forgetting", v4, 0.05)
        self.assertEqual({a["metric"]: a["basis"] for a in r["against"]}, {m: "generations" for m in metrics})
        self.assertTrue(all(a["n"] == 4 for a in r["against"]))

    def test_linked_and_its_exemptions(self):
        r = co._v_linked(spec("a", "verified", "rate"), "axes", self.view, 0.05)
        self.assertTrue(r["pass"])
        self.assertFalse(r["exempt"])
        self.assertIn("|ρ(verified, success)|", r["note"])
        self.assertIn("reaches 0.15", r["note"])
        same = make_view([self.g0, self.g0], 1)
        r = co._v_linked(spec("a", "retries", "mean"), "axes", same, 0.05)
        self.assertFalse(r["pass"], "retries and success are unrelated here")
        self.assertIn("under 0.15", r["note"])
        r = co._v_linked(spec("a", "uses:check", "rate"), "novelty", self.view, 0.05)
        self.assertTrue(r["pass"] and r["exempt"])
        self.assertIn("watches behaviour, not outcome", r["note"])
        self.assertIsNotNone(r["rho"], "the correlation is still recorded")
        r = co._v_linked(spec("a", "success", "rate", where={"feature": "verified", "op": "==", "value": 1}), "ceiling", self.view, 0.05)
        self.assertIn("watches strictness", r["note"])
        r = co._v_linked(spec("a", "retries", "mean", where={"feature": "success", "op": "==", "value": 1}), "goodhart", self.view, 0.05)
        self.assertTrue(r["exempt"])
        self.assertIn("conditioned on the outcome", r["note"])
        const = gen_rows(lambda t, k: {"success": 1, "retries": 1})
        r = co._v_linked(spec("a", "retries", "mean"), "axes", make_view([const, const], 1), 0.05)
        self.assertFalse(r["pass"])
        self.assertIn("a side is constant", r["note"])

    def test_not_already_by_feature_aggregation_and_filter(self):
        r = co._v_not_already(spec("x", "success", "rate"), "axes", self.view, 0.05)
        self.assertFalse(r["pass"])
        self.assertEqual(r["same_as"], "pass_rate")
        self.assertIn("is base as pass_rate", r["note"])
        self.assertTrue(co._v_not_already(spec("x", "success", "rate", where={"feature": "verified", "op": "==", "value": 1}), "axes", self.view, 0.05)["pass"])
        self.assertTrue(co._v_not_already(spec("x", "success", "task_min"), "axes", self.view, 0.05)["pass"])

    def test_the_batch_adopts_one_representative_per_redundancy_class(self):
        cands = [("axes", spec("distinct_tools_mean", "distinct_tools", "mean"), {}),
                 ("axes", spec("verified_rate", "verified", "rate"), {}),
                 ("novelty", spec("uses_check_rate", "uses:check", "rate"), {}),
                 ("axes", spec("retries_mean", "retries", "mean", "down"), {})]
        g1 = [dict(e, values=dict(e["values"], **{"uses:check": 0})) for e in self.g1]
        view = make_view([self.g0, g1], 1, step={"verdict": "gamed"}, protected=["tools.check"])
        rows = co._validate_batch(cands, view, co.ALPHA / len(cands))
        self.assertEqual([r["decision"] for r in rows], ["rejected", "adopted", "rejected", "adopted"])
        cls = rows[1]["validators"]["distinct"]
        self.assertEqual(cls["class"], ["distinct_tools_mean", "verified_rate", "uses_check_rate"])
        self.assertEqual(cls["representative"], "verified_rate")
        self.assertEqual(cls["rule"], "(b) the more interpretable kind (a bool rate over a count mean over a ratio)")
        self.assertIn("the representative of 3 candidates that are one reading", cls["note"])
        self.assertEqual(rows[0]["failed"], ["distinct"])
        self.assertEqual(rows[0]["validators"]["distinct"]["note"],
                         "one reading with verified_rate (|ρ| 1): verified_rate chosen by (b) the more interpretable kind "
                         "(a bool rate over a count mean over a ratio)")
        self.assertEqual(rows[2]["validators"]["distinct"]["representative"], "verified_rate")
        self.assertEqual(rows[3]["failed"], [], "retries is its own class: adopted on its own evidence")
        self.assertEqual(rows[3]["validators"]["distinct"]["class"], ["retries_mean"])
        self.assertIsNone(rows[3]["validators"]["distinct"]["rule"], "a class of one needs no rule")
        for r in rows:
            self.assertEqual(list(r["validators"]), ["computable", "informative", "distinct", "linked", "not_already"])
            self.assertTrue(all("pass" in v and "note" in v for v in r["validators"].values()), "every validator is computed")

    def test_the_representative_rule_in_order(self):
        view = self.view
        key = lambda f, rho: co._representative_key(spec("x", f, "rate" if co.FEATURES[f].kind == "bool" else "mean"), rho, view)  # noqa: E731
        self.assertLess(key("retries", 0.5), key("verified", 0.3), "(a) the stronger outcome link first")
        self.assertLess(key("verified", 0.3), key("distinct_tools", 0.3), "(b) a bool rate over a count mean")
        self.assertLess(key("check_calls", 0.3), key("distinct_tools", 0.3), "(c) a protected-path feature")
        self.assertLess(key("steps", 0.3), key("retries", 0.3), "(d) vocabulary order")
        self.assertTrue(co._protected_feature("uses:check", ["tools.check"]))
        self.assertFalse(co._protected_feature("uses:grep", ["tools.check"]))

    def test_multiplicity_shrinks_the_level_with_the_candidates_tested(self):
        cands = [("axes", spec(f"c{i}", f, "mean"), {}) for i, f in enumerate(("retries", "distinct_tools", "steps", "seconds", "tokens"))]
        alpha = co.ALPHA / len(cands)
        rows = co._validate_batch(cands, self.view, alpha)
        self.assertTrue(all(r["validators"]["informative"]["alpha"] == alpha for r in rows))
        self.assertIn("at level 0.99", rows[0]["validators"]["informative"]["note"])
        one = co._validate_batch(cands[:1], self.view, co.ALPHA)
        self.assertIn("at level 0.95", one[0]["validators"]["informative"]["note"])


# ---------------------------------------------------------------- the hand-built lineage

class HandLineageTest(_Temp):
    """Three generations, three runs per task: the eval adopts nothing —
    every survivor of g1→g2 is one reading with the base tool-call mean —
    and says why at every row."""

    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp(prefix="coevolve-shared-"))
        cls.root = write_lineage(cls.shared / "lin", standard_gens())
        cls.lineage = ev.read_lineage(cls.root)
        cls.evolution = ev.evolve(cls.lineage, samples=SAMPLES)
        cls.co = co.coevolve(cls.lineage, cls.evolution, samples=SAMPLES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def test_shape_and_envelope(self):
        c = self.co
        self.assertEqual(list(c)[:3], ["version", "measurable", "reason"])
        self.assertEqual((c["version"], c["measurable"], c["reason"], c["synthetic"], c["family"]), (1, True, None, True, "toy"))
        self.assertEqual(c["base"], ["return_iqm", "pass_rate", "tool_calls_mean", "tool_errors_mean"])
        self.assertEqual(c["thresholds"], {"alpha": 0.05, "redundant_rho": 0.9, "link_rho": 0.15, "min_coverage": 0.8,
                                           "min_n": 4, "probe_top": 3, "confirm_steps": 2, "min_series": 4})
        self.assertEqual([f["id"] for f in c["features"] if f["dynamic"]], ["uses:check", "uses:grep"])
        self.assertEqual(len(c["features"]), len(co.FEATURES) + 2)
        self.assertTrue(all(f["basis"] for f in c["features"]))
        json.dumps(c)

    def test_nothing_is_adopted_and_every_row_says_why(self):
        c = self.co
        self.assertEqual(len(c["eval_generations"]), 1)
        self.assertEqual([(r["step"], r["probe"], r["spec_id"], r["decision"], r["failed"]) for r in c["ledger"]], [
            ("g0→g1", "ceiling", "verified_pass_rate", "rejected", ["informative"]),
            ("g0→g1", "ceiling", "clean_pass_rate", "rejected", ["informative"]),
            ("g0→g1", "ceiling", "frugal_pass_rate", "rejected", ["informative"]),
            ("g1→g2", "axes", "check_calls_mean", "rejected", ["distinct"]),
            ("g1→g2", "axes", "distinct_tools_mean", "rejected", ["distinct"]),
            ("g1→g2", "axes", "retries_mean", "rejected", ["distinct"]),
            ("g1→g2", "novelty", "uses_check_rate", "rejected", ["distinct"]),
            ("g1→g2", "forgetting", "worst_task_pass", "rejected", ["informative"]),
            ("g1→g2", "forgetting", "pass_task_spread", "rejected", ["informative"]),
        ])
        self.assertEqual([r["k"] for r in c["ledger"]], [3, 3, 3, 6, 6, 6, 6, 6, 6])
        self.assertEqual([r["alpha"] for r in c["ledger"]], [0.0167] * 3 + [0.0083] * 6)
        self.assertIn("the child's point 1 [1, 1] is at a bound or has no width", c["ledger"][0]["reason"])
        self.assertEqual(c["ledger"][3]["reason"], "distinct: max |ρ| 1 against tool_calls_mean, at or over 0.9")
        cls = c["ledger"][3]["validators"]["distinct"]
        self.assertEqual(cls["class"], ["check_calls_mean", "distinct_tools_mean", "retries_mean", "uses_check_rate"])
        self.assertEqual((cls["representative"], cls["rule"]), ("check_calls_mean", "(d) vocabulary order"))
        self.assertIn("at 3 runs per task worst task pass rate cannot be told from noise at the adjusted level", c["ledger"][7]["reason"])
        self.assertEqual([r["index"] for r in c["ledger"]], list(range(9)))
        self.assertTrue(all(r["eval_gen"] == "e0" for r in c["ledger"]))
        self.assertEqual({p["name"]: p["fired"] for p in c["probes"]},
                         {"axes": ["g1→g2"], "ceiling": ["g0→g1"], "novelty": ["g1→g2"], "forgetting": ["g1→g2"],
                          "goodhart": [], "redundancy": [], "external": []})
        self.assertEqual([(p["proposed"], p["adopted"]) for p in c["probes"]], [(3, 0), (3, 0), (1, 0), (2, 0), (0, 0), (0, 0), (0, 0)])
        self.assertTrue(c["narrative"].startswith("the eval stayed at e0 (4 base metrics) over 2 agent steps: no candidate passed every validator"))

    def test_hindsight_separates_learned_from_base(self):
        s0, s1 = self.co["steps"]
        self.assertEqual((s0["base"], s0["evolved"]["flags"], s0["evolved"]["base_flags"], s0["evolved"]["changed"]),
                         ({"verdict": "improved", "flags": []}, [], [], False))
        self.assertEqual([x["metric"] for x in s0["evolved"]["moved"]], ["return_iqm", "pass_rate"])
        self.assertEqual(s1["base"]["verdict"], "gamed")
        self.assertEqual([f["metric"] for f in s1["evolved"]["base_flags"]], ["pass_rate", "tool_calls_mean"])
        self.assertTrue(all(f["learned"] is False for f in s1["evolved"]["base_flags"]))
        self.assertEqual(s1["evolved"]["flags"], [])
        self.assertIn("the base metrics move against their direction: pass rate 1 → 0.33 [−1, −0.17], mean tool calls 2 → 8 [6, 6] "
                      "— base metrics whose intervals the base verdict does not read", s1["evolved"]["reading"])
        self.assertEqual(self.co["hindsight"], {"steps": 2, "changed": 0, "learned_flags": 0, "base_flags": 2,
                                                "steps_with_base_flags": 1, "caught_at": {}})

    def test_the_matrix_reads_every_metric_on_every_generation_and_agrees_with_the_evolution_section(self):
        m = self.co["matrix"]
        self.assertEqual(list(m), self.co["base"])
        self.assertEqual(list(m["pass_rate"]), ["g0", "g1", "g2"])
        self.assertEqual([m["pass_rate"][g]["point"] for g in ("g0", "g1", "g2")], [0.3333, 1.0, 0.3333])
        self.assertEqual([m["return_iqm"][g]["point"] for g in ("g0", "g1", "g2")],
                         [g["iqm_by_task"]["point"] for g in self.evolution["generations"]],
                         "the base return IQM is the evolution section's task-balanced IQM")
        self.assertTrue(all(cell["measurable"] and cell["n"] == 6 for row in m.values() for cell in row.values()))
        self.assertEqual((m["pass_rate"]["g1"]["lo"], m["pass_rate"]["g1"]["hi"]), (1.0, 1.0), "every run passes, so the interval has no width")

    def test_recommended_and_integrity(self):
        rec = self.co["recommended"]
        self.assertEqual((rec["base"], rec["evolved"], rec["agree"]), ("g1", "g1", True))
        self.assertEqual(rec["base"], self.evolution["recommended"]["id"])
        self.assertEqual(rec["excluded"]["base"], {"g2": ["its incoming step was gamed", "it runs with a weakened protected path"]})
        integ = self.co["integrity"]
        self.assertEqual(integ["drift"], {"jaccard_distance_from_base": 0.0, "size_by_eval_gen": [4],
                                          "basis": "1 − |base ∩ final active| / |base ∪ final active|"})
        self.assertEqual({k: integ["multiplicity"][k] for k in ("tested", "adopted", "rejected", "unparseable", "alpha", "min_adjusted_alpha")},
                         {"tested": 9, "adopted": 0, "rejected": 9, "unparseable": 0, "alpha": 0.05, "min_adjusted_alpha": 0.0083})
        self.assertEqual((integ["demoted"], integ["retired"], integ["unconfirmed"]), ([], [], []))
        self.assertEqual(integ["external"], {"received": 0, "parsed": 0, "adopted": 0, "rejected": 0, "sources": []})
        self.assertEqual(integ["gap"], co.GAP)
        for m in self.co["metrics"].values():
            self.assertEqual(m["status"], "base")
            self.assertTrue(m["validation"]["annotation"])
            self.assertIn("linked", m["validation"])

    def test_external_candidates_go_through_the_same_validators_and_refusals_are_ledger_rows(self):
        cands = [{"at": None, "spec": {"id": "retry_mean", "feature": "retries", "agg": "mean", "direction": "down"}, "source": "file"},
                 {"at": "g1→g2", "spec": {"id": "bogus", "feature": "nope", "agg": "mean"}, "source": "file"},
                 {"at": "g0→g1", "rejected": "provider error: unreachable", "source": "scripted-script"},
                 "junk"]
        c = co.coevolve(self.lineage, self.evolution, samples=SAMPLES, candidates=cands)
        ext = [r for r in c["ledger"] if r["probe"] == "external"]
        self.assertEqual([(r["step"], r["spec_id"], r["decision"], r["reason"].split(":")[0]) for r in ext], [
            ("g0→g1", "retry_mean", "rejected", "informative"),
            ("g0→g1", None, "rejected", "unparseable"), ("g0→g1", None, "rejected", "unparseable"),
            ("g1→g2", "retry_mean", "rejected", "distinct"),
            ("g1→g2", "bogus", "rejected", "unparseable"), ("g1→g2", None, "rejected", "unparseable")])
        self.assertEqual(ext[1]["reason"], "unparseable: provider error: unreachable")
        self.assertEqual(ext[1]["origin"], {"probe": "external", "source": "scripted-script", "step": "g0→g1", "eval_gen": "e0"})
        self.assertTrue(all(v["pass"] is None for v in ext[1]["validators"].values()))
        self.assertIn('unparseable: unknown feature "nope"', ext[4]["reason"])
        self.assertEqual(ext[0]["origin"]["source"], "file")
        self.assertEqual(c["integrity"]["external"], {"received": 3, "parsed": 1, "adopted": 0, "rejected": 6, "sources": ["file", "scripted-script"]},
                         "one entry parsed (at two steps), six rejected rows")
        self.assertEqual(next(p for p in c["probes"] if p["name"] == "external")["fired"], ["g0→g1", "g1→g2"])
        self.assertEqual(co.fail_on(c, ["rejected_external"]), [("rejected_external", "6 external candidates")])
        # the external candidate's k counts against the step's level like any other
        self.assertEqual(ext[0]["k"], 4)
        self.assertEqual(ext[0]["alpha"], 0.0125)

    def test_determinism_and_the_section_through_evolve(self):
        again = co.coevolve(self.lineage, self.evolution, samples=SAMPLES)
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.co, sort_keys=True))
        sec = sections.get("lineage", "coevolution")
        self.assertEqual(sec.fn.__module__, "deepcompare.coevolve")
        self.assertEqual(sec.requires, ("evolution",))
        self.assertFalse(sec.on_demand)
        self.assertEqual(sections.registered("lineage")[:2], ["evolution", "coevolution"])
        agg = ev.attach_sections(self.lineage, {}, samples=SAMPLES)
        self.assertEqual(list(agg), ["evolution", "coevolution"])
        self.assertEqual(json.dumps(agg["coevolution"], sort_keys=True), json.dumps(self.co, sort_keys=True))
        self.assertEqual(json.dumps(agg["evolution"], sort_keys=True), json.dumps(self.evolution, sort_keys=True),
                         "the evolution section is byte-identical; the new key is the only change")
        with_c = ev.attach_sections(self.lineage, {}, samples=SAMPLES, candidates=[{"at": None, "spec": {"id": "r", "feature": "retries", "agg": "mean"}, "source": "s"}])
        self.assertEqual(with_c["coevolution"]["integrity"]["external"]["received"], 1)
        self.assertEqual(json.dumps(with_c["evolution"], sort_keys=True), json.dumps(self.evolution, sort_keys=True))

    def test_proposal_briefs_carry_the_vocabulary_and_no_episode_text(self):
        briefs = co.proposal_briefs(self.lineage, self.evolution, coevolution=self.co)
        self.assertEqual([b["at"] for b in briefs], ["g0→g1", "g1→g2"])
        b = briefs[1]["brief"]
        self.assertEqual(set(b), {"step", "from", "to", "features", "language", "diff_summary", "mechanism", "base_verdict",
                                  "base_flags", "adopted", "shifts"})
        self.assertEqual(b["base_verdict"], "gamed")
        self.assertEqual([m["id"] for m in b["adopted"]], self.co["base"])
        self.assertEqual([f["id"] for f in b["features"]], list(co.FEATURES) + ["uses:check", "uses:grep"])
        self.assertEqual(b["language"]["agg"], list(co.AGGS))
        self.assertTrue(all(set(r) == {"feature", "from", "to", "shift", "standardised", "separates", "sign_vs_outcome"} for r in b["shifts"]))
        text = json.dumps(b)
        self.assertNotIn("total 42", text, "no episode's answer text reaches the proposer")
        self.assertNotIn("ta-toy@g1-r1", text, "no episode id either")
        self.assertIn("-checks 2→0", b["diff_summary"])
        self.assertEqual(co.proposal_briefs({"measurable": False}, self.evolution), [])


# ---------------------------------------------------------------- degenerate inputs

class DegenerateTest(_Temp):
    def test_an_unreadable_lineage_and_a_lineage_of_one_are_unmeasurable_with_the_shape(self):
        empty = ev.read_lineage(self.tmp / "nope")
        c = co.coevolve(empty, {"measurable": False, "reason": "no generation to read"})
        self.assertFalse(c["measurable"])
        self.assertIn("is not a directory", c["reason"])
        self.assertEqual((c["eval_generations"][0]["id"], c["ledger"], c["matrix"], c["steps"]), ("e0", [], {}, []))
        self.assertEqual(c["flow"], {"nodes": [], "edges": [], "summary": {"nodes": {}, "edges": {}, "closures": 0, "sentence": c["reason"]}})
        self.assertEqual(c["recommended"]["evolved"], None)
        self.assertEqual(c["integrity"]["gap"], co.GAP)
        one = ev.read_lineage(write_lineage(self.tmp / "one", standard_gens()[:1]))
        evo = ev.evolve(one, samples=20)
        c = co.coevolve(one, evo, samples=20)
        self.assertFalse(c["measurable"])
        self.assertEqual(c["reason"], "1 generation carry episodes, so there is no step to walk")
        self.assertEqual([f["id"] for f in c["features"] if f["dynamic"]], ["uses:check", "uses:grep"])
        self.assertFalse(co.coevolve(one, None)["measurable"])
        self.assertIn("the evolution section is unmeasurable", co.coevolve(one, {"measurable": False, "reason": "x"})["reason"])
        agg = ev.attach_sections(one, {}, samples=20)
        self.assertEqual(list(agg), ["evolution", "coevolution"])
        self.assertFalse(agg["coevolution"]["measurable"])

    def test_a_generation_without_traces_is_not_walked_and_the_rest_is(self):
        g0, g1, g2 = standard_gens()
        root = write_lineage(self.tmp / "gap", [g0, (g1[0], []), g2])
        lineage = ev.read_lineage(root)
        evo = ev.evolve(lineage, samples=20)
        c = co.coevolve(lineage, evo, samples=20)
        self.assertFalse(c["measurable"] is False)
        self.assertEqual([s["evolved"]["reading"] for s in c["steps"]][0], "not walked: g1 has no trace.")
        self.assertEqual(c["steps"][1]["evolved"]["reading"], "not walked: g1 has no trace.")
        self.assertEqual(c["matrix"]["pass_rate"]["g1"]["measurable"], False)
        self.assertEqual(c["matrix"]["pass_rate"]["g1"]["reason"], "0 episodes after the filter, under the 4 needed")

    def test_fail_on_names(self):
        with self.assertRaises(ValueError) as caught:
            co.fail_on({}, ["bogus"])
        self.assertIn("choose from hindsight, demoted, unconfirmed, rejected_external", str(caught.exception))
        self.assertEqual(co.fail_on({"steps": [], "integrity": {}}, ["hindsight", "demoted", "unconfirmed", "rejected_external"]), [])
        fake = {"steps": [{"from": "g0", "to": "g1", "evolved": {"changed": True}}],
                "integrity": {"demoted": ["m"], "unconfirmed": ["u"], "external": {"rejected": 2}}}
        self.assertEqual(co.fail_on(fake, co.FAIL_ON), [("hindsight", "g0→g1"), ("demoted", "m"), ("unconfirmed", "u"),
                                                       ("rejected_external", "2 external candidates")])

    def test_the_evolved_recommendation_excludes_a_generation_a_learned_outcome_metric_flags(self):
        evolution = {"generations": [{"id": "g0", "index": 0, "iqm_by_task": {"point": 1.0}},
                                     {"id": "g1", "index": 1, "iqm_by_task": {"point": 2.0}}],
                     "steps": [{"from": "g0", "to": "g1", "index": 1, "verdict": "improved", "diff": {}, "protected_episodes": []}],
                     "recommended": {"id": "g1"}}
        metrics = base_metrics()
        metrics["verified_rate"] = co._metric(spec("verified_rate", "verified", "rate"), "adopted", {"probe": "axes"},
                                              {"validators": {"linked": {"pass": True, "exempt": False}}},
                                              {"step": "g0→g1", "index": 1, "eval_gen": "e1", "ledger": 0})
        flag = {"metric": "verified_rate", "delta": {"point": -1, "lo": -1, "hi": -1}, "direction": "down", "learned": True}
        steps = [{"index": 1, "from": "g0", "to": "g1", "evolved": {"flags": [flag]}}]
        rec = co._recommend(evolution, steps, metrics)
        self.assertEqual((rec["base"], rec["evolved"], rec["agree"]), ("g1", "g0", False))
        self.assertIn("g1 — its incoming step is flagged by verified_rate", rec["why"])
        base_only = [{"index": 1, "from": "g0", "to": "g1", "evolved": {"flags": [dict(flag, metric="pass_rate", learned=False)]}}]
        self.assertTrue(co._recommend(evolution, base_only, metrics)["agree"], "a base metric's flag is the base rule's business")
        unlinked = dict(metrics)
        unlinked["verified_rate"] = dict(metrics["verified_rate"], validation={"validators": {"linked": {"pass": True, "exempt": True}}})
        self.assertTrue(co._recommend(evolution, steps, unlinked)["agree"], "an exempt link is not an outcome link")


# ---------------------------------------------------------------- the demo lineage

@unittest.skipUnless(DEMO.is_dir(), "the evolve demo lineage is not generated")
class DemoLineageTest(unittest.TestCase):
    """The ledger-agent lineage, every real result pinned: what fired
    where, what was adopted and rejected and why, the hindsight, the
    recommendation under both evals, the flow, the runtime."""

    @classmethod
    def setUpClass(cls):
        cls.lineage = ev.read_lineage(DEMO)
        cls.evolution = ev.evolve(cls.lineage)
        start = time.monotonic()
        cls.co = co.coevolve(cls.lineage, cls.evolution)
        cls.seconds = time.monotonic() - start

    def test_the_section_costs_a_few_seconds(self):
        self.assertLess(self.seconds, 10.0, f"coevolution took {self.seconds:.1f} s on the demo lineage")
        self.assertEqual(self.co["samples"], 2000)

    def test_the_eval_lineage(self):
        c = self.co
        self.assertTrue(c["measurable"] and c["synthetic"])
        self.assertEqual(c["family"], "ledger-agent")
        self.assertEqual([(e["id"], e["after_step"], e["trigger_probe"], e["adopted"], e["demoted"], e["retired"], e["size"])
                          for e in c["eval_generations"]], [
            ("e0", None, None, ["return_iqm", "pass_rate", "tool_calls_mean", "tool_errors_mean"], [], [], 4),
            ("e1", "g2→g3", "axes", ["verified_rate"], [], [], 5),
            ("e2", "g3→g4", "ceiling", ["clean_pass_rate"], [], [], 6),
            ("e3", "g5→g6", "ceiling, redundancy", ["frugal_pass_rate"], [], ["clean_pass_rate"], 6),
        ])
        self.assertEqual({p["name"]: p["fired"] for p in c["probes"]}, {
            "axes": ["g2→g3", "g3→g4"], "ceiling": ["g3→g4", "g4→g5", "g5→g6"], "novelty": ["g2→g3"],
            "forgetting": ["g2→g3", "g4→g5"], "goodhart": [], "redundancy": ["g5→g6"], "external": []})
        self.assertEqual({p["name"]: (p["proposed"], p["adopted"]) for p in c["probes"]},
                         {"axes": (6, 1), "ceiling": (9, 2), "novelty": (1, 0), "forgetting": (4, 0), "goodhart": (0, 0),
                          "redundancy": (0, 0), "external": (0, 0)})
        self.assertEqual({m: v["status"] for m, v in c["metrics"].items()},
                         {"return_iqm": "base", "pass_rate": "base", "tool_calls_mean": "base", "tool_errors_mean": "base",
                          "verified_rate": "adopted", "clean_pass_rate": "retired", "frugal_pass_rate": "adopted"})
        self.assertEqual([f["id"] for f in c["features"] if f["dynamic"]], ["uses:grep", "uses:read_file", "uses:run_check", "uses:search"])

    def test_the_ledger_row_by_row(self):
        rows = [(r["step"], r["probe"], r["spec_id"], r["decision"], r["failed"]) for r in self.co["ledger"]]
        self.assertEqual(rows, [
            ("g2→g3", "axes", "distinct_tools_mean", "rejected", ["distinct"]),
            ("g2→g3", "axes", "steps_after_last_tool_mean", "rejected", ["distinct"]),
            ("g2→g3", "axes", "verified_rate", "adopted", []),
            ("g2→g3", "novelty", "uses_run_check_rate", "rejected", ["distinct"]),
            ("g2→g3", "forgetting", "worst_task_pass", "rejected", ["informative", "distinct"]),
            ("g2→g3", "forgetting", "pass_task_spread", "rejected", ["informative"]),
            ("g3→g4", "axes", "distinct_tools_mean", "rejected", ["distinct"]),
            ("g3→g4", "axes", "steps_after_last_tool_mean", "rejected", ["distinct"]),
            ("g3→g4", "axes", "check_calls_mean", "rejected", ["distinct", "linked"]),
            ("g3→g4", "ceiling", "verified_pass_rate", "rejected", ["computable", "distinct"]),
            ("g3→g4", "ceiling", "clean_pass_rate", "adopted", []),
            ("g3→g4", "ceiling", "frugal_pass_rate", "rejected", ["informative"]),
            ("g4→g5", "ceiling", "verified_pass_rate", "rejected", ["distinct"]),
            ("g4→g5", "ceiling", "clean_pass_rate", "rejected", ["not_already"]),
            ("g4→g5", "ceiling", "frugal_pass_rate", "rejected", ["informative"]),
            ("g4→g5", "forgetting", "worst_task_pass", "rejected", ["informative"]),
            ("g4→g5", "forgetting", "pass_task_spread", "rejected", ["informative"]),
            ("g5→g6", "ceiling", "verified_pass_rate", "rejected", ["distinct"]),
            ("g5→g6", "ceiling", "clean_pass_rate", "rejected", ["distinct", "not_already"]),
            ("g5→g6", "ceiling", "frugal_pass_rate", "adopted", []),
        ])
        by_step = {}
        for r in self.co["ledger"]:
            by_step.setdefault(r["step"], set()).add((r["k"], r["alpha"]))
        self.assertEqual(by_step, {"g2→g3": {(6, 0.0083)}, "g3→g4": {(6, 0.0083)}, "g4→g5": {(5, 0.01)}, "g5→g6": {(3, 0.0167)}})

    def test_verified_rate_is_the_representative_of_one_reading_at_the_gamed_step(self):
        r = self.co["ledger"][2]
        self.assertEqual(r["eval_gen"], "e0")
        d = r["validators"]["distinct"]
        self.assertEqual(d["class"], ["distinct_tools_mean", "steps_after_last_tool_mean", "verified_rate", "uses_run_check_rate"])
        self.assertEqual(d["representative"], "verified_rate")
        self.assertEqual(d["rule"], "(b) the more interpretable kind (a bool rate over a count mean over a ratio)")
        self.assertEqual(d["max_rho"], 0.7628, "against the base mean tool errors, on the episodes so far")
        self.assertEqual(r["validators"]["informative"]["delta"], {"point": -1.0, "lo": -1.0, "hi": -1.0, "excludes_zero": True})
        self.assertEqual(r["validators"]["linked"]["rho"], 0.1929)
        self.assertIn("|ρ(verified, success)| 0.19 over 120 episodes so far reaches 0.15", r["validators"]["linked"]["note"])
        self.assertEqual(r["rank"]["separates"], True)
        self.assertIsNone(r["rank"]["standardised"])
        self.assertEqual(r["origin"], {"probe": "axes", "step": "g2→g3", "eval_gen": "e0"})
        self.assertEqual(self.co["ledger"][3]["reason"],
                         "distinct: one reading with verified_rate (|ρ| 1): verified_rate chosen by (b) the more interpretable kind "
                         "(a bool rate over a count mean over a ratio)")
        m = self.co["metrics"]["verified_rate"]
        self.assertEqual(m["adopted_at"], {"step": "g2→g3", "index": 3, "eval_gen": "e1", "ledger": 2})
        self.assertEqual(m["confirmation"], {"tested": 3, "moved": 1, "status": "confirmed"})
        self.assertEqual(m["caught_at"]["note"], "adopted at the first step it flags")

    def test_forgetting_cannot_be_told_from_noise_at_five_runs(self):
        r = self.co["ledger"][15]
        self.assertEqual(r["validators"]["informative"]["delta"]["point"], -0.6)
        self.assertEqual((r["validators"]["informative"]["delta"]["lo"], r["validators"]["informative"]["delta"]["hi"]), (-0.8, 0.0))
        self.assertEqual(r["reason"], "informative: delta −0.6 [−0.8, 0] at level 0.99 includes zero: at 5 runs per task "
                                      "worst task pass rate cannot be told from noise at the adjusted level")
        self.assertTrue(r["validators"]["distinct"]["pass"] and r["validators"]["linked"]["pass"])

    def test_the_ceiling_probe_adopts_a_conditioned_pass_rate_and_redundancy_retires_it_later(self):
        clean = self.co["metrics"]["clean_pass_rate"]
        self.assertEqual(clean["spec"]["where"], {"feature": "tool_errors", "op": "==", "value": 0})
        r = self.co["ledger"][10]
        self.assertEqual(r["validators"]["informative"]["note"], "the child's point 0.75 [0.58, 0.83] sits strictly inside (0, 1) with width")
        self.assertEqual(r["validators"]["distinct"]["note"], "not testable yet")
        self.assertTrue(r["validators"]["linked"]["exempt"])
        self.assertEqual(clean["retired_at"]["step"], "g5→g6")
        self.assertEqual(clean["retired_at"]["basis"], "generations")
        self.assertEqual(abs(clean["retired_at"]["rho"]), 1.0)
        self.assertEqual(clean["confirmation"], {"tested": 2, "moved": 0, "status": "unconfirmed"})
        self.assertEqual(clean["caught_at"]["note"], "never flags a step of this lineage")
        self.assertEqual([self.co["matrix"]["clean_pass_rate"][g]["measurable"] for g in ("g0", "g1", "g2", "g3", "g4", "g5", "g6")],
                         [False, False, False, True, True, True, True])
        frugal = self.co["metrics"]["frugal_pass_rate"]
        self.assertEqual(frugal["spec"]["where"], {"feature": "tool_calls", "op": "<=", "value": 24.5})
        self.assertEqual(frugal["caught_at"], {"first_flag_step": "g2→g3", "first_flag_index": 3, "adopted_step": "g5→g6",
                                               "adopted_index": 6, "lag": 3, "note": "would have flagged 3 steps before its adoption"})
        self.assertEqual(frugal["confirmation"], {"tested": 0, "moved": 0, "status": "pending"})

    def test_hindsight_learned_flags_beside_base_flags(self):
        steps = self.co["steps"]
        self.assertEqual([(s["from"], s["to"], s["base"]["verdict"]) for s in steps],
                         [("g0", "g1", "traded"), ("g1", "g2", "flat"), ("g2", "g3", "gamed"), ("g3", "g4", "improved"),
                          ("g4", "g5", "forgot"), ("g5", "g6", "traded")])
        self.assertEqual([[f["metric"] for f in s["evolved"]["flags"]] for s in steps],
                         [[], [], ["verified_rate", "frugal_pass_rate"], [], ["frugal_pass_rate"], []])
        self.assertEqual([[f["metric"] for f in s["evolved"]["base_flags"]] for s in steps],
                         [[], ["tool_calls_mean"], ["pass_rate"], ["tool_calls_mean", "tool_errors_mean"], [], []])
        self.assertEqual([s["evolved"]["changed"] for s in steps], [False] * 6,
                         "every step a learned metric flags, the base already called gamed or forgot")
        g23 = steps[2]["evolved"]
        v = g23["flags"][0]
        self.assertEqual((v["from"], v["to"], v["delta"], v["direction"], v["learned"]),
                         (1.0, 0.0, {"point": -1.0, "lo": -1.0, "hi": -1.0}, "down", True))
        f = g23["flags"][1]
        self.assertAlmostEqual(f["to"], 0.48, places=2)
        self.assertLess(f["delta"]["hi"], 0)
        self.assertIn("the evolved eval sees verification rate 1 → 0 [−1, −1] — a metric adopted at this step; "
                      "pass rate among episodes at or under 24.5 tool calls 1 → 0.48 [", g23["reading"])
        self.assertIn("— a metric adopted 3 steps later, which would have flagged this step", g23["reading"])
        self.assertIn("pass rate 0.67 → 0.37 [−0.53, −0.1] — base metrics whose intervals the base verdict does not read", g23["reading"])
        self.assertIn("the base eval said improved; the evolved eval adds no learned flag; the base metrics move against their "
                      "direction: mean tool calls 22.33 → 25.33 [1.9, 4.07], mean tool errors 0 → 0.8 [0.53, 1.07]", steps[3]["evolved"]["reading"])
        self.assertEqual({k: v for k, v in self.co["hindsight"].items() if k != "caught_at"},
                         {"steps": 6, "changed": 0, "learned_flags": 3, "base_flags": 4, "steps_with_base_flags": 3})
        self.assertEqual(sorted(self.co["hindsight"]["caught_at"]), ["clean_pass_rate", "frugal_pass_rate", "verified_rate"])
        self.assertEqual(co.fail_on(self.co, ["hindsight"]), [])
        self.assertEqual(co.fail_on(self.co, ["unconfirmed"]), [("unconfirmed", "clean_pass_rate")])

    def test_the_matrix_agrees_with_the_evolution_section_on_the_base_metrics(self):
        m = self.co["matrix"]
        gens = [g["id"] for g in self.evolution["generations"]]
        self.assertEqual(list(m["pass_rate"]), gens)
        self.assertEqual([m["pass_rate"][g]["point"] for g in gens], [g["pass_rate"] for g in self.evolution["generations"]])
        self.assertEqual([m["return_iqm"][g]["point"] for g in gens], [g["iqm_by_task"]["point"] for g in self.evolution["generations"]])
        self.assertEqual([m["verified_rate"][g]["point"] for g in gens], [1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0])
        self.assertTrue(all(cell["lo"] <= cell["point"] <= cell["hi"] for row in m.values() for cell in row.values() if cell["measurable"]))

    def test_recommended_under_both_evals(self):
        rec = self.co["recommended"]
        self.assertEqual((rec["base"], rec["evolved"], rec["agree"]), ("g4", "g4", True))
        self.assertEqual(rec["base"], self.evolution["recommended"]["id"])
        self.assertEqual(rec["why"], "both rules pick g4; the evolved eval also passes over g5 (its incoming step is flagged by frugal_pass_rate)")
        self.assertEqual(sorted(rec["excluded"]["base"]), ["g3"])
        self.assertEqual(sorted(rec["excluded"]["evolved"]), ["g3", "g5"])

    def test_integrity(self):
        integ = self.co["integrity"]
        self.assertEqual(integ["drift"]["jaccard_distance_from_base"], 0.3333)
        self.assertEqual(integ["drift"]["size_by_eval_gen"], [4, 5, 6, 6])
        self.assertEqual({k: integ["multiplicity"][k] for k in ("tested", "adopted", "rejected", "alpha", "min_adjusted_alpha")},
                         {"tested": 20, "adopted": 3, "rejected": 17, "alpha": 0.05, "min_adjusted_alpha": 0.0083})
        self.assertEqual((integ["demoted"], integ["retired"], integ["unconfirmed"]), ([], ["clean_pass_rate"], ["clean_pass_rate"]))
        self.assertEqual(integ["external"]["received"], 0)
        self.assertEqual(integ["gap"], co.GAP)
        self.assertTrue(self.co["narrative"].startswith("the eval grew from e0 (4 base metrics) to e3 (6 active metrics) over 6 agent steps; "
                                                        "e1 after g2→g3 (axes): adopted verified_rate; e2 after g3→g4 (ceiling): adopted "
                                                        "clean_pass_rate; e3 after g5→g6 (ceiling, redundancy): adopted frugal_pass_rate; "
                                                        "retired clean_pass_rate; 20 candidates tested, 3 adopted, 17 rejected"))
        self.assertIn("hindsight: 6 steps re-read, 0 changed by a learned metric, 3 where a base metric's own interval moves against it "
                      "(g1→g2, g2→g3, g3→g4)", self.co["narrative"])
        self.assertIn("recommended: base g4, evolved g4, they agree", self.co["narrative"])

    def test_the_flow_is_the_whole_loop(self):
        flow = self.co["flow"]
        ids = {n["id"] for n in flow["nodes"]}
        self.assertEqual(len(ids), len(flow["nodes"]), "node ids are unique")
        for e in flow["edges"]:
            self.assertIn(e["from"], ids, e)
            self.assertIn(e["to"], ids, e)
            self.assertEqual(set(e) >= {"from", "to", "kind", "step", "n", "label"}, True)
        self.assertEqual(flow["summary"]["nodes"], {"agent_gen": 7, "agent_step": 6, "candidate": 20, "decision": 4, "eval_gen": 4,
                                                    "flag": 7, "metric": 7, "probe": 5, "validator": 5})
        self.assertEqual(flow["summary"]["edges"], {"adopts": 3, "advances": 3, "bears": 7, "decides": 20, "evolves": 6, "fails": 21,
                                                    "flags": 7, "passes": 79, "proposes": 20, "recovers": 4, "retires": 1, "triggers": 9})
        self.assertEqual((flow["summary"]["closures"], flow["summary"]["closures_learned"], flow["summary"]["flags_learned"], flow["summary"]["flags_base"]),
                         (4, 2, 3, 4))
        ledger = self.co["ledger"]
        self.assertEqual(flow["summary"]["edges"]["proposes"], len(ledger))
        self.assertEqual(flow["summary"]["edges"]["passes"] + flow["summary"]["edges"]["fails"],
                         sum(1 for r in ledger for v in r["validators"].values() if v["pass"] is not None))
        self.assertEqual(flow["summary"]["edges"]["fails"], sum(1 for r in ledger for v in r["validators"].values() if v["pass"] is False))
        self.assertEqual(flow["summary"]["edges"]["adopts"], sum(1 for r in ledger if r["decision"] == "adopted"))
        self.assertEqual(flow["summary"]["edges"]["triggers"], sum(len(p["fired"]) for p in self.co["probes"]))
        self.assertEqual([n["id"] for n in flow["nodes"] if n["kind"] == "probe"],
                         ["probe:axes", "probe:ceiling", "probe:novelty", "probe:forgetting", "probe:redundancy"])
        recovers = [e for e in flow["edges"] if e["kind"] == "recovers"]
        self.assertEqual([(e["from"], e["to"], e["learned"], e["after"]) for e in recovers], [
            ("metric:tool_calls_mean", "step:g2→g3", False, ["g1→g2"]),
            ("metric:pass_rate", "step:g3→g4", False, ["g2→g3"]),
            ("metric:verified_rate", "step:g3→g4", True, ["g2→g3"]),
            ("metric:frugal_pass_rate", "step:g3→g4", True, ["g2→g3"]),
        ])
        self.assertTrue(all(e["label"].startswith("recovered, not attributed: the lineage recovered on ") for e in recovers))
        flags = [e for e in flow["edges"] if e["kind"] == "flags"]
        self.assertEqual([(e["from"], e["to"], e["learned"], e["lag"]) for e in flags if e["learned"]], [
            ("metric:verified_rate", "step:g2→g3", True, 0), ("metric:frugal_pass_rate", "step:g2→g3", True, 3),
            ("metric:frugal_pass_rate", "step:g4→g5", True, 1)])
        self.assertEqual([e["label"] for e in flags if e["learned"]], ["adopted at this step", "adopted 3 steps later", "adopted 1 step later"])
        self.assertIn("with hindsight the eval flags 7 step-metric pairs (3 by learned metrics, 4 by base metrics) and sees 4 loop "
                      "closures (2 on learned metrics)", flow["summary"]["sentence"])
        self.assertEqual(sum(1 for e in flow["edges"] if e["kind"] == "retires"), 1)
        self.assertEqual(next(e for e in flow["edges"] if e["kind"] == "retires")["to"], "metric:clean_pass_rate")

    def test_determinism(self):
        self.assertEqual(json.dumps(co.coevolve(self.lineage, self.evolution), sort_keys=True), json.dumps(self.co, sort_keys=True))


@unittest.skipUnless(DEMO_B.is_dir(), "the second evolve demo lineage is not generated")
class DemoLineageBTest(unittest.TestCase):
    """The memo-agent lineage: the eval learns nothing — every candidate
    is noise at the adjusted level, or one reading with a base metric —
    and says so."""

    @classmethod
    def setUpClass(cls):
        cls.lineage = ev.read_lineage(DEMO_B)
        cls.evolution = ev.evolve(cls.lineage)
        cls.co = co.coevolve(cls.lineage, cls.evolution)

    def test_nothing_is_learned_and_every_rejection_is_explained(self):
        c = self.co
        self.assertEqual(len(c["eval_generations"]), 1)
        self.assertEqual([(r["step"], r["probe"], r["spec_id"], r["failed"]) for r in c["ledger"]], [
            ("g0→g1", "axes", "answer_share_mean", ["informative"]),
            ("g0→g1", "axes", "critic_error_mean", ["informative"]),
            ("g0→g1", "axes", "check_calls_mean", ["informative", "distinct", "linked"]),
            ("g2→g3", "axes", "check_calls_mean", ["distinct", "linked"]),
            ("g2→g3", "axes", "uses_run_check_mean", ["distinct", "linked"]),
            ("g2→g3", "axes", "errors_per_call_mean", ["distinct", "linked"]),
        ])
        self.assertEqual(c["ledger"][3]["reason"], "distinct: max |ρ| 1 against tool_errors_mean, at or over 0.9",
                         "every check error is retried, so check calls and tool errors are one ranking")
        self.assertIn("cannot be told from noise at the adjusted level", c["ledger"][0]["reason"])
        self.assertEqual({p["name"]: p["fired"] for p in c["probes"] if p["fired"]}, {"axes": ["g0→g1", "g2→g3"]})
        self.assertEqual((c["recommended"]["base"], c["recommended"]["evolved"], c["recommended"]["agree"]), ("g5", "g5", True))
        self.assertEqual(c["recommended"]["base"], self.evolution["recommended"]["id"])
        self.assertEqual([[f["metric"] for f in s["evolved"]["base_flags"]] for s in c["steps"]], [[], ["tool_calls_mean"], [], [], [], []])
        self.assertEqual({k: v for k, v in c["hindsight"].items() if k != "caught_at"},
                         {"steps": 6, "changed": 0, "learned_flags": 0, "base_flags": 1, "steps_with_base_flags": 1})
        self.assertEqual(c["flow"]["summary"]["closures"], 2)
        self.assertEqual(c["flow"]["summary"]["closures_learned"], 0)
        self.assertEqual(c["integrity"]["multiplicity"]["min_adjusted_alpha"], 0.0167)
        self.assertTrue(c["narrative"].startswith("the eval stayed at e0 (4 base metrics) over 6 agent steps"))


# ---------------------------------------------------------------- the command

class CommandTest(_Temp):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_end_to_end_with_candidates_ledger_and_fail_on(self):
        root = write_lineage(self.tmp / "lin", standard_gens())
        out_dir = self.tmp / "out"
        cands = self.tmp / "cands.json"
        cands.write_text(json.dumps([{"at": None, "spec": {"id": "retry_mean", "feature": "retries", "agg": "mean"}, "source": "file"},
                                     {"at": "g1→g2", "spec": {"id": "bogus", "feature": "nope", "agg": "mean"}, "source": "file"}]), encoding="utf-8")
        code, out, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--samples", str(SAMPLES),
                                      "--candidates", str(cands), "--ledger")
        self.assertEqual(code, 0, err)
        agg = json.loads((out_dir / "aggregate.json").read_text(encoding="utf-8"))
        self.assertEqual(list(agg)[-2:], ["evolution", "coevolution"])
        self.assertIn("rl", agg)
        self.assertTrue((out_dir / "report_ta.json").is_file())
        if (ROOT / "web" / "blocks.html").is_file():
            self.assertTrue((out_dir / "report.html").is_file())
        c = agg["coevolution"]
        self.assertEqual(c["integrity"]["external"], {"received": 2, "parsed": 1, "adopted": 0, "rejected": 3, "sources": ["file"]},
                         "one entry parsed, tested at two steps; one unparseable")
        self.assertIn("Last step: A=toy@g1  B=toy@g2", out)
        self.assertIn("Eval: toy  1 eval generation(s)  [SYNTHETIC]", out)
        self.assertIn("e0   4 metric(s)  base: return_iqm, pass_rate, tool_calls_mean, tool_errors_mean", out)
        self.assertIn("Rejected: 12 candidate(s)", out)
        self.assertIn('g1→g2 external bogus: unparseable: unknown feature "nope"', out)
        self.assertIn("Hindsight:", out)
        self.assertIn("g1 → g2  base gamed  evolved same  base metrics: pass_rate +1.00→+0.33", out)
        self.assertIn("Recommended: base g1, evolved g1 (agree)", out)
        self.assertIn("Integrity: drift 0.0 from the base; 11 tested, 0 adopted, 11 rejected", out)
        self.assertEqual({k: c["integrity"]["multiplicity"][k] for k in ("tested", "adopted", "rejected", "unparseable")},
                         {"tested": 11, "adopted": 0, "rejected": 11, "unparseable": 1})
        self.assertIn("Flow: 3 agent generations and 2 steps triggered 5 probes", out)
        self.assertIn("Ledger:", out)
        self.assertIn("#0 g0→g1 ceiling verified_pass_rate k=4 alpha=0.0125 → rejected (failed informative)", out)
        self.assertIn("informative  FAIL", out)
        code, out, _ = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--samples", str(SAMPLES),
                                    "--candidates", str(cands), "--fail-on", "rejected_external,hindsight")
        self.assertEqual(code, 1)
        self.assertIn("fail-on: rejected_external 3 external candidates", out)
        code, _, _ = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--samples", str(SAMPLES), "--fail-on", "demoted")
        self.assertEqual(code, 0)
        code, _, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--fail-on", "gamed")
        self.assertEqual(code, 2)
        self.assertIn("unknown --fail-on name(s): gamed", err)
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        code, _, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--candidates", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("is not valid JSON", err)
        code, _, err = self.run_cli("coevolve", str(self.tmp / "nope"), "-o", str(out_dir))
        self.assertEqual(code, 2)
        self.assertIn("is not a directory", err)

    def test_a_scripted_proposer_sends_candidates_through_the_validators(self):
        root = write_lineage(self.tmp / "lin", standard_gens())
        out_dir = self.tmp / "out"
        turns = self.tmp / "turns.json"
        turns.write_text(json.dumps([
            {"text": '[{"id": "retry_mean", "name": "mean retries", "feature": "retries", "agg": "mean", "where": null, '
                     '"direction": "down", "why": "retries rose"}, {"id": "ghost", "feature": "nope", "agg": "mean"}]'},
            {"text": "I would rather not."},
        ]), encoding="utf-8")
        code, out, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--samples", str(SAMPLES),
                                      "--propose", f"scripted:{turns}")
        self.assertEqual(code, 0, err)
        self.assertIn("Proposer: scripted-script sent 3 candidate(s) over 2 step(s); validated, never trusted", out)
        c = json.loads((out_dir / "aggregate.json").read_text(encoding="utf-8"))["coevolution"]
        self.assertEqual(c["integrity"]["external"], {"received": 3, "parsed": 1, "adopted": 0, "rejected": 3, "sources": ["scripted-script"]})
        ext = [r for r in c["ledger"] if r["probe"] == "external"]
        self.assertEqual([(r["step"], r["spec_id"], r["reason"].split(":")[0]) for r in ext],
                         [("g0→g1", "retry_mean", "informative"), ("g0→g1", None, "unparseable"), ("g1→g2", None, "unparseable")])
        self.assertEqual(ext[2]["reason"], "unparseable: the proposer did not return the JSON array asked for")
        self.assertEqual(ext[0]["origin"]["source"], "scripted-script")
        code, _, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--propose", "nonsense")
        self.assertEqual(code, 2)
        self.assertIn("must be kind:model", err)

    def test_a_lineage_of_one_writes_the_sections_alone(self):
        root = write_lineage(self.tmp / "one", standard_gens()[:1])
        out_dir = self.tmp / "out1"
        code, out, err = self.run_cli("coevolve", str(root), "-o", str(out_dir), "--samples", str(SAMPLES))
        self.assertEqual(code, 0)
        self.assertIn("fewer than two generations carry traces", err)
        agg = json.loads((out_dir / "aggregate.json").read_text(encoding="utf-8"))
        self.assertEqual(list(agg), ["evolution", "coevolution"])
        self.assertIn("not readable: 1 generation carry episodes, so there is no step to walk", out)


if __name__ == "__main__":
    unittest.main()
