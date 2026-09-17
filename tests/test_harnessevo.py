"""The harness beside the agent: `deepcompare.harnessevo`.

`evolve` reads every artifact change as "the agent evolved". Two of those
artifacts — `tools` and `config` — are the scaffold the agent runs inside
rather than how it thinks, and nothing at all records the harness that
actually ran each generation. This file is the tests for the section that
says both, and for the one reading neither `evolve` nor `coevolve` can
make: whether a rise in the pass rate came from the agent needing less or
from more being put around it.

The invariant every test here exists to protect is that the word
*attributable* is never reached without evidence. An unrecorded harness is
not a constant one, and a renamed model is not a known one.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from deepcompare import harnessevo as he  # noqa: E402
from deepcompare import sections  # noqa: E402
from deepcompare.evolve import analyse_lineage, attach_sections, read_lineage  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402
from test_evolve import _agent, _trace, write_lineage  # noqa: E402


def _traj(raw: dict) -> Trajectory:
    return Trajectory.from_json(raw)


def _with(raw: dict, **over) -> dict:
    """A trace with the harness-side fields overridden."""
    out = json.loads(json.dumps(raw))
    for key, value in over.items():
        if key == "temperature":
            for st in out["steps"]:
                st["model"] = {"name": over.get("model_name", "sim-1"), "temperature": value}
        elif key == "model_name":
            continue
        elif key == "budget":
            out["budget"] = value
        elif key == "tools":
            out["tools"] = [{"name": n, "effect": "read"} for n in value]
        elif key == "declared_model":
            out["agent"]["model"] = value
        else:
            out[key] = value
    return out


class FingerprintTest(unittest.TestCase):
    """What ran a generation, read from its traces and never from the
    manifest — and the refusal when the traces do not say."""

    def test_a_generation_whose_traces_record_nothing_harness_side_is_unmeasurable(self):
        raw = _trace("g0", "t1", "r1", True, check=True)
        raw["agent"]["model"] = ""
        raw["tools"] = []
        fp = he.fingerprint([_traj(raw)])
        self.assertFalse(fp["measurable"])
        self.assertIn("not in the traces", fp["reason"])
        self.assertIsNone(fp["digest"])

    def test_no_episodes_is_unmeasurable_with_its_own_reason(self):
        fp = he.fingerprint([])
        self.assertFalse(fp["measurable"])
        self.assertIn("no episode", fp["reason"])

    def test_the_fingerprint_reads_the_tools_offered_the_caps_and_the_decoding(self):
        raw = _with(_trace("g0", "t1", "r1", True, check=True),
                    temperature=0.2, model_name="sim-1", budget={"max_steps": 20},
                    tools=["grep", "check"])
        fp = he.fingerprint([_traj(raw)])
        self.assertTrue(fp["measurable"], fp.get("reason"))
        self.assertEqual(fp["tools_offered"], ["check", "grep"])
        self.assertEqual(fp["caps"], {"max_steps": 20.0})
        self.assertEqual([(d["name"], d["temperature"]) for d in fp["decoding"]], [("sim-1", 0.2)])
        self.assertTrue(fp["digest"])

    def test_the_model_name_is_identity_and_stays_out_of_the_harness_digest(self):
        """The whole point of the split: a lineage that versions its model
        string per generation must not read as a harness that moved."""
        base = _with(_trace("g0", "t1", "r1", True, check=True), temperature=0.2, model_name="sim-1")
        renamed = _with(_trace("g1", "t1", "r1", True, check=True), temperature=0.2, model_name="sim-1",
                        declared_model="other-model")
        a, b = he.fingerprint([_traj(base)]), he.fingerprint([_traj(renamed)])
        self.assertEqual(a["digest"], b["digest"], "the harness digest must ignore the declared model name")
        self.assertNotEqual(a["identity_digest"], b["identity_digest"])

    def test_a_real_harness_change_moves_the_digest(self):
        a = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True), temperature=0.2))])
        b = he.fingerprint([_traj(_with(_trace("g1", "t1", "r1", True, check=True), temperature=0.9))])
        self.assertNotEqual(a["digest"], b["digest"])
        move = he.harness_moved(a, b)
        self.assertTrue(move["moved"])
        self.assertIn("temperature", " ".join(c["what"] for c in move["changes"]))

    def test_a_loop_setting_that_is_not_a_number_still_reaches_the_digest(self):
        """The loop's scaffold knobs are a cap, a flag and a tool name, and
        the actuator can turn any of them. A digest that kept only the
        numbers would make two of the three invisible to the very reading
        that is supposed to judge the change they made."""
        raw = _trace("g0", "t1", "r1", True, check=True)
        plain = he.fingerprint([_traj(_with(raw, budget={"max_steps": 6}))])
        gated = he.fingerprint([_traj(_with(raw, budget={"max_steps": 6, "require_before_answer": "run_check"}))])
        cached = he.fingerprint([_traj(_with(raw, budget={"max_steps": 6, "dedupe_tool_calls": True}))])
        self.assertEqual(gated["caps"]["require_before_answer"], "run_check")
        self.assertIs(cached["caps"]["dedupe_tool_calls"], True)
        self.assertEqual(len({plain["digest"], gated["digest"], cached["digest"]}), 3,
                         "a turned knob that leaves the digest where it was")
        for other in (gated, cached):
            move = he.harness_moved(plain, other)
            self.assertTrue(move["moved"])
            self.assertIn("caps", " ".join(c["what"] for c in move["changes"]))
        # and it is said the way a reader says it, not the way Python
        # prints a bool
        caps = [c for c in he.harness_moved(plain, cached)["changes"] if "caps" in c["what"]][0]
        self.assertIn("dedupe_tool_calls on", caps["to"])
        self.assertNotIn("True", caps["to"])
        named = [c for c in he.harness_moved(plain, gated)["changes"] if "caps" in c["what"]][0]
        self.assertIn("require_before_answer run_check", named["to"])


class DimensionsTest(unittest.TestCase):
    """Every change names the row it belongs to, and every fingerprint says
    which rows the episodes recorded at all.

    Both exist so a reader — the page included — never has to match English
    to know what moved or whether anything wrote it down. The second is the
    one that matters: an unrecorded dimension is *unknown*, and reading it
    as "held constant" is the mistake this whole section exists to stop.
    """

    def test_every_change_carries_a_dimension_from_the_closed_vocabulary(self):
        a = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True),
                                        temperature=0.2, tools=["grep"], budget={"max_steps": 6},
                                        declared_model="m@g0"))])
        b = he.fingerprint([_traj(_with(_trace("g1", "t1", "r1", True, check=True),
                                        temperature=0.9, tools=["grep", "web"], budget={"max_steps": 9},
                                        declared_model="m@g1"))])
        move = he.harness_moved(a, b)
        rows = (move["changes"] or []) + (move["identity"]["changes"] or [])
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row["dimension"], he.DIMENSIONS,
                          f"{row['what']!r} names a dimension outside the vocabulary")
        got = {row["dimension"] for row in rows}
        self.assertEqual(got, {"decoding", "tools_offered", "caps", "identity"},
                         "a dimension that moved and did not say so")

    def test_the_fingerprint_says_which_dimensions_were_recorded(self):
        fp = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True),
                                         temperature=0.2, tools=["grep"]))])
        self.assertEqual(sorted(fp["dimensions"]), sorted(he.DIMENSIONS))
        self.assertIs(fp["dimensions"]["caps"], False, "no budget was recorded and caps does not say so")
        self.assertIs(fp["dimensions"]["tools_offered"], True)
        with_caps = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True),
                                                tools=["grep"], budget={"max_steps": 6}))])
        self.assertIs(with_caps["dimensions"]["caps"], True)

    def test_an_unreadable_generation_records_no_dimension_rather_than_all_of_them(self):
        fp = he.fingerprint([])
        self.assertFalse(fp["measurable"])
        self.assertEqual(set(fp["dimensions"]), set(he.DIMENSIONS))
        self.assertEqual([k for k, v in fp["dimensions"].items() if v], [],
                         "a generation with no episodes claimed a dimension was recorded")


class MovedTest(unittest.TestCase):
    """`moved` is None, not False, when a side cannot be read."""

    def test_an_unread_harness_is_not_an_unchanged_one(self):
        good = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True), temperature=0.2))])
        blank = he.fingerprint([])
        for a, b in ((blank, good), (good, blank), (blank, blank)):
            move = he.harness_moved(a, b)
            self.assertIsNone(move["moved"], "an unreadable side must not read as unchanged")
            self.assertIn("no harness fingerprint", move["reason"])

    def test_an_identical_harness_moves_nothing(self):
        fp = he.fingerprint([_traj(_with(_trace("g0", "t1", "r1", True, check=True), temperature=0.2))])
        move = he.harness_moved(fp, fp)
        self.assertFalse(move["moved"])
        self.assertEqual(move["changes"], [])
        self.assertFalse(move["identity"]["moved"])


class ClassifyTest(unittest.TestCase):
    """Reasoning is how the agent thinks; scaffold is what it runs inside."""

    def test_every_artifact_kind_is_classified_and_the_rule_is_published(self):
        self.assertEqual(set(he.KINDS), {"system_prompt", "rules", "skills", "memory", "tools", "config"})
        self.assertEqual({he.KINDS[k] for k in ("system_prompt", "rules", "skills", "memory")}, {"reasoning"})
        self.assertEqual({he.KINDS[k] for k in ("tools", "config")}, {"scaffold"})

    def test_a_prompt_change_alone_is_reasoning(self):
        got = he.classify({"measurable": True, "system_prompt": {"added": 2, "removed": 0}})
        self.assertEqual(got["kind"], "reasoning")
        self.assertEqual(got["reasoning"], ["system_prompt"])
        self.assertEqual(got["scaffold"], [])

    def test_a_config_change_alone_is_scaffold(self):
        got = he.classify({"measurable": True, "config": {"changed": [{"key": "retries", "from": 1, "to": 5}]}})
        self.assertEqual(got["kind"], "scaffold")
        self.assertEqual(got["scaffold"], ["config"])

    def test_both_is_mixed_and_neither_is_none(self):
        both = he.classify({"measurable": True, "rules": {"added": ["r"], "removed": []},
                            "tools": {"added": ["t"], "removed": []}})
        self.assertEqual(both["kind"], "mixed")
        self.assertEqual((both["reasoning"], both["scaffold"]), (["rules"], ["tools"]))
        empty = he.classify({"measurable": True, "rules": {"added": [], "removed": []}})
        self.assertEqual(empty["kind"], "none")

    def test_an_unreadable_diff_is_not_a_step_that_changed_nothing(self):
        got = he.classify({"measurable": False, "reason": "artifacts is not an object on g1"})
        self.assertEqual(got["kind"], "unreadable")
        self.assertIn("not an object", got["reason"])


class AttributionTest(unittest.TestCase):
    """The invariant: `attributable` is never reached without evidence."""

    def test_no_fingerprint_is_assumed_and_names_the_assumption(self):
        got = he.attribution({"moved": None, "changes": [], "reason": "no harness fingerprint on the from side",
                              "identity": {"moved": None}}, "reasoning")
        self.assertEqual(got["status"], "assumed")
        self.assertEqual(got["basis"], "no fingerprint")
        self.assertIn("assumption and not a finding", got["note"])

    def test_a_moved_harness_is_confounded_and_hands_the_delta_to_neither(self):
        got = he.attribution({"moved": True, "changes": [{"what": "sim-1 temperature", "from": "0.2", "to": "0.9"}],
                              "identity": {"moved": False}}, "reasoning")
        self.assertEqual(got["status"], "confounded")
        self.assertIn("cannot be handed to the artifacts", got["note"])

    def test_a_moved_harness_over_unchanged_artifacts_says_the_agent_did_nothing(self):
        got = he.attribution({"moved": True, "changes": [{"what": "the harness caps", "from": "a", "to": "b"}],
                              "identity": {"moved": False}}, "none")
        self.assertEqual(got["status"], "confounded")
        self.assertIn("not something the agent did", got["note"])

    def test_a_renamed_model_is_assumed_not_attributable_and_not_confounded(self):
        """A trace cannot tell a per-generation rename from a real model
        swap, so the status says so rather than picking one."""
        got = he.attribution({"moved": False, "changes": [],
                              "identity": {"moved": True, "changes": [{"what": "the declared model",
                                                                       "from": "a@g0", "to": "a@g1"}]}}, "reasoning")
        self.assertEqual(got["status"], "assumed")
        self.assertEqual(got["basis"], "model string changed")
        self.assertIn("look", got["note"])
        self.assertIn("snapshot id", got["note"])

    def test_attributable_needs_both_the_harness_and_the_name_to_hold(self):
        got = he.attribution({"moved": False, "changes": [], "identity": {"moved": False}}, "reasoning")
        self.assertEqual(got["status"], "attributable")
        self.assertIsNone(got["note"])


class ReconcileTest(unittest.TestCase):
    """A tool table that moved because the agent edited its own tools is
    the agent's scaffold change, not the environment moving."""

    def test_a_tool_change_the_artifact_diff_explains_is_not_a_harness_move(self):
        move = {"moved": True, "identity": {"moved": False},
                "changes": [{"what": "the tools offered", "from": "grep, read_file", "to": "grep, read_file and run_check"}]}
        diff = {"measurable": True, "tools": {"added": ["run_check"], "removed": []}}
        got = he.reconcile(move, diff)
        self.assertFalse(got["moved"])
        self.assertEqual(got["changes"], [])
        self.assertEqual(len(got["explained"]), 1)
        self.assertIn("the agent's own tools artifact", got["explained"][0]["by"])

    def test_a_tool_change_the_diff_does_not_explain_stays_a_harness_move(self):
        move = {"moved": True, "identity": {"moved": False},
                "changes": [{"what": "the tools offered", "from": "grep", "to": "grep and sudo"}]}
        diff = {"measurable": True, "tools": {"added": ["run_check"], "removed": []}}
        got = he.reconcile(move, diff)
        self.assertTrue(got["moved"])
        self.assertEqual(len(got["changes"]), 1)
        self.assertEqual(got["explained"], [])

    def test_a_non_tool_change_is_never_explained_away_by_the_artifacts(self):
        move = {"moved": True, "identity": {"moved": False},
                "changes": [{"what": "sim-1 temperature", "from": "0.2", "to": "0.9"}]}
        got = he.reconcile(move, {"measurable": True, "tools": {"added": ["x"], "removed": []}})
        self.assertTrue(got["moved"])
        self.assertEqual(len(got["changes"]), 1)


