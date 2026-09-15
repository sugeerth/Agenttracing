"""The scaffold actuator: the changes the loop could make, and could not.

The agentic loop had one actuator. `ACTIONS` was `("compare",
"test-prompt", "stop")` and the state it edited was `state["prompts"]`:
it could change how an agent thinks and nothing else. Meanwhile the
triage engine classifies every recommendation it makes by where the fix
lives, and of its nineteen categories only four are prompt-shaped —
eleven name the scaffold, which the loop could not touch at all.

These are the tests for closing that: `deepcompare/scaffold.py` turns the
engine's own findings into hypotheses about the two knobs a harness
genuinely has, `deepcompare/planner.py` schedules them as paired
experiments beside the prompt ones, and the loop runs the variant under
the changed scaffold.

The invariant several of these exist to hold is that a hypothesis the
runner cannot express is not a hypothesis. It goes in `unactionable`
with the reason, where it can be counted, rather than being quietly
dropped or — worse — proposed and never testable.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from deepcompare import planner as P  # noqa: E402
from deepcompare import scaffold as S  # noqa: E402
from deepcompare.triage import EFFORT  # noqa: E402


def _action(category, *, agents=("a",), tasks=("t1",), title="Do the thing", details=()):
    effort = (EFFORT.get(category) or ("investigation", ""))[0]
    return {"category": category, "title": title,
            "effort": {"class": effort, "detail": EFFORT.get(category, ("", ""))[1]},
            "evidence": {"agents": list(agents), "tasks": list(tasks), "details": list(details)}}


def _agg(actions):
    return {"triage": {"actions": list(actions)}}


TOOLS = [{"name": "grep"}, {"name": "web"}, {"name": "run_check"}]


class VocabularyTest(unittest.TestCase):
    """The split this module exists because of."""

    def test_every_effort_class_the_engine_can_produce_is_placed(self):
        classes = {v[0] for v in EFFORT.values()}
        self.assertEqual(classes - set(S.WHERE), set(), "an effort class with nowhere to live")

    def test_most_of_what_the_engine_recommends_is_not_the_prompt(self):
        where = {}
        for category, (effort, _) in EFFORT.items():
            where.setdefault(S.WHERE.get(effort), []).append(category)
        self.assertEqual(len(where["reasoning"]), 4, sorted(where["reasoning"]))
        self.assertGreaterEqual(len(where["scaffold"]), 11, sorted(where["scaffold"]))
        # the point in one assertion: the loop's one actuator reaches the smaller half
        self.assertGreater(len(where["scaffold"]), len(where["reasoning"]))

    def test_the_knobs_are_the_two_a_trace_records_back(self):
        """A knob whose effect no trace records cannot be judged by the
        reading that judges it, so there are exactly these two."""
        self.assertEqual(set(S.KNOBS), {"tools", "budget"})


class HypothesesTest(unittest.TestCase):
    """What the engine asked for, turned into what can be tried."""

    def test_a_prompt_shaped_finding_is_left_to_the_prompt_loop(self):
        got = S.hypotheses(_agg([_action("reasoning")]), "a", tools=TOOLS)
        self.assertEqual(got["proposed"], [])
        self.assertEqual(got["unactionable"], [])
        self.assertEqual(len(got["skipped"]), 1)
        self.assertIn("prompt-shaped", got["skipped"][0]["reason"])

    def test_an_investigation_finding_is_not_a_change_at_all(self):
        got = S.hypotheses(_agg([_action("oracle")]), "a", tools=TOOLS)
        self.assertEqual((got["proposed"], got["unactionable"]), ([], []))
        self.assertIn("nothing to test until someone looks", got["skipped"][0]["reason"])

    def test_a_tool_schema_finding_naming_a_called_tool_becomes_a_withdrawal(self):
        got = S.hypotheses(_agg([_action("tool_availability", details=['Calls "web" that is not on offer'])]),
                           "a", tools=TOOLS, calls={"web": 9})
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual((prop["kind"], prop["knob"]), ("withdraw_tool:web", "tools"))
        self.assertEqual(prop["change"], {"drop_tools": ["web"]})
        self.assertIn("the scaffold's, not the agent's", prop["why"])

    def test_a_tool_named_but_barely_called_is_not_withdrawn(self):
        got = S.hypotheses(_agg([_action("tool_availability", details=['Calls "web" oddly'])]),
                           "a", tools=TOOLS, calls={"web": 1})
        self.assertEqual(got["proposed"], [])
        self.assertEqual(len(got["unactionable"]), 1)
        self.assertIn("called at least", got["unactionable"][0]["reason"])

    def test_a_tool_the_run_is_not_offered_is_never_proposed(self):
        got = S.hypotheses(_agg([_action("tool_execution", details=['Fails at "sudo"'])]),
                           "a", tools=TOOLS, calls={"sudo": 20})
        self.assertEqual(got["proposed"], [])
        self.assertIn("names no tool this run is offered", got["unactionable"][0]["reason"])

    def test_a_scaffold_finding_with_no_knob_is_counted_not_dropped(self):
        """The list that is the finding: the engine asks for a change and
        this harness has nowhere to put it."""
        got = S.hypotheses(_agg([_action("verification"), _action("result_cache"), _action("recovery")]),
                           "a", tools=TOOLS)
        self.assertEqual(got["proposed"], [])
        self.assertEqual(len(got["unactionable"]), 3)
        for row in got["unactionable"]:
            self.assertIn("varies only", row["reason"])
            self.assertIn(row["effort"], ("architecture", "infrastructure", "control-flow"))

    def test_a_finding_for_another_agent_is_not_this_agents_hypothesis(self):
        got = S.hypotheses(_agg([_action("tool_availability", agents=("b",), details=['at "web"'])]),
                           "a", tools=TOOLS, calls={"web": 9})
        self.assertEqual((got["proposed"], got["unactionable"], got["skipped"]), ([], [], []))

    def test_runs_the_harness_stopped_are_a_budget_hypothesis(self):
        got = S.hypotheses(_agg([]), "a", tools=TOOLS, budget={"max_steps": 20},
                           terminations={"budget_exhausted": 4, "agent_stop": 6})
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual((prop["kind"], prop["knob"]), ("raise_cap:max_steps", "budget"))
        self.assertEqual(prop["change"], {"budget": {"max_steps": 30}})
        self.assertIn("not because the agent did", prop["why"])

    def test_an_agent_that_stopped_itself_needs_no_more_room(self):
        got = S.hypotheses(_agg([]), "a", tools=TOOLS, budget={"max_steps": 20},
                           terminations={"agent_stop": 10})
        self.assertEqual(got["proposed"], [])

    def test_stopped_runs_with_no_cap_recorded_say_so_rather_than_guess(self):
        got = S.hypotheses(_agg([]), "a", tools=TOOLS, budget={},
                           terminations={"budget_exhausted": 5, "agent_stop": 5})
        self.assertEqual(got["proposed"], [])
        self.assertIn("no step cap is recorded", got["unactionable"][0]["reason"])

    def test_the_reading_says_what_could_be_tried_and_what_could_not(self):
        got = S.hypotheses(_agg([_action("tool_availability", details=['at "web"']), _action("verification"),
                                 _action("reasoning")]), "a", tools=TOOLS, calls={"web": 9})
        self.assertIn("can be tested", got["reading"])
        self.assertIn("cannot", got["reading"])
        self.assertIn("prompt loop", got["reading"])


class ApplyTest(unittest.TestCase):
    """A change is a difference, never a whole replacement scaffold."""

    def test_withdrawing_a_tool_leaves_the_others_and_the_budget(self):
        got = S.apply_change({"tools": ["grep", "web"], "budget": {"max_steps": 20}}, {"drop_tools": ["web"]})
        self.assertEqual(got, {"tools": ["grep"], "budget": {"max_steps": 20}})

    def test_raising_a_cap_leaves_the_tools(self):
        got = S.apply_change({"tools": ["grep"], "budget": {"max_steps": 20}}, {"budget": {"max_steps": 30}})
        self.assertEqual(got, {"tools": ["grep"], "budget": {"max_steps": 30}})

    def test_an_unknown_key_is_ignored_rather_than_becoming_a_setting(self):
        before = {"tools": ["grep"], "budget": {"max_steps": 20}}
        self.assertEqual(S.apply_change(before, {"temperature": 0.9}), before)
        self.assertEqual(S.apply_change(before, {"budget": {"max_steps": "lots"}}), before)

    def test_it_never_mutates_the_scaffold_it_was_given(self):
        before = {"tools": ["grep", "web"], "budget": {"max_steps": 20}}
        snapshot = json.dumps(before, sort_keys=True)
        S.apply_change(before, {"drop_tools": ["web"], "budget": {"max_steps": 30}})
        self.assertEqual(json.dumps(before, sort_keys=True), snapshot)


class PlannerTest(unittest.TestCase):
    """Scheduling a scaffold hypothesis beside the prompt ones."""

    def _state(self):
        return P.new_state(["a"], ["t1", "t2"], tools=TOOLS, budget={"max_steps": 20})

    def test_the_state_carries_names_not_objects_so_it_survives_a_ledger(self):
        state = self._state()
        self.assertEqual(state["scaffold"]["a"]["current"]["tools"], ["grep", "web", "run_check"])
        json.dumps(state)  # the ledger is written as JSON; this would raise

    def test_a_scaffold_hypothesis_is_queued_once_per_kind(self):
        state = self._state()
        prop = {"kind": "withdraw_tool:web", "knob": "tools", "change": {"drop_tools": ["web"]}, "why": "w"}
        self.assertEqual(P.add_scaffold_candidates(state, "a", [prop], source="triage"), 1)
        self.assertEqual(P.add_scaffold_candidates(state, "a", [prop], source="triage"), 0)
        self.assertEqual(P.add_scaffold_candidates(state, "a", [{"kind": "x", "change": {}}], source="t"), 0,
                         "an empty change is not a hypothesis")

    def test_a_prompt_hypothesis_is_tested_before_a_scaffold_one(self):
        """A reasoning change travels to another harness and a scaffold
        change does not, so the cheaper claim is tested first."""
        state = self._state()
        state["iterations"].append({"action": "compare"})
        P.add_candidates(state, "a", [{"kind": "cite", "text": "Cite your source."}], source="reading")
        P.add_scaffold_candidates(state, "a", [{"kind": "withdraw_tool:web", "knob": "tools",
                                                "change": {"drop_tools": ["web"]}, "why": "w"}], source="triage")
        self.assertEqual(P.plan(state, runs=2)["action"], "test-prompt")

    def test_the_scaffold_experiment_is_planned_once_no_prompt_hypothesis_is_left(self):
        state = self._state()
        state["iterations"].append({"action": "compare"})
        P.add_scaffold_candidates(state, "a", [{"kind": "withdraw_tool:web", "knob": "tools",
                                                "change": {"drop_tools": ["web"]}, "why": "w"}], source="triage")
        got = P.plan(state, runs=2)
        self.assertEqual(got["action"], "test-scaffold")
        self.assertEqual(got["candidate"]["kind"], "withdraw_tool:web")
        self.assertEqual(got["variant"], "s1")
        self.assertIn("belongs to the scaffold rather than to the agent", got["why"])

    def test_a_kept_scaffold_change_edits_the_scaffold_and_says_it_does_not_travel(self):
        state = self._state()
        cand = {"kind": "withdraw_tool:web", "knob": "tools", "change": {"drop_tools": ["web"]}, "why": "w"}
        P.add_scaffold_candidates(state, "a", [cand], source="triage")
        decision = P.decide_change({"t1": (0, 2), "t2": (1, 2)}, {"t1": (2, 2), "t2": (2, 2)},
                                   agent="a", candidate=cand, family="scaffold")
        self.assertTrue(decision["status"].startswith("kept"))
        self.assertEqual((decision["family"], decision["changes"]), ("scaffold", "scaffold"))
        self.assertFalse(decision["transfers"])
        self.assertIn("does not travel with the agent", decision["why"])
        P.apply_decision(state, decision)
        self.assertEqual(state["scaffold"]["a"]["current"]["tools"], ["grep", "run_check"])
        self.assertEqual(state["scaffold"]["a"]["version"], 1)
        self.assertEqual(state["scaffold"]["a"]["candidates"], [])

    def test_a_reverted_scaffold_change_leaves_the_scaffold_alone(self):
        state = self._state()
        cand = {"kind": "withdraw_tool:web", "knob": "tools", "change": {"drop_tools": ["web"]}, "why": "w"}
        P.add_scaffold_candidates(state, "a", [cand], source="triage")
        decision = P.decide_change({"t1": (2, 2), "t2": (2, 2)}, {"t1": (0, 2), "t2": (0, 2)},
                                   agent="a", candidate=cand, family="scaffold")
        self.assertEqual(decision["status"], "reverted")
        P.apply_decision(state, decision)
        self.assertEqual(state["scaffold"]["a"]["current"]["tools"], ["grep", "web", "run_check"])
        self.assertEqual(state["scaffold"]["a"]["version"], 0)

    def test_a_prompt_decision_still_reads_exactly_as_it_did(self):
        """`decide_prompt` is the same function it was; generalising it
        must not have moved a prompt result by a digit."""
        base, var = {"t1": (0, 2), "t2": (1, 2)}, {"t1": (2, 2), "t2": (2, 2)}
        cand = {"kind": "cite", "text": "Cite.", "source": "reading"}
        old = P.decide_prompt(base, var, agent="a", candidate=cand)
        new = P.decide_change(base, var, agent="a", candidate=cand, family="prompt")
        self.assertEqual(old["status"], new["status"])
        self.assertEqual(old["evidence"], new["evidence"])
        self.assertEqual(old["family"], "prompt")
        self.assertTrue(old["transfers"])
        self.assertNotIn("the scaffold's", old["why"])


class LoopIntegrationTest(unittest.TestCase):
    """The loop end to end with a scaffold hypothesis in the queue."""

    def test_the_loop_runs_the_variant_under_the_changed_scaffold(self):
        import tempfile
        try:
            from helpers_loop import run_demo_loop
        except ImportError:
            self.skipTest("the loop helper is not importable")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "loop"
            ledger = run_demo_loop(out, max_iterations=2, runs=1)
        state = ledger["state"]
        self.assertIn("scaffold", state)
        for agent in state["agents"]:
            slot = state["scaffold"][agent]
            self.assertIsInstance(slot["current"]["tools"], list)
            for name in slot["current"]["tools"]:
                self.assertIsInstance(name, str, "the ledger holds names, not tool objects")
        # the ledger is written as JSON, so the whole state must serialise
        json.dumps(ledger)

    def test_the_variant_actually_runs_under_the_changed_budget(self):
        """The one thing the unit tests cannot show: that `_test_scaffold`
        reaches the runner and the variant's episodes were produced under
        the changed scaffold, not merely labelled as if they were."""
        import json as _json
        import tempfile
        try:
            from helpers_loop import TASKS, factory, tools as loop_tools
        except ImportError:
            self.skipTest("the loop helper is not importable")
        from deepcompare.harness.loop import Loop
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "loop"
            loop = Loop(TASKS, {"steady": "steady", "sloppy": "sloppy"}, out_dir=out,
                        provider_factory=factory, tools=loop_tools(), runs=1, max_iterations=2,
                        budget={"max_steps": 12})
            agent = "steady"
            cand = {"kind": "raise_cap:max_steps", "knob": "budget",
                    "change": {"budget": {"max_steps": 30}}, "why": "w", "source": "triage", "from_tasks": []}
            P.add_scaffold_candidates(loop.state, agent, [cand], source="triage")
            it = loop._test_scaffold({"agent": agent, "candidate": cand, "variant": "s1",
                                      "tasks": [t["id"] for t in TASKS][:1], "runs": 1,
                                      "why": "a hand-built scaffold experiment"}, 1)
            self.assertEqual(it["action"], "test-scaffold")
            self.assertEqual(it["decision"]["family"], "scaffold")
            # the variant's traces carry the raised cap; the baseline's the old one
            traces = [p for p in sorted((out / "iter-01" / "traces").glob("*.json"))
                      if p.name != "RUN_MANIFEST.json"]
            self.assertTrue(traces, "the experiment wrote no traces")
            caps = {}
            for path in traces:
                data = _json.loads(path.read_text(encoding="utf-8"))
                caps.setdefault(data["agent"]["name"], set()).add(
                    (data.get("budget") or {}).get("max_steps"))
            self.assertEqual(caps.get(agent), {12}, "the baseline ran under the scaffold in force")
            self.assertEqual(caps.get(f"{agent}+s1"), {30}, "the variant ran under the changed scaffold")

    def test_a_compare_iteration_reports_what_it_could_not_act_on(self):
        import tempfile
        try:
            from helpers_loop import run_demo_loop
        except ImportError:
            self.skipTest("the loop helper is not importable")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "loop"
            ledger = run_demo_loop(out, max_iterations=2, runs=1)
        compares = [it for it in ledger["state"]["iterations"] if it["action"] == "compare"]
        self.assertTrue(compares)
        first = compares[0]
        self.assertIn("scaffold_added", first)
        self.assertIn("scaffold_unactionable", first)
        self.assertIn("scaffold", first)
        for agent, row in first["scaffold"].items():
            self.assertTrue(row["reading"], f"{agent} has no scaffold reading")


if __name__ == "__main__":
    unittest.main()
