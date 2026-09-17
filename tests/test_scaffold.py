"""The scaffold actuator: the changes the loop could make, and could not.

The agentic loop had one actuator. `ACTIONS` was `("compare",
"test-prompt", "stop")` and the state it edited was `state["prompts"]`:
it could change how an agent thinks and nothing else. Meanwhile the
triage engine classifies every recommendation it makes by where the fix
lives, and of its nineteen categories only four are prompt-shaped —
eleven name the scaffold, which the loop could not touch at all.

These are the tests for closing that: `deepcompare/scaffold.py` turns the
engine's own findings into hypotheses about the two knobs a harness
genuinely has — the tool table a run is offered and the settings the loop
obeys — `deepcompare/planner.py` schedules them as paired experiments
beside the prompt ones, and the loop runs the variant under the changed
scaffold.

Two invariants hold most of these up.

A hypothesis the runner cannot express is not a hypothesis. It goes in
`unactionable` with the reason, where it can be counted, rather than being
quietly dropped or — worse — proposed and never testable.

And a knob whose effect no trace records could never be judged. That is
why the budget settings the loop reads are the settings the actuator may
propose, and why the whole vocabulary lives in the trace schema: `BudgetKnobTest`
pins the three lists against each other, so a change that gets past the
actuator cannot fail at the moment the run testing it is written down.
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
READ_TOOLS = [{"name": "grep", "effect": "read"}, {"name": "web", "effect": "read"}]
WRITE_TOOLS = READ_TOOLS + [{"name": "ship", "effect": "write"}]


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
        got = S.hypotheses(_agg([_action("efficiency"), _action("parallel_reads"), _action("prompt_cache")]),
                           "a", tools=TOOLS)
        self.assertEqual(got["proposed"], [])
        self.assertEqual(len(got["unactionable"]), 3)
        for row in got["unactionable"]:
            self.assertIn("varies only", row["reason"])
            self.assertIn(row["effort"], ("infrastructure", "control-flow"))

    def test_the_generic_refusal_no_longer_reaches_any_architecture_finding(self):
        """`safety`, `verification` and `calibration` are the whole of the
        architecture class, and each now has a rule with its own guard. A
        finding there may still be unactionable — usually is — but it gets
        the sentence its own guard wrote, never the one that says this
        harness has no knob for the class at all."""
        architecture = [c for c, v in EFFORT.items() if v[0] == "architecture"]
        self.assertEqual(sorted(architecture), ["calibration", "safety", "verification"])
        got = S.hypotheses(_agg([_action(c) for c in architecture]), "a", tools=TOOLS)
        self.assertEqual(len(got["unactionable"]), len(architecture))
        for row in got["unactionable"]:
            self.assertNotIn("varies only", row["reason"])

    def test_a_knob_that_exists_but_has_no_evidence_gives_its_own_reason(self):
        """The three categories the loop grew knobs for do not fall back to
        the generic sentence. Each says what its own guard wanted and did
        not get, which is a different finding from 'no knob reaches this'."""
        got = S.hypotheses(_agg([_action("verification"), _action("result_cache"), _action("recovery")]),
                           "a", tools=TOOLS)
        self.assertEqual(got["proposed"], [])
        reasons = {row["category"]: row["reason"] for row in got["unactionable"]}
        self.assertEqual(set(reasons), {"verification", "result_cache", "recovery"})
        for reason in reasons.values():
            self.assertNotIn("varies only", reason)
        self.assertIn("names no tool on offer to require", reasons["verification"])
        self.assertIn("no tool on offer declares a read effect", reasons["result_cache"])
        self.assertIn("no terminations were read", reasons["recovery"])

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

    def test_the_reading_claims_no_handover_that_did_not_happen(self):
        """An investigation is skipped too, but it is left to a person, not
        to the prompt loop. The clause used to be printed unconditionally,
        so a reading with no prompt-shaped finding at all still ended '0
        prompt-shaped findings is left to the prompt loop' — a handover that
        did not happen, in a sentence that disagreed with itself about
        number."""
        got = S.hypotheses(_agg([_action("efficiency"), _action("oracle")]), "a", tools=TOOLS)
        self.assertTrue(got["skipped"], "the investigation must still be counted")
        self.assertNotIn("prompt loop", got["reading"])
        self.assertNotIn(" 0 ", " " + got["reading"])
        web = _action("tool_availability", details=['at "web"'])
        one = S.hypotheses(_agg([web, _action("reasoning")]), "a", tools=TOOLS, calls={"web": 9})["reading"]
        many = S.hypotheses(_agg([web, _action("reasoning"), _action("planning")]), "a",
                            tools=TOOLS, calls={"web": 9})["reading"]
        self.assertIn("1 prompt-shaped finding is left", one)
        self.assertIn("2 prompt-shaped findings are left", many)

    def test_the_reading_says_what_could_be_tried_and_what_could_not(self):
        got = S.hypotheses(_agg([_action("tool_availability", details=['at "web"']), _action("verification"),
                                 _action("reasoning")]), "a", tools=TOOLS, calls={"web": 9})
        self.assertIn("can be tested", got["reading"])
        self.assertIn("cannot", got["reading"])
        self.assertIn("prompt loop", got["reading"])



class ReachTest(unittest.TestCase):
    """The claim about the whole vocabulary, checked rather than written.

    The module's central claim — a hypothesis the runner cannot express is
    not a hypothesis — is about every category the engine can recommend,
    not about whatever a batch happened to turn up. Until `reach()` it
    could only be read as a table in the docs, which is to say it could go
    out of date with nothing noticing.

    These tests exist so the map cannot drift from the rules: every
    category `reach()` calls reachable must really be proposable, and every
    one it calls unreachable must really end in `unactionable`.
    """

    def test_every_category_the_engine_can_recommend_is_placed_exactly_once(self):
        got = S.reach()
        self.assertEqual(len(got["rows"]), len(EFFORT))
        self.assertEqual(sorted(r["category"] for r in got["rows"]), sorted(EFFORT))
        self.assertEqual(sum(got["counts"].values()), len(EFFORT))
        self.assertEqual([r["category"] for r in got["rows"]],
                         [r["category"] for r in sorted(got["rows"], key=lambda r: (r["effort"], r["category"]))],
                         "the order is the vocabulary's, not a dict's")

    def test_a_category_called_reachable_really_is_proposable(self):
        """The anti-drift half that matters. A map saying `safety` is
        reachable while no rule proposes for it would be a claim about this
        harness that this harness does not honour."""
        tools = [{"name": "grep", "effect": "read"}, {"name": "web", "effect": "read"},
                 {"name": "run_check", "effect": "read"}, {"name": "ship", "effect": "write"}]
        for row in S.reach()["rows"]:
            if row["verdict"] != "knob":
                continue
            with self.subTest(category=row["category"]):
                # each rule's evidence, given so the guard can clear: a tool
                # quoted for the tool-schema and gate rules, a termination
                # for recovery
                action = _action(row["category"], details=['at "web"', 'missing "run_check"'])
                got = S.hypotheses(_agg([action]), "a", tools=tools, budget={},
                                   terminations={"too_many_errors": 6, "agent_stop": 4},
                                   calls={"web": 9, "run_check": 5})
                kinds = [p["kind"] for p in got["proposed"]]
                self.assertTrue(kinds, f"{row['category']} is mapped to {row['knob']} and proposed nothing")
                self.assertIn(row["knob"].split(": ")[0], [p["knob"] for p in got["proposed"]])

    def test_a_category_called_unreachable_really_reaches_no_knob(self):
        tools = [{"name": "grep", "effect": "read"}, {"name": "ship", "effect": "write"}]
        for row in S.reach()["rows"]:
            if row["verdict"] != "no knob":
                continue
            with self.subTest(category=row["category"]):
                got = S.hypotheses(_agg([_action(row["category"], details=['at "grep"'])]), "a",
                                   tools=tools, budget={}, calls={"grep": 9})
                self.assertEqual(got["proposed"], [],
                                 f"{row['category']} is mapped as unreachable and proposed something")
                self.assertIn("varies only", got["unactionable"][0]["reason"])

    def test_prompt_and_investigation_are_skipped_not_refused(self):
        for row in S.reach()["rows"]:
            if row["verdict"] not in ("prompt", "investigation"):
                continue
            with self.subTest(category=row["category"]):
                got = S.hypotheses(_agg([_action(row["category"])]), "a", tools=TOOLS)
                self.assertEqual((got["proposed"], got["unactionable"]), ([], []))
                self.assertEqual(len(got["skipped"]), 1)

    def test_the_reading_counts_what_the_rows_say(self):
        got = S.reach()
        for verdict, n in got["counts"].items():
            self.assertEqual(n, len([r for r in got["rows"] if r["verdict"] == verdict]))
        self.assertIn(f"Of {len(EFFORT)} categories", got["reading"])
        self.assertIn(f"{got['counts']['no knob']} name the scaffold", got["reading"])
        self.assertNotIn("categorys", got["reading"])


class BudgetKnobTest(unittest.TestCase):
    """The three settings the loop grew, and the guards that decide when a
    recommendation reaches one.

    The rule every one of them is held to: a knob is only offered when the
    traces say the thing it would change is a thing that happened. A guard
    that does not clear is not silence — it is a specific sentence saying
    what the guard wanted, which is a different finding from "no knob
    reaches this class at all".
    """

    def test_a_result_cache_finding_becomes_the_dedupe_knob(self):
        got = S.hypotheses(_agg([_action("result_cache")]), "a", tools=READ_TOOLS)
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual((prop["kind"], prop["knob"]), ("dedupe_tool_calls", "budget"))
        self.assertEqual(prop["change"], {"budget": {"dedupe_tool_calls": True}})
        self.assertIn("still recorded as a step", prop["why"])

    def test_a_cache_is_never_proposed_over_calls_that_are_not_declared_reads(self):
        """A write served from a cache is a write that silently did not
        happen; an undeclared effect is undeclared, not read-only."""
        for tools in (TOOLS, [{"name": "ship", "effect": "write"}]):
            with self.subTest(tools=tools):
                got = S.hypotheses(_agg([_action("result_cache")]), "a", tools=tools)
                self.assertEqual(got["proposed"], [])
                self.assertIn("no tool on offer declares a read effect", got["unactionable"][0]["reason"])

    def test_the_cache_is_not_proposed_twice(self):
        got = S.hypotheses(_agg([_action("result_cache"), _action("result_cache", tasks=("t2",))]),
                           "a", tools=READ_TOOLS, budget={"dedupe_tool_calls": True})
        self.assertEqual(got["proposed"], [])
        self.assertEqual(len(got["unactionable"]), 2)
        self.assertIn("already deduplicating", got["unactionable"][0]["reason"])

    def test_a_verification_finding_that_names_a_tool_becomes_the_answer_gate(self):
        got = S.hypotheses(_agg([_action("verification", details=['claimed done without "run_check"'])]),
                           "a", tools=TOOLS, calls={"run_check": 4})
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual(prop["kind"], "require_before_answer:run_check")
        self.assertEqual(prop["change"], {"budget": {"require_before_answer": "run_check"}})
        self.assertIn("pushes back once", prop["why"])
        self.assertIn("4 times", prop["why"])

    def test_calibration_is_a_gate_too_because_that_is_the_engines_own_fix(self):
        got = S.hypotheses(_agg([_action("calibration", details=['wrong while confident at "run_check"'])]),
                           "a", tools=TOOLS)
        self.assertEqual([p["kind"] for p in got["proposed"]], ["require_before_answer:run_check"])

    def test_the_gate_will_not_choose_the_agents_check_for_it(self):
        got = S.hypotheses(_agg([_action("verification")]), "a", tools=TOOLS)
        self.assertEqual(got["proposed"], [])
        self.assertIn("will not choose the agent's check", got["unactionable"][0]["reason"])

    def test_a_gate_already_in_force_is_not_proposed_again(self):
        got = S.hypotheses(_agg([_action("verification", details=['missing "run_check"'])]), "a",
                           tools=TOOLS, budget={"require_before_answer": "run_check"})
        self.assertEqual(got["proposed"], [])
        self.assertIn("already requires 'run_check'", got["unactionable"][0]["reason"])

    def test_a_safety_finding_becomes_the_read_before_write_gate(self):
        got = S.hypotheses(_agg([_action("safety", tasks=("t1", "t2"))]), "a", tools=WRITE_TOOLS)
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual(prop["kind"], "require_read_before_write")
        self.assertEqual(prop["change"], {"budget": {"require_read_before_write": True}})
        self.assertIn("it protects the state, it does not teach the agent to look first", prop["why"])
        self.assertIn("writes_before_any_read will go on reporting the attempt", prop["why"])

    def test_a_gate_with_nothing_to_hold_or_nothing_to_clear_it_is_not_offered(self):
        """Both halves have to exist. Without a write there is nothing to
        hold back; without a read the agent could never clear the gate, so
        it would refuse one write and buy nothing."""
        no_write = S.hypotheses(_agg([_action("safety")]), "a", tools=READ_TOOLS)
        self.assertEqual(no_write["proposed"], [])
        self.assertIn("no tool on offer declares a write effect", no_write["unactionable"][0]["reason"])
        no_read = S.hypotheses(_agg([_action("safety")]), "a",
                               tools=[{"name": "ship", "effect": "write"}])
        self.assertEqual(no_read["proposed"], [])
        self.assertIn("could never clear the gate", no_read["unactionable"][0]["reason"])

    def test_a_gate_the_experiment_could_not_see_names_the_eval_gap_instead(self):
        """The guard worth reading. This loop decides by the outcome its
        grader measures, and a gate that protects state buys nothing the
        grader reads — so on a finding confined to runs that passed, the
        experiment would see no difference and revert it. The honest answer
        is to say which reading is missing rather than turn a knob nothing
        would score."""
        action = _action("safety", tasks=("t1", "t2"))
        action["on_passing_runs"] = ["t1", "t2"]
        got = S.hypotheses(_agg([action]), "a", tools=WRITE_TOOLS)
        self.assertEqual(got["proposed"], [])
        reason = got["unactionable"][0]["reason"]
        self.assertIn("every task this finding names also passed", reason)
        self.assertIn("The missing reading is the eval's, not the harness's", reason)
        # one failing task among them and it is testable again
        action["on_passing_runs"] = ["t1"]
        self.assertEqual([p["kind"] for p in S.hypotheses(_agg([action]), "a", tools=WRITE_TOOLS)["proposed"]],
                         ["require_read_before_write"])

    def test_a_write_gate_already_in_force_is_not_proposed_again(self):
        got = S.hypotheses(_agg([_action("safety")]), "a", tools=WRITE_TOOLS,
                           budget={"require_read_before_write": True})
        self.assertEqual(got["proposed"], [])
        self.assertIn("already refuses a write before a read", got["unactionable"][0]["reason"])

    def test_runs_that_died_on_the_tool_error_cap_are_a_recovery_hypothesis(self):
        got = S.hypotheses(_agg([_action("recovery")]), "a", tools=TOOLS,
                           budget={"max_tool_errors": 2},
                           terminations={"too_many_errors": 4, "agent_stop": 6})
        self.assertEqual(len(got["proposed"]), 1)
        prop = got["proposed"][0]
        self.assertEqual(prop["kind"], "raise_cap:max_tool_errors")
        self.assertEqual(prop["change"], {"budget": {"max_tool_errors": 3}})
        self.assertIn("The errors were the agent's", prop["why"])

    def test_the_default_tool_error_cap_is_used_when_the_budget_names_none(self):
        """Unlike the step cap, this one has a default the loop really
        applies, so there is a number to raise even when nothing wrote it
        down — and it is the same number `run_task` uses."""
        got = S.hypotheses(_agg([_action("recovery")]), "a", tools=TOOLS, budget={},
                           terminations={"too_many_errors": 5, "agent_stop": 5})
        self.assertEqual(got["proposed"][0]["change"],
                         {"budget": {"max_tool_errors": S.DEFAULT_TOOL_ERRORS + 2}})

    def test_a_recovery_finding_on_runs_that_all_answered_moves_nothing(self):
        got = S.hypotheses(_agg([_action("recovery")]), "a", tools=TOOLS,
                           budget={"max_tool_errors": 3}, terminations={"agent_stop": 10})
        self.assertEqual(got["proposed"], [])
        self.assertIn("changes nothing that was measured", got["unactionable"][0]["reason"])

    def test_the_tool_error_cap_is_not_a_harness_stop(self):
        """`too_many_errors` is deliberately outside `_HARNESS_STOPS`: the
        errors were the agent's, and only the decision of when to stop
        counting them was the loop's. Without a recovery finding it raises
        no step cap."""
        self.assertNotIn("too_many_errors", S._HARNESS_STOPS)
        got = S.hypotheses(_agg([]), "a", tools=TOOLS, budget={"max_steps": 20},
                           terminations={"too_many_errors": 9, "agent_stop": 1})
        self.assertEqual(got["proposed"], [])

    def test_an_agent_that_runs_its_own_loop_gets_no_budget_hypothesis(self):
        """The harness stamps a budget on an external agent's trace and
        nothing obeys it. Proposing a setting there would move the harness
        fingerprint without moving the run — a harness change that did not
        happen, measured as though it had. The tool table is different: the
        runner really does hand that over, so it stays actionable."""
        agg = _agg([_action("result_cache"), _action("verification", details=['missing "run_check"']),
                    _action("recovery"), _action("tool_availability", details=['at "web"'])])
        terms = {"budget_exhausted": 4, "too_many_errors": 4, "agent_stop": 2}
        common = dict(tools=READ_TOOLS + [{"name": "run_check", "effect": "read"}],
                      budget={"max_steps": 20}, terminations=terms, calls={"web": 9, "run_check": 4})
        driven = S.hypotheses(agg, "a", **common)
        external = S.hypotheses(agg, "a", enforces_budget=False, **common)
        self.assertGreater(len([p for p in driven["proposed"] if p["knob"] == "budget"]), 1)
        self.assertEqual([p["knob"] for p in external["proposed"]], ["tools"],
                         "a setting nothing enforces was proposed as a hypothesis")
        budget_reasons = [u["reason"] for u in external["unactionable"] if "runs its own loop" in u["reason"]]
        self.assertEqual(len(budget_reasons), 4, "a budget rule that went quiet instead of saying why")
        for reason in budget_reasons:
            self.assertIn("without moving the run", reason)

    def test_no_proposal_can_make_a_budget_the_recorder_would_refuse(self):
        """The knob vocabulary lives in the trace schema, so a change that
        gets past `apply_change` cannot fail at the moment the run testing
        it is written down. One list, three modules."""
        from deepcompare.trace import BUDGET_FLAGS, BUDGET_NAMES, budget_value_ok

        self.assertEqual(set(BUDGET_FLAGS) | set(BUDGET_NAMES) | {"max_steps", "max_tool_errors"},
                         set(S.BUDGET_KNOBS), "a knob the trace schema and the actuator disagree about")
        cases = [
            (_agg([_action("result_cache")]), READ_TOOLS, {}, None),
            (_agg([_action("verification", details=['missing "run_check"'])]), TOOLS, {}, None),
            (_agg([_action("recovery")]), TOOLS, {}, {"too_many_errors": 5, "agent_stop": 5}),
            (_agg([_action("safety")]), READ_TOOLS + [{"name": "ship", "effect": "write"}], {}, None),
            (_agg([]), TOOLS, {"max_steps": 20}, {"budget_exhausted": 5, "agent_stop": 5}),
        ]
        seen = set()
        for aggregate, tools, budget, terms in cases:
            got = S.hypotheses(aggregate, "a", tools=tools, budget=budget, terminations=terms)
            self.assertTrue(got["proposed"])
            for prop in got["proposed"]:
                after = S.apply_change({"tools": [t["name"] for t in tools], "budget": dict(budget)},
                                       prop["change"])
                for key, value in (prop["change"].get("budget") or {}).items():
                    seen.add(key)
                    self.assertTrue(budget_value_ok(key, value),
                                    f"{key}={value!r} is a setting no trace would accept")
                    self.assertEqual(after["budget"][key], value, "the change did not survive apply_change")
        self.assertEqual(seen, set(S.BUDGET_KNOBS), "a documented knob nothing can propose")

    def test_every_budget_knob_is_one_the_loop_reads(self):
        """The invariant that decides what may become a knob: the loop reads
        its settings from `budget`, so a turned knob is on the trace and
        `harnessevo.fingerprint` reads it back."""
        from deepcompare.harness import agent as agent_mod

        source = Path(agent_mod.__file__).read_text(encoding="utf-8")
        for knob in S.BUDGET_KNOBS:
            self.assertIn(f'budget.get("{knob}")' if knob != "max_steps" else 'budget.get("max_steps")',
                          source, f"{knob} is documented as a knob the loop reads and is not read")


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