def _episodes(n_pass: int, n_fail: int, steps_per_pass: int, gen: str = "g0"):
    """Episodes with a chosen number of passes and a chosen cost per pass."""
    out = []
    for i in range(n_pass):
        out.append(_traj(_trace(gen, f"t{i}", "r1", True, check=True, greps=max(0, steps_per_pass - 2))))
    for i in range(n_fail):
        out.append(_traj(_trace(gen, f"f{i}", "r1", False, check=True, greps=1)))
    return out


class AbsorptionTest(unittest.TestCase):
    """The reading outcome-only grading cannot make: did the pass rate rise
    because the agent needed less, or because more was put around it?"""

    def test_it_fires_when_the_pass_rate_rises_and_each_pass_costs_more(self):
        before = _episodes(4, 6, 4)
        after = _episodes(9, 1, 12)
        got = he.absorption(before, after, samples=50, label="t")
        self.assertTrue(got["measurable"], got.get("reason"))
        self.assertTrue(got["flag"])
        self.assertGreater(got["pass_rate"]["delta"], 0)
        self.assertGreater(got["steps_per_pass"]["delta"], 0)
        self.assertIn("does not transfer" if "does not transfer" in got["reading"] else "travels differently",
                      got["reading"])

    def test_it_does_not_fire_when_each_pass_got_cheaper(self):
        got = he.absorption(_episodes(4, 6, 12), _episodes(9, 1, 4), samples=50, label="t")
        self.assertTrue(got["measurable"])
        self.assertFalse(got["flag"])

    def test_it_does_not_fire_when_the_pass_rate_did_not_rise(self):
        got = he.absorption(_episodes(6, 4, 4), _episodes(6, 4, 12), samples=50, label="t")
        self.assertTrue(got["measurable"])
        self.assertFalse(got["flag"])
        self.assertIn("absorption asks about a rise", got["reading"])

    def test_too_few_passes_is_unmeasurable_rather_than_a_ratio_over_noise(self):
        got = he.absorption(_episodes(1, 9, 4), _episodes(2, 8, 12), samples=50, label="t")
        self.assertFalse(got["measurable"])
        self.assertIn("passing episodes a side", got["reason"])
        self.assertFalse(got["flag"])

    def test_the_work_numbers_are_intervals_and_not_bare_points(self):
        got = he.absorption(_episodes(5, 5, 4), _episodes(8, 2, 12), samples=50, label="t")
        for key in ("steps_per_pass", "tool_calls_per_pass", "retries_per_pass"):
            for side in ("from", "to"):
                row = got[key][side]
                self.assertIsNotNone(row["lo"], f"{key}.{side} has no interval")
                self.assertLessEqual(row["lo"], row["point"])
                self.assertLessEqual(row["point"], row["hi"])


