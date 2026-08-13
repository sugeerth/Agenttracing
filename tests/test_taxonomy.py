"""Tests for the MAST / TRAIL mapping (deepcompare.taxonomy).

The module's whole value is that its empty cells are trustworthy, so most of
what is pinned here is negative: that a mapping never points at a code neither
paper defines, that an unreachable mode is *reported* as unreachable rather
than quietly missing from the table, that coverage arithmetic is derived from
the same mapping a reader would check by hand, and that the two "neither
taxonomy is complete" caveats survive into every output a caller might quote.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare import taxonomy as tx
from deepcompare.divergence import _KIND_PRIORITY
from deepcompare.process import _PATHOLOGIES

DEMO = Path(__file__).resolve().parent.parent / "demo" / "telemetry" / "traces"


def step(index, stype, name, text="text", quality=None, output=None):
    return {
        "index": index, "type": stype, "name": name,
        "input": f"{name}({text})", "output": output if output is not None else f"{name} out {text}",
        "tokens": 100, "latency_s": 1.0, "quality": quality, "note": None,
    }


def traj(agent, steps, success=True, answer="answer", tools=None, budget=None):
    data = {
        "schema_version": 1, "trace_id": f"{agent}-t",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": "t_demo", "prompt": "find the revenue figure", "expected": "gold"},
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 200, "output_tokens": 200,
                   "cost_usd": 0.01, "latency_s": 4.0},
        "steps": steps,
    }
    if tools is not None:
        data["tools"] = tools
    if budget is not None:
        data["budget"] = budget
    return Trajectory.from_json(data)


def demo_reports():
    """Real comparison reports over the shipped telemetry demo traces."""
    pairs: dict[str, dict[str, Trajectory]] = {}
    for path in sorted(DEMO.glob("*.json")):
        task, _, agent = path.stem.partition("__")
        pairs.setdefault(task, {})[agent] = Trajectory.from_json(path)
    reports = []
    for task in sorted(pairs):
        sides = sorted(pairs[task])
        if len(sides) < 2:
            continue
        reports.append(compare(pairs[task][sides[0]], pairs[task][sides[1]]))
    return reports


# ---------------------------------------------------------------- taxonomies


class TaxonomyDataTest(unittest.TestCase):
    def test_mast_has_fourteen_unique_modes_in_three_categories(self):
        self.assertEqual(len(tx.MAST_MODES), 14)
        codes = [mode.code for mode in tx.MAST_MODES]
        self.assertEqual(len(set(codes)), 14)
        self.assertEqual(
            sorted({mode.category for mode in tx.MAST_MODES}),
            ["Inter-Agent Misalignment", "Specification & System Design",
             "Task Verification & Termination"],
        )
        self.assertEqual(sorted(tx.MAST_BY_CODE), sorted(codes))

    def test_trail_has_twenty_unique_leaves_split_eight_eight_four(self):
        self.assertEqual(len(tx.TRAIL_LEAVES), 20)
        self.assertEqual(len({leaf.code for leaf in tx.TRAIL_LEAVES}), 20)
        per_branch = {}
        for leaf in tx.TRAIL_LEAVES:
            per_branch[leaf.branch] = per_branch.get(leaf.branch, 0) + 1
        self.assertEqual(per_branch, {
            "Reasoning Errors": 8,
            "System Execution Errors": 8,
            "Planning and Coordination Errors": 4,
        })

    def test_system_execution_is_eight_of_twenty_leaves(self):
        """Disagreement #1: the block of TRAIL that MAST has no home for."""
        system = [leaf for leaf in tx.TRAIL_LEAVES
                  if leaf.branch == "System Execution Errors"]
        self.assertEqual(len(system), 8)
        self.assertEqual(len(tx.TRAIL_LEAVES), 20)

    def test_provenance_carries_arxiv_ids_and_agreement_figures(self):
        mast, trail = tx.PROVENANCE["MAST"], tx.PROVENANCE["TRAIL"]
        self.assertEqual(mast["arxiv"], "2503.13657")
        self.assertEqual(trail["arxiv"], "2505.08638")
        self.assertIn("0.88", mast["method"])
        self.assertIn("94%", mast["method"])
        self.assertIn("0.77", mast["method"])
        self.assertAlmostEqual(sum(mast["category_mass"].values()), 1.0, places=3)
        self.assertAlmostEqual(
            mast["category_mass"]["Task Verification & Termination"], 0.213)

    def test_signal_vocabularies_track_their_source_modules(self):
        """If divergence.py or process.py grows a label, this table must too."""
        kinds = {kind for kind, _ in _KIND_PRIORITY} | {"tool_execution", "stopping"}
        self.assertEqual(set(tx.DIVERGENCE_KINDS), kinds)
        self.assertEqual(set(tx.PROCESS_FLAGS), {key for key, _ in _PATHOLOGIES})


