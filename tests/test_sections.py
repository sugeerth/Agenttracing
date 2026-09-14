"""The section registry, the envelope builders and the shared prose helpers.

The registry (``deepcompare.sections``) is what wires a section to a
report: these tests pin that ``requires`` orders the plan, that a section
which raises is recorded as unmeasurable while the rest still attach, that
a ``requires`` nothing provides is reported, and that the real registry
attaches today's sections in today's order — the order the shipped JSON
outputs were pinned with.

The ``_text`` hand-checks quote the strings the sections produced before
the helpers moved (taken from the sections' own tests), so a helper that
drifts is caught here rather than in a narrative two modules away.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import _stats, _text, sections  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.section import is_measurable, measurable, unmeasurable  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

DEMO = ROOT / "demo" / "traces"

#: the order report.compare attached its sections by hand before the registry
PAIR_ORDER = ["success_analysis", "uncertainty", "semantic", "counterfactual", "shapley", "process", "tradeoff",
              "efficiency", "diagnosis", "internals", "reading", "timing", "horizon", "impact", "trust",
              "tools_profile", "verdict_card", "feedback", "rl", "budget", "fetches", "data"]
#: the order suite.analyse_runs attached its sections by hand before the registry
AGGREGATE_ORDER = ["equality", "routing", "stability", "reliability", "task_signal", "diagnosis_consolidated",
                   "paired_inference", "triage", "scorecard", "rl", "budget", "fetches", "data"]


class RegistryTest(unittest.TestCase):
    def test_requires_orders_the_plan_whatever_the_registration_order(self):
        with sections.isolated():
            calls = []
            sections.register("pair", "c", requires=("b",))(lambda t: calls.append("c") or {"k": "c"})
            sections.register("pair", "b", requires=("a",))(lambda t: calls.append("b") or {"k": "b"})
            sections.register("pair", "a")(lambda t: calls.append("a") or {"k": "a"})
            self.assertEqual(sections.registered("pair"), ["a", "b", "c"])
            target = sections.attach("pair", {})
            self.assertEqual(calls, ["a", "b", "c"])
            self.assertEqual(list(target), ["a", "b", "c"])

    def test_unrelated_sections_attach_in_registration_order(self):
        with sections.isolated():
            for key in ("z", "m", "a"):
                sections.register("aggregate", key)(lambda t: {})
            self.assertEqual(sections.registered("aggregate"), ["z", "m", "a"])

    def test_a_raising_section_is_unmeasurable_and_the_rest_still_attach(self):
        with sections.isolated():
            sections.register("pair", "first")(lambda t: {"ok": True})
            sections.register("pair", "broken", requires=("first",))(lambda t: 1 / 0)
            sections.register("pair", "last", requires=("broken",))(lambda t: {"ok": True})
            target = sections.attach("pair", {})
            self.assertEqual(list(target), ["first", "broken", "last"])
            self.assertFalse(target["broken"]["measurable"])
            self.assertEqual(target["broken"]["reason"], "ZeroDivisionError: division by zero")
            self.assertTrue(target["last"]["ok"])

    def test_a_requires_nothing_provides_is_an_error(self):
        with sections.isolated():
            sections.register("pair", "reader", requires=("nothing_here",))(lambda t: {"ok": True})
            with self.assertRaises(LookupError):
                sections.check("pair")
            # attaching still cannot take the report down: the section says what it needed
            target = sections.attach("pair", {})
            self.assertFalse(target["reader"]["measurable"])
            self.assertIn("nothing_here", target["reader"]["reason"])
            # a base key the caller built before attaching satisfies it
            self.assertEqual([s.key for s in sections.check("pair", base=("nothing_here",))], ["reader"])
            self.assertTrue(sections.attach("pair", {"nothing_here": 1})["reader"]["ok"])

    def test_registration_rejects_a_bad_scope_a_self_requirement_and_a_taken_key(self):
        with sections.isolated():
            with self.assertRaises(ValueError):
                sections.register("nowhere", "x")
            with self.assertRaises(ValueError):
                sections.register("pair", "x", requires=("x",))
            with self.assertRaises(ValueError):
                sections.register("pair", "x", requires=("",))
            sections.register("pair", "x")(lambda t: {})

            def other(t):
                return {}
            with self.assertRaises(ValueError):
                sections.register("pair", "x")(other)

    def test_a_cycle_is_an_error(self):
        with sections.isolated():
            sections.register("lineage", "a", requires=("b",))(lambda t: {})
            sections.register("lineage", "b", requires=("a",))(lambda t: {})
            with self.assertRaises(ValueError):
                sections.registered("lineage")

    def test_the_context_goes_to_sections_that_declare_it(self):
        with sections.isolated():
            seen = {}
            sections.register("pair", "one")(lambda t: seen.setdefault("one", "no ctx") or {})
            sections.register("pair", "two")(lambda t, ctx: seen.setdefault("two", ctx) or {})
            ctx = sections.PairContext(a="A", b="B")
            sections.attach("pair", {}, ctx)
            self.assertEqual(seen["one"], "no ctx")
            self.assertIs(seen["two"], ctx)

    def test_on_demand_sections_attach_only_when_named_and_after_orders_them(self):
        with sections.isolated():
            calls = []
            sections.register("pair", "late", on_demand=True)(lambda t: calls.append("late") or {"k": 1})
            sections.register("pair", "base")(lambda t: calls.append("base") or {"k": 1})
            sections.register("pair", "reader", requires=("base",), after=("late",))(lambda t: calls.append("reader") or {"k": 1})
            target = sections.attach("pair", {})
            self.assertEqual(calls, ["base", "reader"])
            self.assertNotIn("late", target)
            calls.clear()
            sections.attach("pair", target, only=("reader", "late"))
            self.assertEqual(calls, ["late", "reader"])
            with self.assertRaises(LookupError):
                sections.attach("pair", target, only=("absent",))

    def test_the_real_registry_holds_todays_order(self):
        self.assertEqual([k for k in sections.registered("pair") if k != "milestones"], PAIR_ORDER)
        self.assertTrue(sections.get("pair", "milestones").on_demand)
        self.assertEqual(sections.registered("aggregate"), AGGREGATE_ORDER)
        base = ("task", "a", "b", "alignment", "divergences", "attribution", "answer_eval", "metrics_delta")
        sections.check("pair", base=base)
        sections.check("aggregate")

    def test_a_pair_report_carries_the_sections_in_that_order(self):
        a = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        report = compare(a, b)
        keys = list(report)
        self.assertEqual(keys[keys.index("success_analysis"):], PAIR_ORDER)
        json.dumps(report)


class EnvelopeTest(unittest.TestCase):
    def test_measurable_leads_with_version_measurable_reason_then_the_payload(self):
        block = measurable({"rows": [1], "narrative": "x"}, version=3)
        self.assertEqual(list(block), ["version", "measurable", "reason", "rows", "narrative"])
        self.assertEqual((block["version"], block["measurable"], block["reason"]), (3, True, None))
        self.assertTrue(is_measurable(block))

    def test_unmeasurable_carries_the_reason_and_the_empty_shape(self):
        block = unmeasurable("no runs", version=1, rows=[], narrative="No runs.")
        self.assertEqual(list(block), ["version", "measurable", "reason", "rows", "narrative"])
        self.assertEqual((block["measurable"], block["reason"], block["rows"]), (False, "no runs", []))
        self.assertFalse(is_measurable(block))
        with self.assertRaises(ValueError):
            unmeasurable("", version=1)

    def test_a_part_of_a_section_has_no_version_key(self):
        self.assertEqual(list(measurable({"n": 2})), ["measurable", "reason", "n"])
        self.assertEqual(list(unmeasurable("none", n=0)), ["measurable", "reason", "n"])
        self.assertEqual(measurable(), {"measurable": True, "reason": None})

    def test_the_rl_sub_sections_serialise_as_the_envelope(self):
        from deepcompare.rlaudit import rl_audit
        from deepcompare.rlspace import behaviour_space
        self.assertEqual(list(rl_audit([]))[:3], ["version", "measurable", "reason"])
        self.assertEqual(list(behaviour_space([]))[:3], ["version", "measurable", "reason"])


class TextTest(unittest.TestCase):
    """Every expectation here is a string a section's own test already
    pinned, so the helper reproduces the section's spelling."""

    def test_num_spells_a_return_the_way_the_rl_section_did(self):
        self.assertEqual(_text.num(-6.1), "−6.1")            # "orch: return −6.1 over 7 steps"
        self.assertEqual(_text.num(-5.0), "−5")              # "the largest penalty at step 6 (−5, wrong answer)"
        self.assertEqual(_text.num(5.9), "5.9")
        self.assertEqual(_text.num(1.5), "1.5")
        self.assertEqual(_text.num(0.9), "0.9")              # "(orch −1 against 0.9 so far)"
        self.assertEqual(_text.num(None), "—")
        self.assertEqual(_text.num(2.0 / 3, 4), "0.6667")

    def test_num_keeps_the_zeros_the_audit_kept(self):
        self.assertEqual(_text.num(1.5, trim=False), "1.50")
        self.assertEqual(_text.num(2.0, trim=False), "2")

    def test_signed_spells_a_credit_or_a_delta(self):
        self.assertEqual(_text.signed(5.0), "+5")
        self.assertEqual(_text.signed(-0.5), "−0.5")
        self.assertEqual(_text.signed(0.0), "+0")
        self.assertEqual(_text.signed(None), "—")

    def test_pct_is_the_one_spelling_both_sections_used(self):
        self.assertEqual(_text.pct(1 / 7), "14%")          # "latency recorded on 14% of steps"
        self.assertEqual(_text.pct(0.0), "0%")             # "tokens measured on 0% of steps"
        self.assertEqual(_text.pct(1.0), "100%")
        self.assertEqual(_text.pct(0.8475), "85%")
        self.assertEqual(_text.pct(None), "—")
        # the stats section rounded first and formatted second; the strings never part
        for n in range(1, 400):
            for k in range(n + 1):
                share = k / n
                self.assertEqual(_text.pct(share), f"{round(100 * share):.0f}%")

    def test_secs_in_the_three_spellings_the_sections_used(self):
        # impact: whole from 10s, tenths from 1s, hundredths below
        self.assertEqual([_text.secs(v) for v in (12.34, 3.456, 0.256)], ["12s", "3.5s", "0.26s"])
        # tool profile, milestones, impact's cluster scores: never hundredths
        self.assertEqual([_text.secs(v, tenths_above=None) for v in (12.34, 3.456, 0.256)], ["12s", "3.5s", "0.3s"])
        # timing, horizon: tenths kept at any size
        self.assertEqual([_text.secs(v, whole_above=None) for v in (12.34, 3.456, 0.256)], ["12.3s", "3.5s", "0.26s"])

    def test_plural_and_join_names(self):
        self.assertEqual(_text.plural(1, "step"), "1 step")
        self.assertEqual(_text.plural(7, "step"), "7 steps")               # "over 7 steps"
        self.assertEqual(_text.plural(2, "policy", "policies"), "2 policies")
        self.assertEqual(_text.join_names([]), "")
        self.assertEqual(_text.join_names(["c1"]), "c1")
        self.assertEqual(_text.join_names(["c1", "c2"]), "c1 and c2")
        self.assertEqual(_text.join_names(["c1", "c2", "c3"]), "c1, c2 and c3")

    def test_interval_reads_as_the_aggregate_narrative_did(self):
        self.assertEqual(_text.interval(5.23, 1.71, 8.75), "5.23 [1.71, 8.75]")   # the demo's policy-v2
        self.assertEqual(_text.interval(-2.47, -7.25, 2.32), "−2.47 [−7.25, 2.32]")
        self.assertEqual(_text.interval(0.5, 0.25, 0.75, fmt=_text.pct), "50% [25%, 75%]")

    def test_side_and_run_names_fall_back_the_way_each_section_did(self):
        report = {"a": {"agent": {"name": "atlas"}}, "b": {}}
        self.assertEqual(_text.side_name(report, "a"), "atlas")
        self.assertEqual(_text.side_name(report, "b"), "b")
        self.assertEqual(_text.side_name(report, "b", default="B"), "B")
        self.assertEqual(_text.side_name(None, "a"), "a")
        self.assertEqual(_text.run_name(report["a"]), "atlas")
        self.assertEqual(_text.run_name({}), "the run")
        traj = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        self.assertEqual(_text.run_name(traj), traj.agent.name)