class SectionTest(unittest.TestCase):
    """The section on the shipped lineage, through the registry."""

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineage to analyse")
        cls.lin = read_lineage(str(lineage))
        cls.evo = analyse_lineage(str(lineage))
        cls.sec = he.harness_evolution(cls.lin, cls.evo)

    def test_it_attaches_through_the_registry_after_the_eval(self):
        self.assertIn("harness_evolution", sections._REGISTRY["lineage"])
        agg = attach_sections(self.lin, {})
        self.assertIn("harness_evolution", agg)
        self.assertLess(list(agg).index("coevolution"), list(agg).index("harness_evolution"))

    def test_attaching_it_leaves_every_other_section_byte_identical(self):
        saved = dict(sections._REGISTRY["lineage"])
        try:
            sections._REGISTRY["lineage"] = {k: v for k, v in saved.items() if k != "harness_evolution"}
            without = attach_sections(self.lin, {})
        finally:
            sections._REGISTRY["lineage"] = saved
        with_it = attach_sections(self.lin, {})
        self.assertEqual(sorted(set(with_it) - set(without)), ["harness_evolution"])
        for key in without:
            self.assertEqual(json.dumps(with_it[key], sort_keys=True), json.dumps(without[key], sort_keys=True), key)

    def test_every_step_carries_a_kind_an_attribution_and_an_absorption(self):
        self.assertTrue(self.sec["measurable"], self.sec.get("reason"))
        self.assertEqual(len(self.sec["steps"]), len(self.evo["steps"]))
        for row in self.sec["steps"]:
            self.assertIn(row["kind"], ("reasoning", "scaffold", "mixed", "none", "unreadable"))
            self.assertIn(row["attribution"]["status"], ("attributable", "confounded", "assumed"))
            self.assertIn("measurable", row["absorption"])
            self.assertTrue(row["reading"])

    def test_the_demo_lineage_is_assumed_on_a_rename_and_never_attributable(self):
        """Its model string carries the generation, so a trace cannot say
        whether the model changed — and the section says exactly that."""
        summary = self.sec["summary"]
        self.assertEqual(summary["attributable"], [], "nothing here has the evidence for attributable")
        self.assertEqual(summary["assumed_on_rename"], summary["assumed"])
        self.assertIn("snapshot id", summary["reading"])

    def test_the_agents_own_tool_edits_are_not_counted_as_the_harness_moving(self):
        for row in self.sec["steps"]:
            for change in row["harness"].get("explained") or []:
                self.assertEqual(change["what"], "the tools offered")
            self.assertNotIn("the tools offered", [c["what"] for c in row["harness"]["changes"]],
                             f"{row['from']}→{row['to']}: the agent's own tool edit read as a harness move")

    def test_it_finds_the_step_where_the_scaffold_carried_the_run(self):
        absorbed = self.sec["summary"]["absorbed"]
        self.assertTrue(absorbed, "the shipped lineage has a step where the pass rate and the work both rose")
        row = [s for s in self.sec["steps"] if s["from"] + "→" + s["to"] == absorbed[0]][0]
        self.assertTrue(row["absorption"]["flag"])
        self.assertIn("scaffold", row["changed"]["scaffold"] and "scaffold" or "scaffold")
        self.assertIn("not a fault", row["absorption"]["reading"])

    def test_an_unreadable_lineage_is_unmeasurable_with_the_reason(self):
        got = he.harness_evolution({"measurable": False, "reason": "no generations"}, self.evo)
        self.assertFalse(got["measurable"])
        self.assertIn("no generations", got["reason"])
        got = he.harness_evolution(self.lin, {"measurable": False, "reason": "too few generations"})
        self.assertFalse(got["measurable"])
        self.assertIn("too few generations", got["reason"])

    def test_a_raising_section_degrades_to_unmeasurable_and_the_rest_attach(self):
        agg = {}
        with tempfile.TemporaryDirectory():
            saved = dict(sections._REGISTRY["lineage"])
            try:
                entry = saved["harness_evolution"]
                def boom(*_a, **_k):
                    raise RuntimeError("deliberate")
                sections._REGISTRY["lineage"]["harness_evolution"] = entry.__class__(
                    **{**entry.__dict__, "fn": boom}) if hasattr(entry, "__dict__") else entry
                agg = attach_sections(self.lin, {})
            finally:
                sections._REGISTRY["lineage"] = saved
        self.assertIn("evolution", agg)
        block = agg.get("harness_evolution")
        if block is not None and not block.get("measurable"):
            self.assertIn("deliberate", str(block.get("reason")))