# ------------------------------------------------------------------- mapping


class MappingTest(unittest.TestCase):
    def test_every_entry_references_a_real_code(self):
        for mapping in tx.MAPPINGS:
            with self.subTest(signal=mapping.signal, code=mapping.code):
                self.assertIn(mapping.taxonomy, ("MAST", "TRAIL"))
                table = tx.MAST_BY_CODE if mapping.taxonomy == "MAST" else tx.TRAIL_BY_CODE
                self.assertIn(mapping.code, table)

    def test_every_entry_is_well_formed(self):
        for mapping in tx.MAPPINGS:
            with self.subTest(signal=mapping.signal, code=mapping.code):
                self.assertIn(mapping.confidence, tx.CONFIDENCES)
                self.assertIn(mapping.source, ("divergence", "process_flag"))
                self.assertIn(mapping.signal,
                              tx.DIVERGENCE_KINDS + tx.PROCESS_FLAGS)
                self.assertGreater(len(mapping.why), 40, "justification too thin")

    def test_signal_source_matches_its_vocabulary(self):
        for mapping in tx.MAPPINGS:
            expected = ("divergence" if mapping.signal in tx.DIVERGENCE_KINDS
                        else "process_flag")
            self.assertEqual(mapping.source, expected, mapping.signal)

    def test_every_agentdiff_signal_appears_in_the_table(self):
        mapped = {mapping.signal for mapping in tx.MAPPINGS}
        for signal in tx.DIVERGENCE_KINDS + tx.PROCESS_FLAGS:
            self.assertIn(signal, mapped, f"{signal} has no row at all")

    def test_no_duplicate_signal_code_pairs(self):
        seen = set()
        for mapping in tx.MAPPINGS:
            key = (mapping.signal, mapping.taxonomy, mapping.code)
            self.assertNotIn(key, seen, f"duplicate row {key}")
            seen.add(key)

    def test_refusals_exist_and_carry_reasons(self):
        """`none` with a reason is the expected result, not a gap to fill."""
        refusals = [m for m in tx.MAPPINGS if m.confidence == "none"]
        self.assertGreaterEqual(len(refusals), 4)
        for mapping in refusals:
            self.assertGreater(len(mapping.why), 60)

    def test_swallowed_error_is_refused_for_mast(self):
        """MAST has no system-execution bucket; that must be an empty cell."""
        mast = [m for m in tx.mappings_for("swallowed_error") if m.taxonomy == "MAST"]
        self.assertTrue(mast)
        self.assertTrue(all(m.confidence == "none" for m in mast))
        self.assertIn("system-execution", " ".join(m.why for m in mast))

    def test_blind_write_is_refused_for_trail(self):
        """TRAIL has no verification branch; that must be an empty cell too."""
        trail = [m for m in tx.mappings_for("blind_write") if m.taxonomy == "TRAIL"]
        self.assertTrue(trail)
        self.assertTrue(all(m.confidence == "none" for m in trail))
        self.assertIn("verification", " ".join(m.why for m in trail))

    def test_no_mapping_reaches_mast_category_two(self):
        """Inter-agent modes are unreachable from a single-agent trace."""
        for mapping in tx.MAPPINGS:
            if mapping.taxonomy == "MAST" and mapping.code.startswith("2."):
                self.assertEqual(mapping.confidence, "none", mapping.signal)

    def test_no_mapping_claims_a_judge_only_mode(self):
        judge_only = {"2.3", "2.6", "3.2"}
        claimed = {m.code for m in tx.MAPPINGS
                   if m.taxonomy == "MAST" and m.confidence in ("direct", "partial")}
        self.assertEqual(claimed & judge_only, set())

    def test_mapping_table_is_stable_and_filterable(self):
        first = tx.mapping_table()
        self.assertEqual(first, tx.mapping_table())
        self.assertEqual(len(first), len(tx.MAPPINGS))
        divergence = tx.mapping_table("divergence")
        flags = tx.mapping_table("process_flag")
        self.assertEqual(len(divergence) + len(flags), len(first))
        self.assertTrue(all(row["name"] for row in first))