class StatsTest(unittest.TestCase):
    def test_the_moved_statistics_keep_their_values(self):
        self.assertEqual(_stats.percentile([], 0.9), 0.0)
        self.assertEqual(_stats.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(_stats.median([3, 1, 2]), 2)
        self.assertEqual(_stats.median([4, 1, 2, 3]), 2.5)
        self.assertEqual(_stats.mean([]), None)
        self.assertEqual(_stats.iqm([1, 2, 3]), 2.0)              # nothing cut at three runs
        self.assertEqual(_stats.iqm([0, 1, 2, 3, 4, 5, 6, 100]), 3.5)
        self.assertEqual(_stats.optimality_gap([1, 3], 3), 1.0)
        self.assertEqual(_stats.pvar([1, 3]), 1.0)
        self.assertEqual(_stats.mean_ci([]), (None, None))
        self.assertEqual(_stats.mean_ci([2.0]), (2.0, None))
        m, ci = _stats.mean_ci([1.0, 2.0, 3.0])
        self.assertEqual(m, 2.0)
        self.assertEqual(ci, [0.8684, 3.1316])
        self.assertEqual(_stats.percentile_interval([]), (None, None))
        self.assertEqual(_stats.percentile_interval(list(range(100))), (1, 97))

    def test_rng_is_one_stream_per_section_seed_and_label(self):
        a = _stats.rng(1, "x").random()
        self.assertEqual(a, _stats.rng(1, "x").random())
        self.assertNotEqual(a, _stats.rng(1, "y").random())
        self.assertNotEqual(a, _stats.rng(2, "x").random())
        self.assertNotEqual(a, _stats.rng(1, "x", section="other").random())
        # the seed string the stats section always used, so its bootstrap did not move
        import random
        self.assertEqual(_stats.rng(20260913, "improvement:return").random(),
                         random.Random("agentdiff.rlstats:20260913:improvement:return").random())

    def test_rounded_and_finite_read_a_number_the_way_the_sections_do(self):
        # the four-place rounding the evolve, comparison and grafana sections shared as a local `_r`
        self.assertEqual(_stats.rounded(1 / 3), 0.3333)
        self.assertEqual(_stats.rounded(2), 2.0)
        self.assertEqual(_stats.rounded(0.0000001, 6), 0.0)
        self.assertIsNone(_stats.rounded(None))
        self.assertTrue(_stats.finite(3) and _stats.finite(-0.5))
        self.assertFalse(_stats.finite(True))                    # a bool is not a measurement
        self.assertFalse(_stats.finite(float("nan")) or _stats.finite(float("inf")))
        self.assertFalse(_stats.finite(None) or _stats.finite("3"))

    def test_the_stratified_bootstrap_keeps_every_tasks_run_count(self):
        draws = _stats.stratified_resamples({"t2": [1, 2, 3], "t1": [10, 20]}, 5, _stats.rng(0, "test"))
        self.assertEqual(len(draws), 5)
        for d in draws:
            self.assertEqual(len(d), 5)
            self.assertTrue(all(v in (10, 20) for v in d[:2]) and all(v in (1, 2, 3) for v in d[2:]))


if __name__ == "__main__":
    unittest.main()