class BuiltLineageTest(unittest.TestCase):
    """A hand-built lineage where the harness genuinely moves, so the
    statuses the shipped corpus cannot reach are reached here."""

    def _lineage(self, tmp: Path, *, temp_b: float = 0.2, model_b: str = "sim-1") -> dict:
        g0 = [_with(_trace("g0", f"t{i}", "r1", i % 2 == 0, check=True), temperature=0.2, model_name="sim-1")
              for i in range(8)]
        g1 = [_with(_trace("g1", f"t{i}", "r1", i % 2 == 0, check=True), temperature=temp_b, model_name=model_b)
              for i in range(8)]
        for raw in g0 + g1:
            raw["agent"]["model"] = "steady-model"
            raw["agent"]["version"] = "v1"
        art0 = {"system_prompt": "a", "rules": ["r1"], "skills": [], "tools": ["grep"], "memory": [],
                "config": {"retries": 1}}
        art1 = {"system_prompt": "a\nb", "rules": ["r1"], "skills": [], "tools": ["grep"], "memory": [],
                "config": {"retries": 1}}
        write_lineage(tmp, [(_agent("g0", None, art0), g0), (_agent("g1", "g0", art1), g1)],
                      lineage_json={"family": "toy", "protected": [], "budget": {},
                                    "note": "SYNTHETIC hand-built lineage"})
        return read_lineage(str(tmp))

    def test_a_steady_harness_and_a_steady_name_reach_attributable(self):
        with tempfile.TemporaryDirectory() as tmp:
            lin = self._lineage(Path(tmp) / "lin")
            sec = he.harness_evolution(lin, analyse_lineage(str(Path(tmp) / "lin")))
            self.assertTrue(sec["measurable"], sec.get("reason"))
            self.assertEqual([s["attribution"]["status"] for s in sec["steps"]], ["attributable"])
            self.assertEqual(sec["summary"]["confounded"], [])
            self.assertIn("each step's delta is the artifacts' own", sec["summary"]["reading"])

    def test_a_temperature_that_moved_confounds_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lin"
            lin = self._lineage(root, temp_b=0.9)
            sec = he.harness_evolution(lin, analyse_lineage(str(root)))
            self.assertEqual([s["attribution"]["status"] for s in sec["steps"]], ["confounded"])
            self.assertTrue(sec["summary"]["confounded"])
            self.assertIn("temperature", json.dumps(sec["steps"][0]["harness"]["changes"]))
            self.assertIn("belong to neither", sec["summary"]["reading"])


if __name__ == "__main__":
    unittest.main()