# ------------------------------------------------------------------ coverage


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.coverage = tx.coverage()

    def test_reach_matches_the_mapping_table(self):
        """Coverage must be derivable by hand from MAPPINGS, not asserted."""
        for taxonomy, key, universe in (
            ("MAST", "mast", [m.code for m in tx.MAST_MODES]),
            ("TRAIL", "trail", [leaf.code for leaf in tx.TRAIL_LEAVES]),
        ):
            expected = sorted({
                m.code for m in tx.MAPPINGS
                if m.taxonomy == taxonomy and m.confidence in ("direct", "partial")
            })
            block = self.coverage[key]
            self.assertEqual(block["reachable_codes"], expected)
            self.assertEqual(block["reachable"], len(expected))
            self.assertEqual(block["total"], len(universe))
            self.assertAlmostEqual(
                block["fraction"], round(len(expected) / len(universe), 4))

    def test_refused_codes_do_not_count_as_reach(self):
        refused_only = set()
        for mapping in tx.MAPPINGS:
            if mapping.confidence != "none":
                continue
            claimed = any(
                other.taxonomy == mapping.taxonomy and other.code == mapping.code
                and other.confidence in ("direct", "partial")
                for other in tx.MAPPINGS
            )
            if not claimed:
                refused_only.add((mapping.taxonomy, mapping.code))
        self.assertTrue(refused_only)
        for taxonomy, code in refused_only:
            key = "mast" if taxonomy == "MAST" else "trail"
            self.assertNotIn(code, self.coverage[key]["reachable_codes"])

    def test_headline_numbers(self):
        self.assertEqual(self.coverage["mast"]["reachable"], 5)
        self.assertEqual(self.coverage["mast"]["total"], 14)
        self.assertEqual(self.coverage["trail"]["reachable"], 13)
        self.assertEqual(self.coverage["trail"]["total"], 20)

    def test_every_unreached_mode_is_reported_as_unreachable_with_a_reason(self):
        """Silence is the failure mode: absent != unreachable."""
        mast_unreachable = {row["code"] for row in self.coverage["mast"]["unreachable"]}
        self.assertEqual(
            mast_unreachable,
            {m.code for m in tx.MAST_MODES} - set(self.coverage["mast"]["reachable_codes"]),
        )
        trail_unreachable = {row["code"] for row in self.coverage["trail"]["unreachable"]}
        self.assertEqual(
            trail_unreachable,
            {leaf.code for leaf in tx.TRAIL_LEAVES}
            - set(self.coverage["trail"]["reachable_codes"]),
        )
        for row in (self.coverage["mast"]["unreachable"]
                    + self.coverage["trail"]["unreachable"]):
            with self.subTest(code=row["code"]):
                self.assertIn(row["blocker"], tx.BLOCKERS)
                self.assertGreater(len(row["reason"]), 40)
                self.assertTrue(row["name"])

    def test_all_of_mast_category_two_is_unreachable(self):
        inter = self.coverage["mast"]["by_category"]["Inter-Agent Misalignment"]
        self.assertEqual(inter["reachable"], 0)
        self.assertEqual(inter["total"], 6)
        self.assertEqual(inter["fraction"], 0.0)
        self.assertAlmostEqual(inter["published_mass"], 0.369)
        reasons = {row["code"]: row["blocker"]
                   for row in self.coverage["mast"]["unreachable"]}
        for code in ("2.1", "2.2", "2.4", "2.5"):
            self.assertEqual(reasons[code], "multi_agent")
        for code in ("2.3", "2.6"):
            self.assertEqual(reasons[code], "judge")

    def test_weak_verification_is_unreachable_because_it_needs_a_judge(self):
        reasons = {row["code"]: row for row in self.coverage["mast"]["unreachable"]}
        self.assertIn("3.2", reasons)
        self.assertEqual(reasons["3.2"]["blocker"], "judge")

    def test_trail_system_execution_is_mostly_out_of_reach(self):
        system = self.coverage["trail"]["by_branch"]["System Execution Errors"]
        self.assertEqual(system["total"], 8)
        self.assertEqual(system["reachable"], 3)
        blockers = {row["code"]: row["blocker"]
                    for row in self.coverage["trail"]["unreachable"]}
        for code in ("system_execution.api_issues.rate_limiting",
                     "system_execution.api_issues.authentication_errors",
                     "system_execution.api_issues.resource_not_found",
                     "system_execution.resource_management.timeout_issues"):
            self.assertEqual(blockers[code], "label_vocabulary")

    def test_per_category_totals_sum_to_the_taxonomy(self):
        self.assertEqual(
            sum(b["total"] for b in self.coverage["mast"]["by_category"].values()), 14)
        self.assertEqual(
            sum(b["reachable"] for b in self.coverage["mast"]["by_category"].values()),
            self.coverage["mast"]["reachable"])
        self.assertEqual(
            sum(b["total"] for b in self.coverage["trail"]["by_branch"].values()), 20)
        self.assertEqual(
            sum(b["reachable"] for b in self.coverage["trail"]["by_branch"].values()),
            self.coverage["trail"]["reachable"])

    def test_mass_weighted_reach_is_lower_than_mode_count_reach(self):
        """The modes AgentDiff misses are not the rare ones; say so numerically."""
        mass = self.coverage["mast"]["category_mass_weighted_reach"]
        expected = round(sum(
            bucket["published_mass"] * bucket["fraction"]
            for bucket in self.coverage["mast"]["by_category"].values()
        ), 4)
        self.assertAlmostEqual(mass, expected)
        self.assertLess(mass, 0.5)

    def test_coverage_narrative_states_both_blind_spots(self):
        text = self.coverage["narrative"]
        self.assertIn("Neither taxonomy is a superset", text)
        self.assertIn("21.3%", text)
        self.assertIn("8 of its 20 leaves", text)
        self.assertIn("the method is not", text)

    def test_coverage_is_deterministic(self):
        self.assertEqual(json.dumps(tx.coverage(), sort_keys=True),
                         json.dumps(tx.coverage(), sort_keys=True))


