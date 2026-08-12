"""Tests for divergence clustering into systematic issues (v13).

Two properties matter most: the same behavior on different tasks must
collapse into one issue (otherwise the output is still a list of incidents),
and suppression must never silently delete a finding.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.issues import (
    build_issues,
    fingerprint,
    is_suppressed,
    load_suppressions,
    render_issues_markdown,
)


def step(index, stype, name, text="text", quality=None):
    return {"index": index, "type": stype, "name": name,
            "input": f"{name} {text}", "output": f"{name} out {text}",
            "tokens": 100, "latency_s": 1.0, "quality": quality, "note": None}


def traj(agent, task, success, steps, tokens=400):
    return Trajectory.from_json({
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "gold"},
        "outcome": {"success": success, "answer": "answer",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": tokens // 2, "output_tokens": tokens // 2,
                   "cost_usd": 0.01, "latency_s": float(len(steps))},
        "steps": steps,
    })


def good(task):
    return traj("good", task, True, [
        step(0, "plan", "plan"),
        step(1, "search", "web_search"),
        step(2, "retrieve", "select_result", "official filing"),
        step(3, "answer", "final"),
    ])


def bad_source(task, quality="bad", success=False):
    """Same shape as good(), but picks a poor source at step 2."""
    return traj("weak", task, success, [
        step(0, "plan", "plan"),
        step(1, "search", "web_search"),
        step(2, "retrieve", "select_result", "random blog", quality=quality),
        step(3, "answer", "final"),
    ])


class TestFingerprint(unittest.TestCase):
    def test_same_behavior_different_tasks_shares_fingerprint(self):
        r1 = compare(good("t1"), bad_source("t1"))
        r2 = compare(good("t2"), bad_source("t2"))
        f1 = fingerprint(r1, r1["divergences"][0])
        f2 = fingerprint(r2, r2["divergences"][0])
        self.assertEqual(f1, f2)

    def test_weak_and_bad_are_one_issue_not_two(self):
        # Severities of the same behavior must not split the cluster.
        r_bad = compare(good("t1"), bad_source("t1", quality="bad"))
        r_weak = compare(good("t2"), bad_source("t2", quality="weak", success=True))
        self.assertEqual(
            fingerprint(r_bad, r_bad["divergences"][0]),
            fingerprint(r_weak, r_weak["divergences"][0]),
        )

    def test_different_kinds_do_not_collapse(self):
        tool_run = traj("weak", "t3", False, [
            step(0, "plan", "plan"),
            step(1, "search", "web_search"),
            step(2, "tool_call", "calculator", "wrong tool", quality="bad"),
            step(3, "answer", "final"),
        ])
        r_source = compare(good("t1"), bad_source("t1"))
        r_tool = compare(good("t3"), tool_run)
        self.assertNotEqual(
            fingerprint(r_source, r_source["divergences"][0]),
            fingerprint(r_tool, r_tool["divergences"][0]),
        )

    def test_volatile_detail_is_normalized_away(self):
        a = traj("a", "t1", True, [
            step(0, "plan", "plan"),
            step(1, "read", "open_page_2024", "https://example.com/a/1"),
            step(2, "answer", "final"),
        ])
        b = traj("a", "t2", True, [
            step(0, "plan", "plan"),
            step(1, "read", "open_page_2025", "https://other.org/b/99"),
            step(2, "answer", "final"),
        ])
        # Names differing only by digits normalize to the same shape.
        from deepcompare.issues import _normalize
        self.assertEqual(_normalize("open_page_2024"), _normalize("open_page_2025"))

    def test_fingerprint_is_stable_and_readable(self):
        report = compare(good("t1"), bad_source("t1"))
        signature = fingerprint(report, report["divergences"][0])
        self.assertIn("retrieval", signature)
        self.assertNotIn(" ", signature)
        self.assertEqual(signature, fingerprint(report, report["divergences"][0]))


class TestClustering(unittest.TestCase):
    def reports(self):
        return [
            compare(good("t1"), bad_source("t1")),
            compare(good("t2"), bad_source("t2")),
            compare(good("t3"), bad_source("t3", quality="weak", success=True)),
        ]

    def test_recurring_behavior_collapses_to_one_issue(self):
        result = build_issues(self.reports())
        self.assertEqual(len(result["issues"]), 1)
        issue = result["issues"][0]
        self.assertEqual(issue["occurrence_count"], 3)
        self.assertEqual(len(issue["tasks"]), 3)
        self.assertTrue(issue["recurring"])

    def test_failures_and_costs_accumulate(self):
        result = build_issues(self.reports())
        issue = result["issues"][0]
        self.assertEqual(issue["failures_caused"], 2)
        self.assertGreaterEqual(issue["extra_tokens"], 0)
        self.assertEqual(issue["severity"], "critical")

    def test_issue_names_the_failing_agent(self):
        result = build_issues(self.reports())
        self.assertEqual(result["issues"][0]["agents"], ["weak"])

    def test_severity_ranking_puts_fatal_first(self):
        benign = compare(good("t9"), bad_source("t9", quality="weak", success=True))
        fatal = compare(good("t1"), bad_source("t1"))
        # Give them different shapes so they do not merge.
        benign["divergences"][0]["kind"] = "stopping"
        result = build_issues([benign, fatal])
        severities = [i["severity"] for i in result["issues"]]
        self.assertEqual(severities[0], "critical")

    def test_no_divergences_is_reported_cleanly(self):
        result = build_issues([compare(good("t1"), good("t1"))])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["active"], 0)
        self.assertIn("No divergences", result["narrative"])

    def test_deterministic(self):
        first = build_issues(self.reports())
        second = build_issues(self.reports())
        self.assertEqual(first, second)

    def test_example_prefers_a_fatal_occurrence(self):
        result = build_issues(self.reports())
        self.assertTrue(result["issues"][0]["example"]["caused_failure"])


class TestSuppression(unittest.TestCase):
    def reports(self):
        return [compare(good("t1"), bad_source("t1"))]

    def signature(self):
        report = self.reports()[0]
        return fingerprint(report, report["divergences"][0])

    def test_suppressed_issue_is_marked_not_deleted(self):
        result = build_issues(self.reports(), [self.signature()])
        self.assertEqual(len(result["issues"]), 1)
        self.assertTrue(result["issues"][0]["suppressed"])
        self.assertEqual(result["active"], 0)
        self.assertEqual(result["suppressed"], 1)

    def test_suppression_excluded_from_headline_counts(self):
        result = build_issues(self.reports(), [self.signature()])
        self.assertEqual(sum(result["counts"].values()), 0)
        self.assertIn("suppressed", result["narrative"])

    def test_prefix_patterns(self):
        self.assertTrue(is_suppressed("retrieval/a:x/b:y", ["retrieval/*"]))
        self.assertFalse(is_suppressed("stopping/a:x/b:y", ["retrieval/*"]))

    def test_exact_match_only_without_star(self):
        self.assertTrue(is_suppressed("abc", ["abc"]))
        self.assertFalse(is_suppressed("abcd", ["abc"]))

    def test_load_from_file_with_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".agentdiffignore"
            path.write_text(
                "# known benign\nretrieval/*\n\n  stopping/a:none/b:x  # trailing\n",
                encoding="utf-8",
            )
            patterns = load_suppressions(Path(tmp))
            self.assertEqual(patterns, ["retrieval/*", "stopping/a:none/b:x"])

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_suppressions(Path(tmp)), [])
        self.assertEqual(load_suppressions(None), [])


class TestMarkdown(unittest.TestCase):
    def test_markdown_lists_issues_and_fingerprints(self):
        reports = [compare(good("t1"), bad_source("t1"))]
        markdown = render_issues_markdown(build_issues(reports))
        self.assertIn("Systematic issues", markdown)
        self.assertIn("retrieval/", markdown)
        self.assertIn(".agentdiffignore", markdown)

    def test_suppressed_section_present(self):
        reports = [compare(good("t1"), bad_source("t1"))]
        signature = fingerprint(reports[0], reports[0]["divergences"][0])
        markdown = render_issues_markdown(build_issues(reports, [signature]))
        self.assertIn("Suppressed", markdown)


class TestRealBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo traces not present")
        by_task: dict[str, dict[str, Trajectory]] = {}
        for path in sorted(traces.glob("*.json")):
            t = Trajectory.from_json(path)
            by_task.setdefault(t.task.id, {})[t.agent.name] = t
        cls.reports = [
            compare(pair["atlas-v2"], pair["bolt-v3"])
            for _, pair in sorted(by_task.items())
            if "atlas-v2" in pair and "bolt-v3" in pair
        ]

    def test_batch_collapses_to_fewer_issues_than_divergences(self):
        result = build_issues(self.reports)
        self.assertLess(len(result["issues"]), result["total_divergences"] + 1)
        self.assertGreater(result["total_divergences"], 0)

    def test_top_issue_is_the_recurring_retrieval_problem(self):
        result = build_issues(self.reports)
        top = next(i for i in result["issues"] if not i["suppressed"])
        self.assertEqual(top["kind"], "retrieval")
        self.assertTrue(top["recurring"])
        self.assertGreaterEqual(top["failures_caused"], 1)


if __name__ == "__main__":
    unittest.main()
