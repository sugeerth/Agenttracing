"""Two self-evolving lineages compared as processes: four axes, four verdicts.

Two hand-built lineages in a temp dir, every return a sum a reader can
redo: ``alpha`` (four generations, three runs per task) peaks at g1,
games its verifier off at g2 and ends low; ``beta`` (three generations,
four runs per task) starts lower, climbs cleanly and ends higher. So the
axes disagree by construction — peak to alpha, final to beta, learning to
alpha (it reaches the threshold at the same *index* but after fewer
*episodes*, which is the whole point of the second alignment), process to
beta — and every number the section reports is pinned by hand. Then the
shapes the layer must refuse to guess at: a shorter lineage, no shared
task, one lineage, a lineage of one generation, two lineages of the same
family; byte-determinism; the CLI end to end with the alias; and the two
demo lineages' headlines, pinned to what the data says.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from deepcompare import evolvecompare as ec
from deepcompare.cli import main
from deepcompare.evolve import evolve, read_lineage

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "evolve" / "lineage"
DEMO_B = ROOT / "demo" / "evolve" / "lineage_b"
SAMPLES = 200
TASKS = ("ta", "tb", "tc")


# ---------------------------------------------------------------- fixture

def _step(i, typ, name, reward, *, error=None, span=None):
    return {"index": i, "type": typ, "name": name, "input": name, "output": "", "tokens": 10,
            "latency_s": 1.0, "reward": reward, "error": error, "span": span}


def _trace(family, gen, task, run, success, *, check=True, greps=1, grep_reward=-0.1, error_at_grep=False):
    """A recorded-reward episode: plan 0, ``greps`` greps at ``grep_reward``
    each, a check at −0.1 inside a verifier span when ``check``, the
    answer ±5 — so return = greps × grep_reward − 0.1 × check ± 5."""
    steps = [_step(0, "plan", "plan", 0.0)]
    for k in range(greps):
        steps.append(_step(len(steps), "tool_call", "grep", grep_reward,
                           error=True if error_at_grep and k == 0 else None))
    if check:
        steps.append(_step(len(steps), "tool_call", "check", -0.1, span={"id": "v", "agent": "verifier", "parent": None}))
    steps.append(_step(len(steps), "answer", "final", 5.0 if success else -5.0))
    return {"schema_version": 1, "trace_id": f"{task}-{family}@{gen}-{run}", "run_id": run,
            "agent": {"name": f"{family}@{gen}", "model": "sim", "version": gen},
            "task": {"id": task, "prompt": f"solve {task}", "expected": "42"},
            "outcome": {"success": success, "answer": "42" if success else "41", "termination": "agent_stop"},
            "totals": {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0, "latency_s": float(len(steps))},
            "steps": steps, "tools": [{"name": "grep", "effect": "read"}, {"name": "check", "effect": "read"}],
            "harness": {"adapter": "synthetic", "graded_by": "exact-match", "note": "SYNTHETIC test episode"}}


def _art(checks=2, tools=("grep", "check"), rules=("r1",), memory=(), skills=(("s1", "a"),), prompt="Be careful."):
    return {"system_prompt": prompt, "rules": list(rules), "skills": [{"name": n, "body": b} for n, b in skills],
            "tools": list(tools), "memory": list(memory), "config": {"checks": checks}}


def _agent(family, gid, parent, artifacts, mechanism=None, evidence=None):
    return {"id": gid, "parent": parent, "family": family, "mechanism": mechanism, "evidence": evidence,
            "artifacts": artifacts, "note": f"SYNTHETIC {family} {gid}"}


def _lineage_json(family):
    return {"family": family, "protected": ["config.checks", "tools.check"], "budget": {"memory": 10},
            "note": "SYNTHETIC hand-built lineage"}


def _ev(task, parent_gen, family):
    return {"episodes": [f"{task}__{family}@{parent_gen}__r1"], "summary": "a failure", "source": "self"}


def alpha_gens(tasks=TASKS, family="alpha"):
    """g0 −1.87 (one pass of three), g1 6.9 (all pass, +1 per grep), g2 8.33
    (checks off, ten greps paid +1, one pass of three: gamed), g3 0.46
    (checks still off; ta, tb two of three, tc one of three: tc lost)."""
    runs = ("r1", "r2", "r3")
    g0 = [_trace(family, "g0", t, r, k == 0, error_at_grep=(k == 1)) for t in tasks for k, r in enumerate(runs)]
    g1 = [_trace(family, "g1", t, r, True, greps=2, grep_reward=1.0) for t in tasks for r in runs]
    g2 = [_trace(family, "g2", t, r, k == 0, check=False, greps=10, grep_reward=1.0)
          for t in tasks for k, r in enumerate(runs)]
    g3 = [_trace(family, "g3", t, r, (k < 2) if t != "tc" else (k == 0), check=False)
          for t in tasks for k, r in enumerate(runs)]
    return [(_agent(family, "g0", None, _art()), g0),
            (_agent(family, "g1", "g0", _art(rules=("r1", "r2")), "rule_add", _ev("ta", "g0", family)), g1),
            (_agent(family, "g2", "g1", _art(checks=0, tools=("grep",), rules=("r1", "r2")), "config",
                    _ev("tb", "g1", family)), g2),
            (_agent(family, "g3", "g2", _art(checks=0, tools=("grep",), rules=("r1", "r2", "r3")), "rule_add",
                    _ev("tc", "g2", family)), g3)]


def beta_gens(tasks=TASKS, family="beta"):
    """g0 −5.2 (one pass of four; the IQM of four cuts the pass), g1 5.4 (all
    pass, +0.5 per grep), g2 5.9 (two greps): clean, shorter, ends higher."""
    runs = ("r1", "r2", "r3", "r4")
    g0 = [_trace(family, "g0", t, r, k == 0) for t in tasks for k, r in enumerate(runs)]
    g1 = [_trace(family, "g1", t, r, True, grep_reward=0.5) for t in tasks for r in runs]
    g2 = [_trace(family, "g2", t, r, True, greps=2, grep_reward=0.5) for t in tasks for r in runs]
    return [(_agent(family, "g0", None, _art()), g0),
            (_agent(family, "g1", "g0", _art(skills=(("s1", "a"), ("s2", "b"))), "skill_add", _ev("ta", "g0", family)), g1),
            (_agent(family, "g2", "g1", _art(skills=(("s1", "a"), ("s2", "b")), memory=("m1",)), "memory",
                    _ev("tb", "g1", family)), g2)]


def write_lineage(root: Path, gens: list, lineage_json) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if lineage_json is not None:
        (root / "lineage.json").write_text(json.dumps(lineage_json), encoding="utf-8")
    for agent, traces in gens:
        d = root / agent["id"]
        (d / "traces").mkdir(parents=True, exist_ok=True)
        (d / "agent.json").write_text(json.dumps(agent), encoding="utf-8")
        for t in traces:
            (d / "traces" / f"{t['task']['id']}__{t['agent']['name']}__{t['run_id']}.json").write_text(
                json.dumps(t), encoding="utf-8")
    return root


class _Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="evolvecompare-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pair(self, a=None, b=None, a_json=None, b_json=None, a_name="alpha", b_name="beta", **kw):
        ra = write_lineage(self.tmp / a_name, a or alpha_gens(), _lineage_json("alpha") if a_json is None else a_json)
        rb = write_lineage(self.tmp / b_name, b or beta_gens(), _lineage_json("beta") if b_json is None else b_json)
        return ec.compare_lineages([ra, rb], samples=SAMPLES, **kw)


# ---------------------------------------------------------------- the metric by hand

class MetricTest(unittest.TestCase):
    def test_iqm_within_each_task_then_the_mean_over_tasks(self):
        eps = ([{"task_id": "ta", "return": v} for v in (1, 2, 3, 4, 100)]
               + [{"task_id": "tb", "return": v} for v in (10, 10, 10)])
        band = ec.iqm_by_task(eps, ["ta", "tb"], samples=50, label="t")
        # ta: five runs cut one from each end -> mean(2, 3, 4) = 3; tb: three runs cut nothing -> 10
        self.assertEqual(band["per_task"], {"ta": 3.0, "tb": 10.0})
        self.assertEqual(band["point"], 6.5)
        self.assertEqual((band["tasks_n"], band["n"]), (2, 8))
        self.assertTrue(band["lo"] <= band["point"] <= band["hi"])

    def test_tasks_outside_the_list_and_rows_without_a_return_do_not_count(self):
        eps = [{"task_id": "ta", "return": 1.0}, {"task_id": "zz", "return": 99.0}, {"task_id": "ta", "return": None}]
        band = ec.iqm_by_task(eps, ["ta"], samples=10)
        self.assertEqual((band["point"], band["n"]), (1.0, 1))
        empty = ec.iqm_by_task([{"task_id": "zz", "return": 1.0}], ["ta"], samples=10)
        self.assertFalse(empty["measurable"])
        self.assertIsNone(empty["point"])

    def test_the_bootstrap_is_seeded_by_its_label(self):
        eps = [{"task_id": "ta", "return": v} for v in (1, 5, 9)]
        a = ec.iqm_by_task(eps, ["ta"], samples=100, label="x")
        b = ec.iqm_by_task(eps, ["ta"], samples=100, label="x")
        self.assertEqual(a, b)


class RuleTest(unittest.TestCase):
    """The two verdict rules that are pure arithmetic, at every branch."""

    def test_learning_by_fewest_episodes_then_the_only_finisher_then_area(self):
        r = {"index": 1, "id": "g1", "episodes_cum": 18}
        s = {"index": 1, "id": "g1", "episodes_cum": 24}
        self.assertEqual(ec.learning_verdict({"a": r, "b": s}, {})[0], "a")
        self.assertEqual(ec.learning_verdict({"a": r, "b": None}, {})[0], "a")
        self.assertIsNone(ec.learning_verdict({"a": r, "b": dict(s, episodes_cum=18)}, {})[0])
        auc = {"a": {"by_episodes": {"value": 3.0}}, "b": {"by_episodes": {"value": 7.0}}}
        w, basis = ec.learning_verdict({"a": None, "b": None}, auc)
        self.assertEqual(w, "b")
        self.assertIn("area under the by-episodes curve", basis)
        self.assertIsNone(ec.learning_verdict({"a": None, "b": None}, {"a": {"by_episodes": {"value": 2.0}},
                                                                        "b": {"by_episodes": {"value": 2.0}}})[0])
        self.assertIsNone(ec.learning_verdict({"a": None, "b": None}, {"a": {"by_episodes": {"value": None}},
                                                                        "b": {"by_episodes": {"value": None}}})[0])

    def test_process_is_lexicographic_and_a_full_tie_is_null(self):
        def p(score, measurable=True):
            return {"measurable": measurable, "score": score, "reason": "x has no step to judge"}
        self.assertEqual(ec.process_verdict({"a": p([1, 0, -1.0, 0]), "b": p([0, 3, -0.2, 9])})[0], "b")
        self.assertEqual(ec.process_verdict({"a": p([1, 1, -1.0, 0]), "b": p([1, 0, -0.2, 9])})[0], "b")
        self.assertEqual(ec.process_verdict({"a": p([1, 1, -1.0, 9]), "b": p([1, 1, -0.5, 0])})[0], "a")
        w, basis = ec.process_verdict({"a": p([1, 1, -1.0, 2]), "b": p([1, 1, -1.0, 0])})
        self.assertEqual(w, "b")
        self.assertIn("accepted on noise", basis)
        w, basis = ec.process_verdict({"a": p([1, 1, -1.0, 2]), "b": p([1, 1, -1.0, 2])})
        self.assertIsNone(w)
        self.assertIn("tied on every rung", basis)
        w, basis = ec.process_verdict({"a": p([0, 0, 0, 0]), "b": p([9, 9, 0, 9], measurable=False)})
        self.assertIsNone(w)
        self.assertIn("no step to judge", basis)


# ---------------------------------------------------------------- the hand-built pair

class HandPairTest(_Temp):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="evolvecompare-hand-"))
        ra = write_lineage(cls.tmpdir / "alpha", alpha_gens(), _lineage_json("alpha"))
        rb = write_lineage(cls.tmpdir / "beta", beta_gens(), _lineage_json("beta"))
        cls.cmp = ec.compare_lineages([ra, rb], samples=SAMPLES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_the_section_is_measurable_and_names_both_lineages_in_order(self):
        c = self.cmp
        self.assertTrue(c["measurable"], c["reason"])
        self.assertEqual(c["version"], ec.VERSION)
        self.assertEqual(c["metric"], "iqm_by_task")
        self.assertEqual([ln["label"] for ln in c["lineages"]], ["alpha", "beta"])
        self.assertEqual([ln["generations_n"] for ln in c["lineages"]], [4, 3])
        self.assertEqual([ln["episodes_n"] for ln in c["lineages"]], [36, 36])
        self.assertEqual(c["lineages"][0]["recommended"], {"id": "g1", "index": 1})
        self.assertEqual(c["lineages"][0]["best"], {"id": "g2", "index": 2})
        self.assertEqual(c["lineages"][0]["last"], {"id": "g3", "index": 3})
        self.assertEqual(c["lineages"][1]["recommended"], {"id": "g2", "index": 2})
        self.assertEqual(c["lineages"][1]["last"], {"id": "g2", "index": 2})
        self.assertEqual(c["lineages"][0]["evolution"]["family"], "alpha")
        self.assertEqual(c["lineages"][0]["evolution"]["steps"][1]["verdict"], "gamed")
        self.assertEqual(c["tasks"], {"shared": ["ta", "tb", "tc"], "only": {"alpha": [], "beta": []},
                                      "rule": c["tasks"]["rule"]})

    def test_the_curves_by_hand_and_the_two_alignments(self):
        a = self.cmp["curves"]["by_index"]["alpha"]
        b = self.cmp["curves"]["by_index"]["beta"]
        self.assertEqual([r["id"] for r in a], ["g0", "g1", "g2", "g3"])
        self.assertEqual([r["id"] for r in b], ["g0", "g1", "g2"])
        # alpha: (4.8 − 5.2 − 5.2) / 3; 2 − 0.1 + 5; (15 + 5 + 5) / 3; two tasks 1.5667 and tc −1.7667
        self.assertAlmostEqual(a[0]["point"], -1.8667, places=3)
        self.assertAlmostEqual(a[1]["point"], 6.9, places=3)
        self.assertAlmostEqual(a[2]["point"], 8.3333, places=3)
        self.assertAlmostEqual(a[3]["point"], (2 * 1.5667 - 1.7667) / 3, places=3)
        # beta: the IQM of four runs cuts one from each end, so g0's single pass is cut
        self.assertAlmostEqual(b[0]["point"], -5.2, places=3)
        self.assertAlmostEqual(b[1]["point"], 5.4, places=3)
        self.assertAlmostEqual(b[2]["point"], 5.9, places=3)
        self.assertEqual([r["pass_rate"] for r in a], [round(1 / 3, 4), 1.0, round(1 / 3, 4), round(5 / 9, 4)])
        self.assertEqual([r["pass_rate"] for r in b], [0.25, 1.0, 1.0])
        # three runs per task on alpha, four on beta: the index and episode alignments part
        self.assertEqual([r["episodes_cum"] for r in a], [9, 18, 27, 36])
        self.assertEqual([r["episodes_cum"] for r in b], [12, 24, 36])
        self.assertEqual([r["tokens_cum"] for r in a], [180, 360, 540, 720])
        self.assertEqual([r["tokens_cum"] for r in b], [240, 480, 720])
        self.assertTrue(all(r["seconds_cum"] > 0 for r in a + b))
        self.assertEqual([r["x"] for r in self.cmp["curves"]["by_episodes"]["beta"]], [12, 24, 36])
        for r in a + b:
            self.assertTrue(r["lo"] <= r["point"] <= r["hi"], r)
            self.assertEqual(r["metric_source"], "evolution.generations[].iqm_by_task")
        self.assertIn("differ in length (3, 4)", self.cmp["curves"]["reading"])
        self.assertIn("the two alignments differ", self.cmp["curves"]["reading"])

    def test_the_race_threshold_states_its_source_and_the_race_is_won_on_episodes(self):
        race = self.cmp["race"]
        self.assertTrue(race["measurable"], race["reason"])
        th = race["threshold"]
        # the midpoint between beta's g0 (−5.2, the lowest g0) and alpha's g1 (6.9, the highest recommended)
        self.assertAlmostEqual(th["value"], 0.85, places=4)
        self.assertEqual(th["lowest_g0"], {"value": -5.2, "label": "beta", "id": "g0"})
        self.assertEqual(th["highest_recommended"], {"value": 6.9, "label": "alpha", "id": "g1", "which": "recommended"})
        self.assertIn("midpoint between the lowest generation-0 point (−5.2, beta g0)", th["source"])
        self.assertIn("highest recommended point (6.9, alpha g1)", th["source"])
        self.assertEqual(race["reached"]["alpha"], {"index": 1, "id": "g1", "episodes_cum": 18, "point": 6.9})
        self.assertEqual(race["reached"]["beta"], {"index": 1, "id": "g1", "episodes_cum": 24, "point": 5.4})
        a = self.cmp["curves"]["by_index"]["alpha"]
        by_index = sum((a[i]["point"] + a[i + 1]["point"]) / 2 for i in range(3))
        self.assertAlmostEqual(race["auc"]["alpha"]["by_index"]["value"], by_index, places=3)
        self.assertAlmostEqual(race["auc"]["alpha"]["by_index"]["value"], 14.5278, places=2)
        self.assertAlmostEqual(race["auc"]["alpha"]["by_episodes"]["value"], 9 * by_index, places=2)
        self.assertAlmostEqual(race["auc"]["beta"]["by_index"]["value"], (-5.2 + 5.4) / 2 + (5.4 + 5.9) / 2, places=3)
        self.assertAlmostEqual(race["auc"]["beta"]["by_episodes"]["value"], 12 * ((-5.2 + 5.4) / 2 + (5.4 + 5.9) / 2), places=2)
        self.assertIn("alpha reached it at g1 (index 1, 18 episodes, 6.9)", race["reading"])
        self.assertIn("beta reached it at g1 (index 1, 24 episodes, 5.4)", race["reading"])
        self.assertIn("longer series has more room", race["reading"])

    def test_peak_goes_to_alpha_and_final_to_beta_through_the_pair_machinery(self):
        peak, final = self.cmp["peak"], self.cmp["final"]
        self.assertTrue(peak["measurable"], peak["reason"])
        self.assertEqual((peak["a"]["id"], peak["b"]["id"]), ("g1", "g2"))
        self.assertEqual(peak["basis"], "recommended generation vs recommended generation")
        # every alpha g1 run returns 6.9, every beta g2 run 5.9: P(b > a) is 0 with no width
        self.assertEqual(peak["improvement"], {"point": 0.0, "lo": 0.0, "hi": 0.0})
        self.assertEqual(peak["separates"], "alpha")
        self.assertAlmostEqual(peak["metric"]["a"], 6.9, places=3)
        self.assertAlmostEqual(peak["metric"]["b"], 5.9, places=3)
        self.assertAlmostEqual(peak["metric"]["delta"], -1.0, places=3)
        self.assertEqual(peak["pass_rate"]["a"], 1.0)
        self.assertEqual(peak["pass_rate"]["b"], 1.0)
        self.assertEqual(sorted(peak["per_task"]), ["ta", "tb", "tc"])
        self.assertEqual(peak["per_task"]["ta"]["p"], 0.0)
        self.assertAlmostEqual(peak["per_task"]["ta"]["a"], 6.9, places=3)
        self.assertAlmostEqual(peak["per_task"]["ta"]["delta"], -1.0, places=3)
        self.assertIsNotNone(peak["iqm_pooled"]["a"]["point"])
        self.assertIsNotNone(peak["behaviour_distance"])
        self.assertIn("alpha g1 against beta g2", peak["reading"])
        self.assertIn("every resample keeps alpha g1 ahead", peak["reading"])
        self.assertEqual(len(peak["pairs"]), 1)
        self.assertTrue(final["measurable"], final["reason"])
        self.assertEqual((final["a"]["id"], final["b"]["id"]), ("g3", "g2"))
        self.assertEqual(final["improvement"], {"point": 1.0, "lo": 1.0, "hi": 1.0})
        self.assertEqual(final["separates"], "beta")
        self.assertEqual(final["pass_rate"], {"a": round(5 / 9, 4), "b": 1.0, "passes_a": 5, "passes_b": 12,
                                              "episodes_a": 9, "episodes_b": 12})
        self.assertIn("every resample keeps beta g2 ahead", final["reading"])

    def test_by_generation_aligns_by_index_and_leaves_the_short_side_null(self):
        rows = self.cmp["by_generation"]
        self.assertEqual([r["index"] for r in rows], [0, 1, 2, 3])
        self.assertEqual([r["a"]["id"] for r in rows], ["g0", "g1", "g2", "g3"])
        self.assertEqual([r["b"]["id"] if r["b"] else None for r in rows], ["g0", "g1", "g2", None])
        self.assertIsNone(rows[3]["improvement"])
        self.assertIsNone(rows[3]["behaviour_distance"])
        self.assertEqual(rows[3]["reason"], "one lineage has no generation at this index")
        for r in rows[:3]:
            self.assertIsNotNone(r["improvement"]["point"], r)
            self.assertIsNotNone(r["behaviour_distance"], r)
        self.assertEqual(rows[1]["improvement"], {"point": 0.0, "lo": 0.0, "hi": 0.0})
        self.assertEqual(rows[1]["separates"], "alpha")
        self.assertEqual(rows[0]["a"]["episodes_cum"], 9)
        self.assertEqual(rows[0]["b"]["episodes_cum"], 12)
        self.assertIn("P(beta@k > alpha@k)", self.cmp["by_generation_reading"])
        self.assertIn(f"at most {ec.BY_GENERATION_CAP}", self.cmp["by_generation_note"])

    def test_process_tallies_retention_and_the_mechanisms_that_paid(self):
        pa, pb = self.cmp["process"]["alpha"], self.cmp["process"]["beta"]
        self.assertEqual((pa["steps"], pa["gamed"], pa["forgot"]), (3, 1, 0))
        self.assertEqual(pa["protected_touched"], 2, "config.checks and tools.check at one step, the episodes' "
                                                     "second sighting of tools.check not counted twice")
        self.assertEqual([(t["step"], t["path"]) for t in pa["protected_touched_paths"]],
                         [(2, "config.checks"), (2, "tools.check")])
        self.assertEqual(pa["over_budget"], 0)
        self.assertEqual(pa["collapsed"], 0)
        self.assertEqual(pa["collapsed_basis"], "the engine's step flags")
        # the engine flags a step noisy only when neither axis clears 0.5; the gamed step's
        # success axis clears it downward, so nothing here was kept on noise
        self.assertEqual(pa["accepted_on_noise_steps"], [])
        self.assertEqual(pa["accepted_on_noise"], 0)
        self.assertIn("neither the return axis nor the success axis", pa["accepted_on_noise_rule"])
        ret = pa["retention"]
        self.assertEqual(ret["ever_solved"], ["ta", "tb", "tc"])
        self.assertEqual(ret["solved_at_recommended"], ["ta", "tb", "tc"])
        self.assertEqual(ret["solved_at_last"], ["ta", "tb"])
        self.assertEqual(ret["lost"], ["tc"])
        self.assertAlmostEqual(ret["at_last"], 2 / 3, places=4)
        self.assertEqual(ret["never_solved"], [])
        self.assertIn("0.5", ret["rule"])
        self.assertIsNotNone(pa["drift_from_origin_at_last"])
        self.assertEqual(pa["score"][:2], [3, 0])
        self.assertAlmostEqual(pa["score"][2], -2 / 3, places=4)
        self.assertEqual(pa["score"][3], 0)
        # mechanisms: rule_add twice (g1 +8.77, g3 −7.88), config once (+1.43, and gamed)
        m = pa["mechanisms"]
        self.assertEqual(sorted(m), ["config", "rule_add"])
        self.assertEqual(m["rule_add"]["steps"], 2)
        self.assertEqual(m["rule_add"]["to"], ["g1", "g3"])
        self.assertAlmostEqual(m["rule_add"]["mean_delta"], ((6.9 + 1.8667) + ((2 * 1.5667 - 1.7667) / 3 - 8.3333)) / 2, places=3)
        self.assertEqual((m["rule_add"]["delta_positive"], m["rule_add"]["delta_negative"]), (1, 1))
        self.assertEqual(m["rule_add"]["improved"], 1)
        self.assertAlmostEqual(m["config"]["mean_delta"], 8.3333 - 6.9, places=3)
        self.assertEqual(m["config"]["verdicts"], {"gamed": 1})
        self.assertEqual(pa["best_paying_mechanism"], "config",
                         "the gamed step paid most on the metric; the verdict beside it says how")
        self.assertIn("best-paying mechanism config (n=1", pa["reading"])
        self.assertIn("lost tc", pa["reading"])
        self.assertEqual((pb["steps"], pb["gamed"], pb["forgot"], pb["traded"], pb["improved"]), (2, 0, 0, 0, 2))
        self.assertEqual((pb["protected_touched"], pb["accepted_on_noise"]), (0, 0))
        self.assertEqual(pb["retention"]["lost"], [])
        self.assertEqual(pb["retention"]["at_last"], 1.0)
        self.assertEqual(pb["score"], [0, 0, -1.0, 0])
        self.assertEqual(pb["best_paying_mechanism"], "skill_add")
        self.assertAlmostEqual(pb["mechanisms"]["skill_add"]["mean_delta"], 5.4 + 5.2, places=3)
        self.assertAlmostEqual(pb["mechanisms"]["memory"]["mean_delta"], 0.5, places=3)

    def test_task_race(self):
        tr = self.cmp["task_race"]
        self.assertEqual(tr["first_solver"], {"ta": "alpha", "tb": "alpha", "tc": "alpha"})
        self.assertEqual(tr["never_solved"], {"alpha": [], "beta": []})
        tc = tr["tasks"]["tc"]
        self.assertEqual(tc["alpha"]["first_solved_index"], 1)
        self.assertEqual(tc["alpha"]["first_solved_id"], "g1")
        self.assertEqual(tc["alpha"]["first_solved_episodes_cum"], 18)
        self.assertFalse(tc["alpha"]["solved_at_last"])
        self.assertEqual(tc["alpha"]["pass_curve"], [round(1 / 3, 4), 1.0, round(1 / 3, 4), round(1 / 3, 4)])
        self.assertEqual(tc["beta"]["first_solved_episodes_cum"], 24)
        self.assertTrue(tc["beta"]["solved_at_last"])
        self.assertEqual(tc["beta"]["pass_curve"], [0.25, 1.0, 1.0])

    def test_the_four_verdicts_disagree_and_the_reading_names_every_axis(self):
        v = self.cmp["verdict"]
        self.assertEqual((v["peak"], v["final"], v["learning"], v["process"]), ("alpha", "beta", "alpha", "beta"))
        self.assertEqual(v["learning_basis"], "fewest episodes to the threshold (18)")
        self.assertEqual(v["process_basis"], "decided on gamed + protected touched: alpha 3, beta 0")
        self.assertEqual(v["peak_basis"], "the interval clears 0.5 in favour of alpha")
        self.assertEqual(v["final_basis"], "the interval clears 0.5 in favour of beta")
        for phrase in ("peak: alpha — alpha g1 vs beta g2", "final: beta — alpha g3 vs beta g2",
                       "learning: alpha — fewest episodes", "process: beta — decided on",
                       "alpha takes peak, learning", "beta takes final, process"):
            self.assertIn(phrase, v["reading"])
        self.assertIn("alpha (4 generations, 36 episodes) and beta (3 generations, 36 episodes) over 3 shared tasks",
                      self.cmp["narrative"])
        self.assertTrue(self.cmp["advisory"].startswith("[insufficient]"), self.cmp["advisory"])
        self.assertIn("stratified bootstrap", self.cmp["advisory"])

    def test_the_evals_side_by_side_and_the_transfer_at_the_stated_level(self):
        ev = self.cmp["evals"]
        self.assertEqual(list(self.cmp)[-1], "evals", "the new key is last; everything before it is unchanged")
        self.assertTrue(ev["measurable"], ev["reason"])
        self.assertEqual(ev["alpha"], 0.05)
        self.assertIn("one test per metric and lineage, unadjusted", ev["transfer_rule"])
        a, b = ev["lineages"]
        self.assertEqual((a["label"], b["label"]), ("alpha", "beta"))
        # what each eval learned is read off its own coevolution section, not recomputed
        from deepcompare.coevolve import coevolve
        co_a = coevolve(read_lineage(self.tmpdir / "alpha"), self.cmp["lineages"][0]["evolution"], samples=SAMPLES)
        self.assertEqual(a["adopted"], [m for e in co_a["eval_generations"][1:] for m in e["adopted"]])
        self.assertEqual(a["eval_generations"], len(co_a["eval_generations"]))
        self.assertEqual(a["tested"], co_a["integrity"]["multiplicity"]["tested"])
        self.assertEqual(a["rejected"], sum(a["rejected_by"].values()))
        self.assertEqual(a["recommended"], {k: co_a["recommended"][k] for k in ("base", "evolved", "agree")})
        self.assertEqual(a["narrative"], co_a["narrative"])
        self.assertEqual(a["closures"], co_a["flow"]["summary"]["closures"])
        # the two evals learned different things, so nothing is shared
        self.assertTrue(a["adopted"] and b["adopted"])
        self.assertEqual(set(a["adopted"]) & set(b["adopted"]), set())
        self.assertEqual(ev["shared_metrics"], [])
        # every learned metric is applied to the other lineage's last step, once
        pairs = [(t["metric"], t["learned_on"], t["applied_to"], t["step"]) for t in ev["transfer"]]
        self.assertEqual(pairs, [(m, "alpha", "beta", "g1→g2") for m in a["adopted"]]
                         + [(m, "beta", "alpha", "g2→g3") for m in b["adopted"]])
        for t in ev["transfer"]:
            self.assertEqual(t["alpha"], 0.05)
            self.assertTrue(t["measurable"], t["reason"])
            self.assertTrue(t["delta"]["lo"] <= t["delta"]["point"] <= t["delta"]["hi"])
            self.assertEqual(t["informative_there"], t["delta"]["lo"] > 0 or t["delta"]["hi"] < 0)
            self.assertIn(t["metric"], t["reading"])
        # the reading names both evals, what only one learned, and declares no winner
        self.assertIn("alpha's eval grew to e1 and adopted", ev["reading"])
        self.assertIn("beta's eval grew to e1 and adopted", ev["reading"])
        self.assertIn("no metric was adopted by more than one eval", ev["reading"])
        self.assertIn("no eval is declared the better one", ev["reading"])
        for word in ("better eval", "wins", "winner:"):
            self.assertNotIn(word, ev["reading"])

    def test_deterministic_twice_and_json_clean(self):
        ra, rb = self.tmpdir / "alpha", self.tmpdir / "beta"
        again = ec.compare_lineages([ra, rb], samples=SAMPLES)
        self.assertEqual(json.dumps(self.cmp, sort_keys=True), json.dumps(again, sort_keys=True))


# ---------------------------------------------------------------- shapes the layer must not guess at

class ShapeTest(_Temp):
    def test_a_threshold_override_says_so(self):
        c = self.pair(threshold=6.0)
        self.assertEqual(c["race"]["threshold"]["source"], "--threshold")
        self.assertEqual(c["race"]["threshold"]["value"], 6.0)
        self.assertEqual(c["race"]["reached"]["alpha"]["id"], "g1")
        self.assertIsNone(c["race"]["reached"]["beta"], "beta's highest point is 5.9")
        self.assertEqual(c["verdict"]["learning"], "alpha")
        self.assertIn("only alpha reached the threshold", c["verdict"]["learning_basis"])

    def test_no_shared_task_is_not_a_comparison(self):
        c = self.pair(b=beta_gens(tasks=("tx", "ty")))
        self.assertFalse(c["measurable"])
        self.assertIn("no task is shared", c["reason"])
        self.assertEqual(c["tasks"]["shared"], [])
        self.assertEqual(c["tasks"]["only"], {"alpha": ["ta", "tb", "tc"], "beta": ["tx", "ty"]})
        self.assertEqual([ln["label"] for ln in c["lineages"]], ["alpha", "beta"])
        self.assertEqual(c["lineages"][1]["evolution"]["family"], "beta", "each lineage's own section still ships")
        for key in ("curves", "race", "peak", "final", "task_race"):
            self.assertFalse(c[key]["measurable"], key)
        self.assertEqual(c["by_generation"], [])
        self.assertEqual({ax: c["verdict"][ax] for ax in ec.AXES}, {ax: None for ax in ec.AXES})
        self.assertFalse(c["evals"]["measurable"])
        self.assertEqual((c["evals"]["lineages"], c["evals"]["transfer"], c["evals"]["reading"]), ([], [], c["reason"]))

    def test_an_unshared_task_is_excluded_and_the_metric_recomputed(self):
        extra = alpha_gens(tasks=("ta", "tb", "tc", "td"))
        c = self.pair(a=extra)
        self.assertTrue(c["measurable"])
        self.assertEqual(c["tasks"]["only"], {"alpha": ["td"], "beta": []})
        a = c["curves"]["by_index"]["alpha"]
        self.assertEqual(a[1]["metric_source"], "recomputed over the shared tasks")
        self.assertAlmostEqual(a[1]["point"], 6.9, places=3)
        self.assertEqual(a[0]["episodes_cum"], 9, "td's episodes are not counted")
        self.assertIn("excluded as unshared: alpha td", c["narrative"])
        own = c["lineages"][0]["evolution"]["generations"][1]["iqm_by_task"]["per_task"]
        self.assertEqual(sorted(own), ["ta", "tb", "tc", "td"], "the lineage's own section still covers every task it ran")

    def test_one_lineage_is_not_a_comparison(self):
        ra = write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        c = ec.compare_lineages([ra], samples=SAMPLES)
        self.assertFalse(c["measurable"])
        self.assertEqual(c["reason"], "one lineage; a comparison needs two")
        self.assertEqual(len(c["lineages"]), 1)
        self.assertEqual(c["lineages"][0]["recommended"], {"id": "g1", "index": 1})
        self.assertEqual(ec.compare_lineages([], samples=SAMPLES)["measurable"], False)

    def test_a_lineage_of_one_generation_has_no_race_and_no_process_to_judge(self):
        c = self.pair(b=beta_gens()[:1])
        self.assertTrue(c["measurable"])
        self.assertTrue(c["peak"]["measurable"], c["peak"]["reason"])
        self.assertEqual((c["peak"]["a"]["id"], c["peak"]["b"]["id"]), ("g1", "g0"))
        self.assertEqual(c["peak"]["separates"], "alpha")
        self.assertFalse(c["race"]["measurable"])
        self.assertIn("beta has one generation", c["race"]["reason"])
        self.assertIsNone(c["race"]["auc"]["beta"]["by_index"]["value"])
        self.assertEqual(c["race"]["auc"]["beta"]["by_index"]["reason"], "a curve of one point has no area")
        self.assertIsNone(c["verdict"]["learning"])
        self.assertFalse(c["process"]["beta"]["measurable"])
        self.assertEqual(c["process"]["beta"]["steps"], 0)
        self.assertIsNone(c["verdict"]["process"])
        self.assertIn("beta has no step to judge", c["verdict"]["process_basis"])
        self.assertEqual([r["b"] is None for r in c["by_generation"]], [False, True, True, True])

    def test_an_unreadable_lineage_says_which(self):
        ra = write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        (self.tmp / "empty").mkdir()
        c = ec.compare_lineages([ra, self.tmp / "empty"], samples=SAMPLES)
        self.assertFalse(c["measurable"])
        self.assertIn("empty: no */agent.json", c["reason"])
        self.assertEqual([ln["label"] for ln in c["lineages"]], ["alpha", "empty"])

    def test_two_lineages_of_the_same_family_get_distinct_labels_and_still_compare(self):
        c = self.pair(a=alpha_gens(family="toy"), b=beta_gens(family="toy"), a_json=_lineage_json("toy"),
                      b_json=_lineage_json("toy"), a_name="run1", b_name="run2")
        self.assertTrue(c["measurable"], c["reason"])
        self.assertEqual([ln["label"] for ln in c["lineages"]], ["toy", "toy (run2)"])
        self.assertTrue(c["peak"]["measurable"], c["peak"]["reason"])
        self.assertEqual(c["peak"]["separates"], "toy")
        self.assertEqual(c["verdict"]["final"], "toy (run2)")
        self.assertEqual(sorted(c["process"]), ["toy", "toy (run2)"])

    def test_the_by_generation_cap_is_honoured_and_said(self):
        c = self.pair(by_generation_cap=2) if False else None  # compare_lineages has no cap argument
        ra = read_lineage(self.tmp / "alpha") if (self.tmp / "alpha").is_dir() else None
        if ra is None:
            ra = read_lineage(write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha")))
        rb = read_lineage(write_lineage(self.tmp / "beta", beta_gens(), _lineage_json("beta")))
        ea, eb = evolve(ra, samples=SAMPLES), evolve(rb, samples=SAMPLES)
        c = ec.evolution_compare([ra, rb], [ea, eb], samples=SAMPLES, by_generation_cap=2)
        rows = c["by_generation"]
        self.assertIsNotNone(rows[1]["improvement"])
        self.assertIsNone(rows[2]["improvement"])
        self.assertIn("past the cap of 2", rows[2]["reason"])
        self.assertTrue(c["peak"]["measurable"], "peak and final are built whatever the cap")
        with self.assertRaises(ValueError):
            ec.evolution_compare([ra, rb], [ea], samples=SAMPLES)
        # an eval section already computed is reused; the block is the same either way
        from deepcompare.coevolve import coevolve
        given = ec.evolution_compare([ra, rb], [ea, eb], samples=SAMPLES, by_generation_cap=2,
                                     coevolutions=[coevolve(ra, ea, samples=SAMPLES), None])
        self.assertEqual(json.dumps(given["evals"], sort_keys=True), json.dumps(c["evals"], sort_keys=True))
        with self.assertRaises(ValueError):
            ec.evolution_compare([ra, rb], [ea, eb], samples=SAMPLES, coevolutions=[None, None, None])


# ---------------------------------------------------------------- the command

class CommandTest(_Temp):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_evolve_against_writes_the_primary_output_plus_the_comparison(self):
        ra = write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        rb = write_lineage(self.tmp / "beta", beta_gens(), _lineage_json("beta"))
        out_dir = self.tmp / "out"
        code, out, err = self.run_cli("evolve", str(ra), "--against", str(rb), "-o", str(out_dir),
                                      "--samples", str(SAMPLES))
        self.assertEqual(code, 0, err)
        agg = json.loads((out_dir / "aggregate.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(agg["rl"]["agents"]), ["alpha@g2", "alpha@g3"], "lineage A's last pair, as evolve writes")
        self.assertEqual(agg["evolution"]["family"], "alpha")
        cmp = agg["evolution_compare"]
        self.assertTrue(cmp["measurable"], cmp["reason"])
        self.assertEqual({ax: cmp["verdict"][ax] for ax in ec.AXES},
                         {"peak": "alpha", "final": "beta", "learning": "alpha", "process": "beta"})
        self.assertEqual(cmp["lineages"][0]["evolution"]["recommended"]["id"], agg["evolution"]["recommended"]["id"])
        self.assertEqual(cmp["evals"]["lineages"][0]["narrative"], agg["coevolution"]["narrative"],
                         "the primary's eval is the one already on the aggregate")
        self.assertEqual(len(cmp["evals"]["transfer"]), len(cmp["evals"]["lineages"][0]["adopted"])
                         + len(cmp["evals"]["lineages"][1]["adopted"]))
        self.assertTrue((out_dir / "report_ta.json").is_file())
        if (ROOT / "web" / "blocks.html").is_file():
            self.assertTrue((out_dir / "report.html").is_file())
        self.assertIn("Comparison: alpha (4 gen, 36 ep) vs beta (3 gen, 36 ep)", out)
        self.assertIn("Tasks: 3 shared", out)
        self.assertIn("Peak     alpha g1 vs beta g2  P(b > a) 0.00 [0.00, 0.00]", out)
        self.assertIn("→ alpha", out)
        self.assertIn("Final    alpha g3 vs beta g2  P(b > a) 1.00 [1.00, 1.00]", out)
        self.assertIn("Learning threshold +0.85", out)
        self.assertIn("reached: alpha g1 @18 ep, beta g1 @24 ep  → alpha", out)
        self.assertIn("Process  alpha: 3 step(s)  gamed 1  forgot 0  traded 0  on-noise 0  protected 2", out)
        self.assertIn("retention 2/3  best mechanism config (n=1", out)
        self.assertIn("Verdict  peak alpha · final beta · learning alpha · process beta", out)
        self.assertIn("Advisory: [insufficient]", out)

        # the alias is the same command and writes the same section
        out2 = self.tmp / "out2"
        code, out_b, err = self.run_cli("evolve-compare", str(ra), str(rb), "-o", str(out2), "--samples", str(SAMPLES))
        self.assertEqual(code, 0, err)
        agg2 = json.loads((out2 / "aggregate.json").read_text(encoding="utf-8"))
        self.assertEqual(json.dumps(agg2["evolution_compare"], sort_keys=True), json.dumps(cmp, sort_keys=True))
        self.assertIn("Verdict  peak alpha · final beta · learning alpha · process beta", out_b)

        # --threshold is stated; --fail-on still judges the primary lineage
        code, out_c, _ = self.run_cli("evolve-compare", str(ra), str(rb), "-o", str(out2), "--samples", str(SAMPLES),
                                      "--threshold", "6", "--fail-on", "gamed")
        self.assertEqual(code, 1)
        self.assertIn("Learning threshold +6.00 (--threshold)", out_c)
        self.assertIn("fail-on: step 2 gamed", out_c)

    def test_the_alias_needs_two_lineages(self):
        ra = write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        code, _, err = self.run_cli("evolve-compare", str(ra), "-o", str(self.tmp / "o"))
        self.assertEqual(code, 2)
        self.assertIn("at least two lineage directories", err)

    def test_without_against_no_comparison_is_written(self):
        ra = write_lineage(self.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        out_dir = self.tmp / "out"
        code, out, err = self.run_cli("evolve", str(ra), "-o", str(out_dir), "--samples", str(SAMPLES))
        self.assertEqual(code, 0, err)
        agg = json.loads((out_dir / "aggregate.json").read_text(encoding="utf-8"))
        self.assertNotIn("evolution_compare", agg)
        self.assertNotIn("Comparison:", out)


# ---------------------------------------------------------------- the demo, pinned to what the data says

class DemoCompareTest(unittest.TestCase):
    """ledger-agent against memo-agent, at the default resamples: the four
    axes and the headline numbers, whatever they are."""

    @classmethod
    def setUpClass(cls):
        cls.cmp = ec.compare_lineages([DEMO, DEMO_B])

    def test_the_four_axes(self):
        v = self.cmp["verdict"]
        self.assertTrue(self.cmp["measurable"], self.cmp["reason"])
        self.assertEqual({ax: v[ax] for ax in ec.AXES},
                         {"peak": None, "final": "ledger-agent", "learning": None, "process": "memo-agent"})
        self.assertEqual(v["process_basis"], "decided on gamed + protected touched: ledger-agent 3, memo-agent 0")
        self.assertEqual(v["learning_basis"], "ledger-agent and memo-agent reached the threshold at the same episode count (90)")
        self.assertIn("ledger-agent takes final, memo-agent takes process", v["reading"])

    def test_peak_does_not_separate_and_final_goes_to_ledger(self):
        peak, final = self.cmp["peak"], self.cmp["final"]
        self.assertEqual((peak["a"]["id"], peak["b"]["id"]), ("g4", "g5"))
        self.assertEqual([round(peak["improvement"][k], 2) for k in ("point", "lo", "hi")], [0.40, 0.27, 0.52])
        self.assertIsNone(peak["separates"])
        self.assertEqual((round(peak["metric"]["a"], 2), round(peak["metric"]["b"], 2)), (5.52, 5.84))
        self.assertEqual((peak["pass_rate"]["passes_a"], peak["pass_rate"]["passes_b"]), (25, 24))
        self.assertEqual((final["a"]["id"], final["b"]["id"]), ("g6", "g6"))
        self.assertEqual([round(final["improvement"][k], 2) for k in ("point", "lo", "hi")], [0.32, 0.20, 0.45])
        self.assertEqual(final["separates"], "ledger-agent")
        self.assertEqual((round(final["metric"]["a"], 2), round(final["metric"]["b"], 2)), (5.09, 4.34))

    def test_the_race_and_the_curves(self):
        race = self.cmp["race"]
        self.assertAlmostEqual(race["threshold"]["value"], 0.7, places=2)
        self.assertEqual(race["threshold"]["lowest_g0"]["label"], "memo-agent")
        self.assertEqual(race["threshold"]["highest_recommended"], {"value": 5.8444, "label": "memo-agent", "id": "g5",
                                                                    "which": "recommended"})
        self.assertEqual({k: (r["id"], r["episodes_cum"]) for k, r in race["reached"].items()},
                         {"ledger-agent": ("g2", 90), "memo-agent": ("g2", 90)})
        a = self.cmp["curves"]["by_index"]["ledger-agent"]
        b = self.cmp["curves"]["by_index"]["memo-agent"]
        self.assertEqual([round(r["point"], 2) for r in a], [-1.22, -0.06, 1.03, 0.68, 5.52, 4.94, 5.09])
        self.assertEqual([round(r["point"], 2) for r in b], [-4.44, -2.06, 1.06, 2.0, 1.8, 5.84, 4.34])
        self.assertEqual([r["episodes_cum"] for r in a], [30, 60, 90, 120, 150, 180, 210])
        self.assertGreater(race["auc"]["ledger-agent"]["by_episodes"]["value"], race["auc"]["memo-agent"]["by_episodes"]["value"])
        seps = [(r["index"], r["separates"]) for r in self.cmp["by_generation"] if r["separates"]]
        self.assertEqual(seps, [(4, "ledger-agent"), (5, "ledger-agent"), (6, "ledger-agent")])

    def test_process_two_findings_to_none_and_the_mechanisms_that_paid(self):
        pa, pb = self.cmp["process"]["ledger-agent"], self.cmp["process"]["memo-agent"]
        self.assertEqual((pa["gamed"], pa["forgot"], pa["traded"], pa["protected_touched"], pa["over_budget"],
                          pa["collapsed"], pa["accepted_on_noise"]), (1, 1, 2, 2, 4, 0, 4))
        self.assertEqual(pa["accepted_on_noise_steps"], ["g1", "g2", "g5", "g6"])
        self.assertEqual([(t["from"], t["to"], t["path"]) for t in pa["protected_touched_paths"]],
                         [("g2", "g3", "config.checks"), ("g2", "g3", "tools.run_check")])
        self.assertEqual(pa["retention"]["lost"], ["rl03_flag_rollout"])
        self.assertEqual((pb["gamed"], pb["forgot"], pb["traded"], pb["improved"], pb["protected_touched"],
                          pb["over_budget"], pb["accepted_on_noise"]), (0, 0, 0, 3, 0, 0, 3))
        self.assertEqual(pb["retention"]["lost"], [])
        self.assertEqual(pb["retention"]["at_last"], 1.0)
        self.assertEqual((pa["best_paying_mechanism"], pa["mechanisms"]["mixed"]["steps"],
                          round(pa["mechanisms"]["mixed"]["mean_delta"], 2)), ("mixed", 1, 4.84))
        self.assertEqual((pb["best_paying_mechanism"], pb["mechanisms"]["memory"]["steps"],
                          round(pb["mechanisms"]["memory"]["mean_delta"], 2)), ("memory", 2, 3.58))
        self.assertEqual(self.cmp["task_race"]["first_solver"],
                         {"rl01_ledger_reconcile": "ledger-agent", "rl02_flaky_test": "ledger-agent",
                          "rl03_flag_rollout": "ledger-agent", "rl04_query_regression": "ledger-agent",
                          "rl05_incident_postmortem": None, "rl06_api_contract": None})

    def test_the_evals_ledger_agent_learned_three_memo_agent_none_and_what_transferred(self):
        ev = self.cmp["evals"]
        self.assertTrue(ev["measurable"], ev["reason"])
        a, b = ev["lineages"]
        self.assertEqual(a["label"], "ledger-agent")
        self.assertEqual(a["eval_generations"], 4)
        self.assertEqual(a["adopted"], ["verified_rate", "clean_pass_rate", "frugal_pass_rate"])
        self.assertEqual(a["active"], ["verified_rate", "frugal_pass_rate"])
        self.assertEqual((a["retired"], a["demoted"], a["unconfirmed"]), (["clean_pass_rate"], [], ["clean_pass_rate"]))
        self.assertEqual((a["tested"], a["rejected"]), (22, 19))
        self.assertEqual(a["rejected_by"], {"computable": 1, "distinct": 11, "informative": 6, "not_already": 1})
        self.assertEqual((a["min_adjusted_alpha"], a["drift"]), (0.0063, 0.3333))
        self.assertEqual((a["closures"], a["closures_learned"], a["hindsight_changed"], a["hindsight_lag_max"]), (4, 2, 0, 3))
        self.assertEqual(a["recommended"], {"base": "g4", "evolved": "g4", "agree": True})
        self.assertEqual(b["label"], "memo-agent")
        self.assertEqual(b["eval_generations"], 1)
        self.assertEqual((b["adopted"], b["active"], b["retired"]), ([], [], []))
        self.assertEqual((b["tested"], b["rejected"]), (6, 6))
        self.assertEqual(b["rejected_by"], {"distinct": 3, "informative": 3}, "every candidate noise or redundant")
        self.assertEqual((b["min_adjusted_alpha"], b["drift"], b["hindsight_lag_max"]), (0.0167, 0.0, None))
        self.assertEqual(b["recommended"], {"base": "g5", "evolved": "g5", "agree": True})
        self.assertEqual(ev["shared_metrics"], [])
        # the transfer: ledger-agent's three metrics on memo-agent's last step, g5→g6, one test each at 0.05
        rows = {t["metric"]: t for t in ev["transfer"]}
        self.assertEqual([t["metric"] for t in ev["transfer"]], ["verified_rate", "clean_pass_rate", "frugal_pass_rate"])
        self.assertTrue(all((t["learned_on"], t["applied_to"], t["step"], t["alpha"]) == ("ledger-agent", "memo-agent", "g5→g6", 0.05)
                            for t in ev["transfer"]))
        v = rows["verified_rate"]
        self.assertEqual((v["from"], v["to"], v["delta"]), (1.0, 1.0, {"point": 0.0, "lo": 0.0, "hi": 0.0}))
        self.assertFalse(v["informative_there"], "memo-agent verifies every episode at both generations: nothing to say")
        self.assertIn("says nothing there", v["reading"])
        c = rows["clean_pass_rate"]
        self.assertEqual(c["status"], "retired")
        self.assertEqual((c["from"], round(c["to"], 4)), (1.0, 0.8571))
        self.assertEqual({k: round(c["delta"][k], 4) for k in ("point", "lo", "hi")}, {"point": -0.1429, "lo": -0.1429, "hi": -0.1429})
        self.assertTrue(c["informative_there"] and c["flags_there"], "a zero-width interval below zero, as the resamples fall")
        self.assertEqual(c["moved"], "down")
        self.assertIn("it would flag that step", c["reading"])
        f = rows["frugal_pass_rate"]
        self.assertEqual((f["from"], f["to"], f["delta"]["point"]), (0.9, 0.9, 0.0))
        self.assertEqual((f["delta"]["lo"], f["delta"]["hi"]), (-0.2, 0.3))
        self.assertFalse(f["informative_there"])
        reading = ev["reading"]
        self.assertIn("ledger-agent's eval grew to e3 and adopted verified_rate, clean_pass_rate and frugal_pass_rate "
                      "(retired clean_pass_rate): 22 candidates tested, 19 rejected", reading)
        self.assertIn("memo-agent's eval learned nothing and stayed at e0: 6 candidates tested, 6 rejected "
                      "(3 by distinct, 3 by informative)", reading)
        self.assertIn("only ledger-agent's eval learned a metric", reading)
        self.assertIn("no eval is declared the better one", reading)


# ---------------------------------------------------------------- the embedded copies and the registry

class EvalSummaryTest(unittest.TestCase):
    """The evals side read off hand-built sections: the longest hindsight
    lag ignores a metric that first flags after its adoption, and a
    transferred ``uses:<tool>`` metric reads 0 where the other lineage
    never called the tool."""

    @staticmethod
    def section(caught_at: dict) -> dict:
        return {"measurable": True, "reason": None, "metrics": {}, "integrity": {}, "ledger": [], "eval_generations": [{"id": "e0"}],
                "hindsight": {"caught_at": caught_at}, "recommended": {"base": "g1", "evolved": "g1", "agree": True},
                "flow": {"summary": {}}, "narrative": "n"}

    def test_hindsight_lag_max_ignores_a_metric_that_first_flags_after_its_adoption(self):
        # finding 11: caught_at.lag = adopted − first is negative then, and max() narrated "the longest hindsight lag -2 steps"
        view = {"label": "L", "family": "f"}
        only_after = ec._eval_summary(view, self.section({"m": {"lag": -2}}))
        self.assertIsNone(only_after["hindsight_lag_max"])
        self.assertNotIn("hindsight lag", ec._evals_reading([only_after], [], []))
        at_adoption = ec._eval_summary(view, self.section({"m": {"lag": -2}, "n": {"lag": 0}}))
        self.assertIsNone(at_adoption["hindsight_lag_max"], "adopted at the first step it flags is no lag")
        late = ec._eval_summary(view, self.section({"m": {"lag": -2}, "n": {"lag": 0}, "o": {"lag": 3}, "p": {"lag": None}}))
        self.assertEqual(late["hindsight_lag_max"], 3)
        self.assertIn("the longest hindsight lag 3 steps", ec._evals_reading([late], [], []))

    def test_a_transferred_tool_metric_reads_0_where_the_other_lineage_never_called_the_tool(self):
        # finding 10: the other lineage's table carried only its own uses:<tool> columns, so the metric was "unreadable"
        from deepcompare.coevolve import FEATURES, parse_spec
        from test_coevolve import gen_rows
        spec = parse_spec({"id": "uses_run_check_rate", "feature": "uses:run_check", "agg": "rate", "direction": "neutral"},
                          list(FEATURES) + ["uses:run_check"])
        co_a = {"measurable": True, "metrics": {"uses_run_check_rate": {"spec": spec, "status": "adopted"}},
                "eval_generations": [{"id": "e0", "adopted": []}, {"id": "e1", "adopted": ["uses_run_check_rate"]}]}
        co_b = {"measurable": True, "metrics": {}, "eval_generations": [{"id": "e0", "adopted": []}]}
        views = [{"label": "A", "family": "a", "lineage": {}}, {"label": "B", "family": "b", "lineage": {}}]
        strip = lambda rows: [dict(e, values={k: v for k, v in e["values"].items() if not k.startswith("uses:")}) for e in rows]  # noqa: E731
        table_b = [{"id": "g0", "index": 0, "episodes": strip(gen_rows(lambda t, k: {}))},
                   {"id": "g1", "index": 1, "episodes": strip(gen_rows(lambda t, k: {}))}]
        self.assertFalse(any(k.startswith("uses:") for row in table_b for e in row["episodes"] for k in e["values"]))
        rows = ec._transfer(views, [co_a, co_b], [[], table_b], SAMPLES)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["metric"], row["learned_on"], row["applied_to"], row["step"]), ("uses_run_check_rate", "A", "B", "g0→g1"))
        self.assertTrue(row["measurable"], row["reason"])
        self.assertEqual((row["from"], row["to"], row["delta"]), (0.0, 0.0, {"point": 0.0, "lo": 0.0, "hi": 0.0}))
        self.assertFalse(row["informative_there"])
        self.assertIn("0 → 0", row["reading"])
        self.assertFalse(any(k.startswith("uses:") for row_ in table_b for e in row_["episodes"] for k in e["values"]),
                         "the other lineage's table is read, not rewritten")
        where = parse_spec({"id": "pass_when_checked", "feature": "success", "agg": "rate", "direction": "up",
                            "where": {"feature": "uses:run_check", "op": ">", "value": 0}}, list(FEATURES) + ["uses:run_check"])
        co_a["metrics"]["pass_when_checked"] = {"spec": where, "status": "adopted"}
        co_a["eval_generations"][1]["adopted"].append("pass_when_checked")
        filtered = ec._transfer(views, [co_a, co_b], [[], table_b], SAMPLES)[1]
        self.assertFalse(filtered["measurable"])
        self.assertIn("0 episodes after the filter", filtered["reason"], "a filter on the tool selects nothing there, and says so")


class EmbeddedCopyTest(unittest.TestCase):
    """``lineages[i].evolution`` carries each lineage's own section without
    the episode timelines — the page's timescape reads those from
    ``aggregate.evolution`` — and says so beside ``episodes_capped``."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="evc-embed-"))
        cls.a = write_lineage(cls.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        cls.b = write_lineage(cls.tmp / "beta", beta_gens(), _lineage_json("beta"))
        cls.lineage_a, cls.lineage_b = read_lineage(cls.a), read_lineage(cls.b)
        cls.ev_a = evolve(cls.lineage_a, samples=SAMPLES)
        cls.ev_b = evolve(cls.lineage_b, samples=SAMPLES)
        cls.before = json.dumps(cls.ev_a, sort_keys=True)
        cls.cmp = ec.evolution_compare([cls.lineage_a, cls.lineage_b], [cls.ev_a, cls.ev_b], samples=SAMPLES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_embedded_copy_drops_every_timeline_and_says_so(self):
        for entry in self.cmp["lineages"]:
            for g in entry["evolution"]["generations"]:
                self.assertTrue(g["episodes"], "the episodes themselves stay")
                self.assertTrue(all("timeline" not in e for e in g["episodes"]))
                keys = list(g)
                self.assertEqual(keys[keys.index("episodes_capped") + 1], "timelines")
                self.assertEqual(g["timelines"], ec.TIMELINES_OMITTED)
                self.assertEqual(g["timelines"], "omitted; see aggregate.evolution")

    def test_the_copy_keeps_every_other_key_in_order_and_the_original_untouched(self):
        self.assertEqual(json.dumps(self.ev_a, sort_keys=True), self.before, "the section given is not mutated")
        self.assertTrue(all("timeline" in e for g in self.ev_a["generations"] for e in g["episodes"]))
        copy_ = self.cmp["lineages"][0]["evolution"]
        self.assertEqual(list(copy_), list(self.ev_a))
        for g_in, g_out in zip(self.ev_a["generations"], copy_["generations"]):
            self.assertEqual([k for k in g_out if k != "timelines"], list(g_in))
            for e_in, e_out in zip(g_in["episodes"], g_out["episodes"]):
                self.assertEqual(e_out, {k: v for k, v in e_in.items() if k != "timeline"})
        self.assertLess(len(json.dumps(copy_)), len(self.before))   # on the demo the section shrinks to under a third

    def test_embedded_evolution_on_odd_shapes(self):
        self.assertEqual(ec.embedded_evolution({"generations": []}), {"generations": []})
        self.assertEqual(ec.embedded_evolution({"measurable": False}), {"measurable": False, "generations": []})
        out = ec.embedded_evolution({"generations": [{"id": "g0", "episodes": [{"timeline": [], "x": 1}]}]})
        self.assertEqual(out["generations"], [{"id": "g0", "episodes": [{"x": 1}], "timelines": ec.TIMELINES_OMITTED}])
        self.assertIsNone(ec.embedded_evolution(None))

    def test_an_unreadable_lineage_is_embedded_the_same_way(self):
        broken = dict(self.ev_b, measurable=False, reason="broken on purpose")
        c = ec.evolution_compare([self.lineage_a, self.lineage_b], [self.ev_a, broken], samples=SAMPLES)
        self.assertFalse(c["measurable"])
        for entry in c["lineages"]:
            for g in entry["evolution"]["generations"]:
                self.assertTrue(all("timeline" not in e for e in g["episodes"]))
                self.assertEqual(g["timelines"], ec.TIMELINES_OMITTED)

    def test_the_comparison_is_an_on_demand_lineage_section_after_evolution(self):
        from deepcompare import evolve as evolve_module, sections
        sec = sections.get("lineage", "evolution_compare")
        self.assertTrue(sec.on_demand)
        self.assertEqual(sec.requires, ("evolution",))
        self.assertEqual(sections.registered("lineage")[:3], ["evolution", "coevolution", "evolution_compare"])
        agg = evolve_module.attach_sections(self.lineage_a, {}, samples=SAMPLES, against=[str(self.b)])
        self.assertEqual(list(agg), ["evolution", "coevolution", "data_evolution", "harness_evolution", "evolution_compare"])
        self.assertEqual(json.dumps(agg["evolution"], sort_keys=True), self.before)
        self.assertEqual(json.dumps(agg["evolution_compare"], sort_keys=True), json.dumps(self.cmp, sort_keys=True))
        alone = evolve_module.attach_sections(self.lineage_a, {}, samples=SAMPLES)
        self.assertEqual(list(alone), ["evolution", "coevolution", "data_evolution", "harness_evolution"], "without lineages to compare against, the comparison does not attach")

    def test_compare_lineages_reuses_a_lineage_already_read(self):
        reread = ec.compare_lineages([str(self.a), str(self.b)], samples=SAMPLES, evolutions=[self.ev_a])
        reused = ec.compare_lineages([str(self.a), str(self.b)], samples=SAMPLES, evolutions=[self.ev_a],
                                     lineages=[self.lineage_a])
        self.assertEqual(json.dumps(reread, sort_keys=True), json.dumps(reused, sort_keys=True))
        self.assertEqual(json.dumps(reused, sort_keys=True), json.dumps(self.cmp, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