# -------------------------------------------------------------------- caveats


class CaveatTest(unittest.TestCase):
    def test_incompleteness_caveat_names_both_gaps(self):
        text = tx.INCOMPLETENESS_CAVEAT
        self.assertIn("MAST has no category for system-execution", text)
        self.assertIn("TRAIL has no verification category", text)
        self.assertIn("21.3%", text)

    def test_method_caveat_refuses_authority(self):
        text = tx.METHOD_CAVEAT
        self.assertIn("rule-based", text)
        self.assertIn("2503.13657", text)
        self.assertIn("2505.08638", text)
        self.assertIn("must not be reported as a MAST or TRAIL measurement", text)

    def test_single_agent_and_fault_caveats(self):
        self.assertIn("Inter-Agent Misalignment", tx.SINGLE_AGENT_CAVEAT)
        self.assertIn("36.9%", tx.SINGLE_AGENT_CAVEAT)
        self.assertIn("tau-bench", tx.FAULT_CAVEAT)
        self.assertIn("user simulator", tx.FAULT_CAVEAT)

    def test_caveats_ride_along_with_every_output(self):
        report = demo_reports()[0]
        for payload in (tx.classify(report), tx.coverage()):
            self.assertIn(tx.INCOMPLETENESS_CAVEAT, payload["caveats"])
            self.assertIn(tx.METHOD_CAVEAT, payload["caveats"])
            self.assertIn(tx.SINGLE_AGENT_CAVEAT, payload["caveats"])
            self.assertIn(tx.FAULT_CAVEAT, payload["caveats"])

    def test_module_docstring_carries_provenance(self):
        doc = tx.__doc__ or ""
        for token in ("2503.13657", "2505.08638", "0.88", "94%", "0.77",
                      "41.8%", "36.9%", "21.3%"):
            self.assertIn(token, doc)


# ---------------------------------------------------------------- classifying


class ClassifyRealReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = demo_reports()

    def test_demo_reports_exist(self):
        self.assertGreaterEqual(len(self.reports), 4)
        self.assertTrue(any(r["divergences"] for r in self.reports))

    def test_classification_of_a_real_report_is_sane(self):
        for report in self.reports:
            result = tx.classify(report)
            with self.subTest(task=result["task"]):
                self.assertEqual(result["task"], report["task"]["id"])
                self.assertEqual(set(result["agents"]), {"a", "b"})
                # every label points at a real code and carries its justification
                for label in result["mast"]["labels"]:
                    self.assertIn(label["code"], tx.MAST_BY_CODE)
                    self.assertIn(label["category"], result["mast"]["by_category"])
                    self.assertGreater(label["occurrences"], 0)
                    self.assertIn(label["confidence"], ("direct", "partial"))
                    self.assertTrue(label["why"])
                for label in result["trail"]["labels"]:
                    self.assertIn(label["code"], tx.TRAIL_BY_CODE)
                    self.assertIn(label["branch"], result["trail"]["by_branch"])
                    self.assertGreater(label["occurrences"], 0)
                    self.assertIn(label["confidence"], ("direct", "partial"))
                # counts cover the full universe, zeroes included
                self.assertEqual(set(result["mast"]["by_category"]),
                                 {m.category for m in tx.MAST_MODES})
                self.assertEqual(set(result["trail"]["by_branch"]),
                                 {leaf.branch for leaf in tx.TRAIL_LEAVES})
                self.assertEqual(set(result["mast"]["by_code"]), set(tx.MAST_BY_CODE))
                self.assertEqual(set(result["trail"]["by_code"]), set(tx.TRAIL_BY_CODE))
                self.assertTrue(all(v >= 0 for v in result["mast"]["by_code"].values()))
                # a single-agent tool can never put mass in MAST category 2
                self.assertEqual(
                    result["mast"]["by_category"]["Inter-Agent Misalignment"], 0)
                self.assertEqual(
                    result["mast"]["distinct_modes"],
                    len({label["code"] for label in result["mast"]["labels"]}))

    def test_counts_are_consistent_with_labels(self):
        result = tx.classify(self.reports[0])
        for key, block in (("category", result["mast"]), ("branch", result["trail"])):
            counts = block["by_category" if key == "category" else "by_branch"]
            recomputed = {name: 0 for name in counts}
            for label in block["labels"]:
                recomputed[label[key]] += label["occurrences"]
            self.assertEqual(counts, recomputed)

    def test_process_flags_are_recovered_from_the_report(self):
        joined = {
            flag
            for report in self.reports
            for side in ("a", "b")
            for flag in tx.classify(report)["signals"]["process_flags"][side]
        }
        self.assertTrue(joined, "no process flag recovered from any demo report")
        self.assertTrue(joined <= set(tx.PROCESS_FLAGS))

    def test_report_process_block_is_used_when_present(self):
        result = tx.classify(self.reports[0])
        self.assertEqual(result["signals"]["process_basis"],
                         "report's own process block")
        self.assertEqual(result["signals"]["process_unmeasurable"], [])

    def test_unmeasurable_flags_are_declared_not_assumed_clean(self):
        """Rebuilt from serialised steps, tools and budget are gone: say so.

        A report's ``a``/``b`` sides carry agent, outcome, totals and steps but
        not the offered tool table or the harness budget, so a consumer that
        recomputes flags from them cannot check four of the eleven. Reporting
        those as clean would be a false negative dressed as a pass.
        """
        stripped = dict(self.reports[0])
        stripped.pop("process", None)
        result = tx.classify(stripped)
        unmeasurable = result["signals"]["process_unmeasurable"]
        self.assertEqual(result["signals"]["process_basis"],
                         "recomputed from the report's serialised steps")
        self.assertIn("schema_violation", unmeasurable)
        self.assertIn("undeclared_tools", unmeasurable)
        self.assertIn("budget_pressure", unmeasurable)
        self.assertIn("false_success", unmeasurable)
        self.assertIn("unchecked rather than clean", result["narrative"])

    def test_classification_is_deterministic(self):
        report = self.reports[0]
        self.assertEqual(json.dumps(tx.classify(report), sort_keys=True),
                         json.dumps(tx.classify(report), sort_keys=True))

    def test_narrative_carries_the_incompleteness_and_method_caveats(self):
        for report in self.reports:
            text = tx.classify(report)["narrative"]
            self.assertIn("Neither taxonomy is a superset of the other", text)
            self.assertIn("rule-based mappings of deterministic trace signals", text)

    def test_batch_aggregation_sums_the_per_task_counts(self):
        batch = tx.classify_batch(self.reports)
        self.assertEqual(batch["tasks"], len(self.reports))
        for name, total in batch["mast"]["by_category"].items():
            self.assertEqual(
                total, sum(r["mast"]["by_category"][name] for r in batch["per_task"]))
        for name, total in batch["trail"]["by_branch"].items():
            self.assertEqual(
                total, sum(r["trail"]["by_branch"][name] for r in batch["per_task"]))
        self.assertEqual(batch["mast"]["by_category"]["Inter-Agent Misalignment"], 0)
        self.assertIn("coverage", batch)
        self.assertIn(tx.INCOMPLETENESS_CAVEAT, batch["caveats"])


class ClassifySyntheticTest(unittest.TestCase):
    """Signals the demo traces do not happen to produce, forced explicitly."""

    def _report(self, kinds, flags_a=(), flags_b=()):
        return {
            "task": {"id": "t_synth", "prompt": "p"},
            "a": {"agent": {"name": "alpha"}},
            "b": {"agent": {"name": "beta"}},
            "divergences": [
                {"rank": i + 1, "kind": kind, "summary": f"s{i}"}
                for i, kind in enumerate(kinds)
            ],
            "process": {
                "a": {"gap": {"raised": list(flags_a)}},
                "b": {"gap": {"raised": list(flags_b)}},
            },
        }

    def test_every_signal_produces_a_label_or_an_explicit_refusal(self):
        for signal in tx.DIVERGENCE_KINDS + tx.PROCESS_FLAGS:
            if signal in tx.DIVERGENCE_KINDS:
                report = self._report([signal])
            else:
                report = self._report([], flags_a=[signal])
            result = tx.classify(report)
            produced = (result["mast"]["labels"] + result["trail"]["labels"]
                        + result["declined"])
            with self.subTest(signal=signal):
                self.assertTrue(produced, f"{signal} produced nothing at all")

    def test_declined_rows_name_the_signal_and_the_refused_code(self):
        result = tx.classify(self._report(["reasoning", "planning"]))
        declined = {(row["signal"], row["taxonomy"], row["nearest_code"])
                    for row in result["declined"]}
        self.assertIn(("reasoning", "MAST", "2.6"), declined)
        self.assertIn(("planning", "MAST", "2.3"), declined)
        for row in result["declined"]:
            self.assertGreater(len(row["reason"]), 60)
        self.assertIn("declined", result["narrative"])

    def test_declined_codes_never_enter_the_counts(self):
        result = tx.classify(self._report(["reasoning"], flags_a=["swallowed_error"]))
        self.assertEqual(result["mast"]["by_code"]["2.6"], 0)
        self.assertEqual(result["mast"]["by_code"]["3.3"], 0)
        self.assertTrue(any(row["nearest_code"] == "3.3"
                            for row in result["declined"]))

    def test_repeated_signals_accumulate_occurrences_without_promotion(self):
        result = tx.classify(self._report(["retrieval", "retrieval", "retrieval"]))
        leaf = "reasoning.information_processing.poor_information_retrieval"
        self.assertEqual(result["trail"]["by_code"][leaf], 3)
        label = next(x for x in result["trail"]["labels"] if x["code"] == leaf)
        self.assertEqual(label["confidence"], "direct")
        mast = next(x for x in result["mast"]["labels"] if x["code"] == "1.1")
        self.assertEqual(mast["confidence"], "partial",
                         "volume must never upgrade a partial mapping")

    def test_loops_reach_mast_step_repetition_directly(self):
        result = tx.classify(self._report([], flags_a=["loop_block", "looped"]))
        label = next(x for x in result["mast"]["labels"] if x["code"] == "1.3")
        self.assertEqual(label["confidence"], "direct")
        self.assertEqual(result["mast"]["by_code"]["1.3"], 2)

    def test_empty_report_classifies_to_nothing_but_still_caveats(self):
        result = tx.classify({"task": {"id": "t", "prompt": "p"}, "divergences": []})
        self.assertEqual(result["mast"]["labels"], [])
        self.assertEqual(result["trail"]["labels"], [])
        self.assertEqual(sum(result["mast"]["by_code"].values()), 0)
        self.assertIn("no divergences", result["narrative"])
        self.assertIn(tx.INCOMPLETENESS_CAVEAT, result["caveats"])

    def test_unrecognised_report_shape_degrades_instead_of_raising(self):
        result = tx.classify({"divergences": [{"rank": 1, "kind": "retrieval"}]})
        self.assertIsNone(result["task"])
        self.assertFalse(result["signals"]["process_flags"]["a"])
        self.assertTrue(result["trail"]["labels"])


class ProcessFlagRecoveryTest(unittest.TestCase):
    def test_flags_recomputed_from_a_full_trajectory_pair(self):
        """With tools and a budget present, the tool-dependent flags can fire."""
        steps = [
            step(0, "tool_call", "get_page", "url=https://example.com/report"),
            step(1, "tool_call", "get_page", "url=https://example.com/report"),
            step(2, "answer", "answer", "done"),
        ]
        a = traj("alpha", steps, tools=[{"name": "get_page", "effect": "read"}],
                 budget={"max_steps": 3})
        b = traj("beta", [step(0, "tool_call", "get_page", "url=https://example.com/x"),
                          step(1, "answer", "answer", "done")],
                 tools=[{"name": "get_page", "effect": "read"}],
                 budget={"max_steps": 10})
        report = compare(a, b)
        flags = tx.process_flags(report)
        self.assertTrue(flags["available"])
        self.assertIn("repeated_calls", flags["raised"]["a"])
        self.assertIn("budget_pressure", flags["raised"]["a"])
        self.assertEqual(flags["unmeasurable"], [])
        result = tx.classify(report)
        self.assertGreaterEqual(result["mast"]["by_code"]["1.3"], 1)
        self.assertGreaterEqual(
            result["trail"]["by_code"][
                "system_execution.resource_management.resource_exhaustion"], 1)

    def test_report_supplied_process_block_is_preferred(self):
        report = {
            "task": {"id": "t", "prompt": "p"},
            "divergences": [],
            "process": {"a": {"gap": {"raised": ["looped"]}},
                        "b": {"gap": {"raised": []}}},
        }
        flags = tx.process_flags(report)
        self.assertEqual(flags["basis"], "report's own process block")
        self.assertEqual(flags["raised"], {"a": ["looped"], "b": []})


if __name__ == "__main__":
    unittest.main()
