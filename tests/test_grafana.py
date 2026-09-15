"""The Grafana export: the numbers leave the engine as Prometheus samples,
and nothing is invented on the way out.

What this pins: the exposition validates by the rules Prometheus' parser
enforces (and the validator catches each rule broken); every interval
travels as three gauges; every sample says whether it is synthetic; the
demo outputs give a fixed number of samples and a few known values; two
exports are the same bytes; every dashboard queries only metric names
the exporter emits, never a point without its interval; and the
provisioning, the scrape config and the compose file agree with each
other and with the dashboards.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare import grafana
from deepcompare.grafana import (FAMILIES, PREFIX, collect, export, format_value, render_csv, render_json,
                                 render_prom, validate_exposition)

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "demo" / "rl" / "train"
BATCH = ROOT / "demo" / "traces"
LINEAGE = ROOT / "demo" / "evolve" / "lineage"
TRACE = TRAIN / "rl01_ledger_reconcile__policy-v1__r1.json"
GRAFANA = ROOT / "grafana"
DASHBOARDS = sorted((GRAFANA / "dashboards").glob("*.json"))
UIDS = {"agentdiff-agents", "agentdiff-tools", "agentdiff-training", "agentdiff-evolution", "agentdiff-run", "agentdiff-evals",
        "agentdiff-budget"}
BUDGET_FAMILIES = ("budget_tokens", "budget_by_kind", "budget_by_tool", "budget_waste", "budget_cost_usd", "budget_cap",
                   "budget_over_cap", "fetches", "fetches_errors", "fetches_repeats", "fetches_used")
METRIC_IN_EXPR = re.compile(r"\bagentdiff_[a-z0-9_]+")

_CACHE: dict = {}


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "deepcompare", *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=900)


def _output(kind: str) -> Path:
    """The demo analysis, run once per test process and cached."""
    if kind not in _CACHE:
        base = Path(tempfile.mkdtemp(prefix=f"agentdiff-grafana-{kind}-"))
        if kind == "train":
            result = _cli("runs", str(TRAIN), "-o", str(base))
        elif kind == "batch":
            result = _cli("batch", str(BATCH), "-o", str(base))
        else:
            result = _cli("evolve", str(LINEAGE), "-o", str(base))
        assert result.returncode == 0, result.stderr[-2000:]
        _CACHE[kind] = base
    return _CACHE[kind]


def _samples(target) -> dict:
    """{(metric, sorted label pairs): value} for the target."""
    return {(m, tuple(sorted(l.items()))): v for m, l, v in collect(target)["samples"]}


def _find(samples: dict, metric: str, **labels) -> list:
    want = set(labels.items())
    return [v for (m, l), v in samples.items() if m == PREFIX + metric and want <= set(l)]


def _one(samples: dict, metric: str, **labels):
    found = _find(samples, metric, **labels)
    assert len(found) == 1, (metric, labels, found)
    return found[0]


def _with(samples: dict, family: str, labels: dict) -> list:
    """Like :func:`_find`, for families whose labels include one named
    ``metric`` (the eval's), which the keyword form cannot pass."""
    want = set(labels.items())
    return [v for (m, l), v in samples.items() if m == PREFIX + family and want <= set(l)]


# ---------------------------------------------------------------- the families

class FamilyTest(unittest.TestCase):
    def test_every_family_is_a_gauge_with_a_one_line_help(self):
        for name, (kind, help_text) in FAMILIES.items():
            self.assertRegex(PREFIX + name, r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
            self.assertEqual(kind, "gauge", name)
            self.assertTrue(help_text and "\n" not in help_text, name)

    def test_every_interval_travels_as_three_gauges(self):
        for name in FAMILIES:
            if name.endswith("_lo"):
                self.assertIn(name[:-3], FAMILIES, name)
                self.assertIn(name[:-3] + "_hi", FAMILIES, name)
                self.assertIn("interval", FAMILIES[name][1], name)

    def test_the_exporter_opens_no_socket(self):
        for path in (ROOT / "deepcompare" / "grafana.py", ROOT / "deepcompare" / "commands" / "grafana.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"urllib", "http", "socket", "ssl"}, path.name)


# ---------------------------------------------------------------- the format

class ExpositionFormatTest(unittest.TestCase):
    def test_values_format_as_prometheus_reads_them(self):
        self.assertEqual(format_value(8), "8")
        self.assertEqual(format_value(1.0), "1")
        self.assertEqual(format_value(0.875), "0.875")
        self.assertEqual(format_value(-4.2417), "-4.2417")
        self.assertEqual(format_value(True), "1")
        self.assertEqual(format_value(float("nan")), "NaN")
        self.assertEqual(format_value(float("inf")), "+Inf")

    def test_render_escapes_label_values_and_sorts_labels(self):
        text = render_prom([(PREFIX + "runs", {"task": 'say "hi"\\', "agent": "a"}, 3)])
        self.assertIn('agentdiff_runs{agent="a",task="say \\"hi\\"\\\\"} 3', text)
        self.assertTrue(text.startswith("# HELP agentdiff_runs "))
        self.assertIn("# TYPE agentdiff_runs gauge\n", text)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(validate_exposition(text), [])

    def test_the_validator_accepts_the_prometheus_examples(self):
        text = ('# HELP http_requests_total The total number of HTTP requests.\n'
                '# TYPE http_requests_total counter\n'
                'http_requests_total{method="post",code="200"} 1027\n'
                'http_requests_total{method="post",code="400"}    3\n'
                '# Escaping in label values:\n'
                'msdos_file_access_time_seconds{path="C:\\\\DIR\\\\FILE.TXT",error="Cannot find file:\\n\\"FILE.TXT\\""} 1.458255915e9\n'
                'metric_without_timestamp_and_labels 12.47\n'
                'something_weird{problem="division by zero"} +Inf\n')
        self.assertEqual(validate_exposition(text), [])

    def test_the_validator_catches_each_rule_broken(self):
        cases = {
            "metric name": '1bad_name 1\n',
            "label name": 'm{1x="a"} 1\n',
            "reserved label": 'm{__x="a"} 1\n',
            "TYPE after": 'm 1\n# TYPE m gauge\n',
            "second HELP": '# HELP m a\n# HELP m b\nm 1\n',
            "unknown type": '# TYPE m rate\nm 1\n',
            "duplicate": 'm{a="1"} 1\nm{a="1"} 2\n',
            "two groups": 'm 1\nn 1\nm 2\n',
            "timestamp": 'm 1 1700000000000\n',
            "not a float": 'm one\n',
            "no line feed": 'm 1',
        }
        for label, text in cases.items():
            with self.subTest(rule=label):
                self.assertNotEqual(validate_exposition(text), [], label)


# ---------------------------------------------------------------- the runs layout

class TrainExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _output("train")
        cls.collected = collect(cls.out)
        cls.samples = _samples(cls.out)
        cls.prom = render_prom(cls.collected["samples"])

    def test_the_exposition_validates(self):
        self.assertEqual(validate_exposition(self.prom), [])
        self.assertEqual(self.collected["kind"], "batch")
        self.assertEqual(self.collected["notes"], [])

    def test_sample_and_family_counts_are_pinned(self):
        self.assertEqual(len(self.collected["samples"]), 2357)
        self.assertEqual(len({m for m, _l, _v in self.collected["samples"]}), 72)

    def test_where_the_tokens_went_and_what_was_fetched_are_exported_as_counts(self):
        s = self.samples
        agg = json.loads((self.out / "aggregate.json").read_text(encoding="utf-8"))
        families = {m for m, _l, _v in self.collected["samples"]}
        for name in BUDGET_FAMILIES:
            if name in ("budget_cost_usd", "budget_cap", "budget_over_cap"):
                self.assertNotIn(PREFIX + name, families, f"{name}: the RL demo records no cost and was given no cap")
            else:
                self.assertIn(PREFIX + name, families, name)
            self.assertNotIn(name + "_lo", FAMILIES, "a count has no interval family")
            self.assertIn("no interval", FAMILIES[name][1] if name != "budget_cap" else "no interval", name)
        # per run, three bases; the runs demo measured every token
        self.assertEqual(len(_find(s, "budget_tokens")), 96 * 3)
        self.assertEqual(_one(s, "budget_tokens", agent="policy-v1", task="rl01_ledger_reconcile", run="r1", basis="measured"), 2701)
        self.assertEqual(_one(s, "budget_tokens", agent="policy-v1", task="rl01_ledger_reconcile", run="r1", basis="estimated"), 0)
        for name in ("policy-v1", "policy-v2"):
            self.assertEqual(sum(_find(s, "budget_tokens", agent=name, basis="measured")), agg["budget"]["agents"][name]["measured"])
            self.assertEqual({k: _one(s, "budget_by_kind", agent=name, kind=k) for k in agg["budget"]["agents"][name]["by_kind"]},
                             agg["budget"]["agents"][name]["by_kind"])
            self.assertEqual(_one(s, "budget_by_tool", agent=name, tool="read_file"), agg["budget"]["agents"][name]["by_tool"]["read_file"])
            self.assertEqual(sum(_find(s, "fetches", agent=name)), agg["fetches"]["agents"][name]["fetches"])
            self.assertEqual(sum(_find(s, "fetches_errors", agent=name)), agg["fetches"]["agents"][name]["errors"])
            self.assertEqual(sum(_find(s, "fetches_repeats", agent=name)), agg["fetches"]["agents"][name]["repeats"])
            self.assertEqual(sum(_find(s, "fetches_used", agent=name, use="used")), agg["fetches"]["agents"][name]["used"])
            self.assertEqual(sum(_find(s, "fetches_used", agent=name, use="unknown")), agg["fetches"]["agents"][name]["unknown_use"])
        self.assertEqual(_one(s, "budget_by_kind", agent="policy-v1", kind="read"), 71224)
        # the waste is read per run from the pair reports' sides: one representative pair per task
        self.assertEqual(len(_find(s, "budget_waste")), 6 * 2 * 3)
        self.assertEqual([_one(s, "budget_waste", agent="policy-v1", task="rl01_ledger_reconcile", run="r1", what=w)
                          for w in ("after_last_evidence", "in_errored_calls", "in_repeats")], [533, 37, 390])
        self.assertEqual(len(_find(s, "fetches", kind="search")), 96)
        self.assertEqual(_one(s, "fetches", agent="policy-v1", task="rl01_ledger_reconcile", run="r1", kind="read"), 15)
        for (metric, labels), _v in s.items():
            if any(metric == PREFIX + n for n in BUDGET_FAMILIES):
                self.assertEqual(dict(labels).get("synthetic"), "true", (metric, labels))

    def test_a_cap_and_the_runs_over_it_are_exported_when_the_analysis_was_given_one(self):
        runs = [{"agent": "a", "task": "t", "run": "r1", "measurable": True, "tokens": 900, "measured": 900, "estimated": 0,
                 "unknown": 0, "steps": 3, "cost_usd": 0.5, "seconds": 1.0, "synthetic": True},
                {"agent": "a", "task": "t", "run": "r2", "measurable": True, "tokens": 100, "measured": 0, "estimated": 100,
                 "unknown": 0, "steps": 3, "cost_usd": None, "seconds": 1.0, "synthetic": True}]
        budget = {"version": 1, "measurable": True, "reason": None,
                  "agents": {"a": {"by_kind": {"plan": 1000}, "by_tool": {}, "synthetic": True}}, "tasks": {},
                  "heaviest_runs": [], "cap": {"value": 500, "source": "--token-cap", "over": [{"agent": "a", "task": "t", "run": "r1", "tokens": 900}]},
                  "runs": runs}
        c = grafana.collect_batch({"aggregate": {"budget": budget, "fetches": {"measurable": False, "reason": "none", "runs": []}}, "reports": []})
        s = {(m, tuple(sorted(l.items()))): v for m, l, v in c.samples}
        self.assertEqual(_one(s, "budget_cap", source="--token-cap"), 500)
        self.assertEqual(_one(s, "budget_over_cap", agent="a", task="t", run="r1", synthetic="true"), 900)
        self.assertEqual(_find(s, "budget_cost_usd"), [0.5], "a run without a recorded cost is no sample")
        self.assertEqual(_one(s, "budget_tokens", run="r2", basis="estimated"), 100)
        self.assertIn("no fetches reading in the aggregate or the reports: fetch families omitted", c.notes)

    def test_every_sample_says_it_is_synthetic(self):
        for (metric, labels), _value in self.samples.items():
            keys = dict(labels)
            if metric.endswith("runs_floor"):
                continue  # a constant of the engine, not of any trace
            if "agent" in keys or "from" in keys or "generation" in keys:
                self.assertEqual(keys.get("synthetic"), "true", (metric, labels))

    def test_pass_rate_carries_its_wilson_interval(self):
        s = self.samples
        self.assertEqual(_one(s, "runs", agent="policy-v2", task="rl01_ledger_reconcile"), 8)
        self.assertEqual(_one(s, "passes", agent="policy-v2", task="rl01_ledger_reconcile"), 7)
        self.assertEqual(_one(s, "pass_rate", agent="policy-v2", task="rl01_ledger_reconcile"), 0.875)
        self.assertEqual(_one(s, "pass_rate_lo", agent="policy-v2", task="rl01_ledger_reconcile"), 0.5291)
        self.assertLess(_one(s, "pass_rate_lo", agent="policy-v2", task="rl05_incident_postmortem"), 0.01)
        self.assertEqual(_one(s, "agent_pass_rate", agent="policy-v1"), 0.2083)
        self.assertEqual(len(_find(s, "pass_rate")), 12)

    def test_the_training_ground_numbers_match_the_aggregate(self):
        s = self.samples
        agg = json.loads((self.out / "aggregate.json").read_text(encoding="utf-8"))
        imp = agg["rl"]["stats"]["improvement"]
        self.assertEqual(_one(s, "improvement", **{"from": "policy-v1", "to": "policy-v2"}), imp["point"])
        self.assertEqual(_one(s, "improvement_lo", **{"from": "policy-v1"}), imp["lo"])
        self.assertEqual(_one(s, "improvement_hi", **{"from": "policy-v1"}), imp["hi"])
        self.assertEqual(_one(s, "task_improvement", task="rl05_incident_postmortem"), 0.4062)
        self.assertEqual(_one(s, "iqm", agent="policy-v1"), agg["rl"]["stats"]["aggregates"]["policy-v1"]["iqm"]["point"])
        self.assertEqual(_one(s, "return_mean", agent="policy-v2"), agg["rl"]["agents"]["policy-v2"]["mean_return"])
        self.assertEqual(_one(s, "return_mean_lo", agent="policy-v2"), agg["rl"]["agents"]["policy-v2"]["return_ci"][0])
        self.assertEqual(_one(s, "task_return_delta", task="rl05_incident_postmortem"), -4.325)
        self.assertEqual(_one(s, "critic_explained_variance", agent="_all"),
                         agg["rl"]["audit"]["critic"]["overall"]["explained_variance"])
        self.assertEqual(_one(s, "reward_disagreements", scope="shaping"), 23)
        self.assertEqual(_one(s, "paired_tasks"), 6)

    def test_the_runs_advisory_names_its_level(self):
        s = self.samples
        self.assertEqual(_one(s, "runs_per_task_min", level="structured-ok"), 8)
        self.assertEqual(_one(s, "runs_floor", kind="structured"), 8)

    def test_tools_are_summed_over_the_reports(self):
        s = self.samples
        self.assertEqual(_one(s, "tool_calls", agent="policy-v1", tool="read_file"), 96)
        self.assertGreaterEqual(_one(s, "tool_max_identical_run", agent="policy-v1", tool="read_file"), 2)
        self.assertEqual(len(_find(s, "tool_calls")), 8)

    def test_json_and_csv_carry_the_same_samples(self):
        payload = render_json(self.collected)
        self.assertEqual(len(payload["samples"]), 2357)
        self.assertEqual(sum(len(v) for v in payload["series"].values()), 2357)
        self.assertTrue(set(payload["families"]) <= {PREFIX + n for n in FAMILIES})
        self.assertEqual(set(payload["families"]), set(payload["series"]))
        rows = render_csv(self.collected["samples"]).splitlines()
        self.assertEqual(len(rows), 2358)
        self.assertTrue(rows[0].startswith("metric,value,agent,"))

    def test_two_exports_are_the_same_bytes(self):
        a, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        try:
            export(self.out, a)
            export(self.out, b)
            for name in ("metrics.prom", "metrics.json", "metrics.csv"):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes(), name)
            self.assertTrue((a / "metrics.prom").read_text(encoding="utf-8").startswith("# HELP agentdiff_runs "))
        finally:
            shutil.rmtree(a)
            shutil.rmtree(b)


# ---------------------------------------------------------------- the batch layout

class BatchExportTest(unittest.TestCase):
    def test_a_batch_without_rl_says_so_and_still_exports(self):
        collected = collect(_output("batch"))
        self.assertEqual(len(collected["samples"]), 722)
        self.assertEqual(len({m for m, _l, _v in collected["samples"]}), 51)
        self.assertIn("no rl section: return, IQM and improvement families omitted", collected["notes"])
        self.assertIn("no budget ledger in the aggregate: token families read from the pair reports' sides", collected["notes"])
        self.assertIn("no fetches ledger in the aggregate: fetch families read from the pair reports' sides", collected["notes"])
        self.assertEqual(validate_exposition(render_prom(collected["samples"])), [])
        s = _samples(_output("batch"))
        self.assertEqual(_one(s, "runs", agent="atlas-v2", task="t01_acme_revenue"), 1)
        self.assertEqual(_one(s, "runs_per_task_min", level="insufficient"), 1)
        # the batch demo records a cost on every run and measured every token
        self.assertEqual(len(_find(s, "budget_cost_usd")), 16)
        self.assertEqual(_one(s, "budget_cost_usd", agent="atlas-v2", task="t01_acme_revenue", run="r1"), 0.0051)
        # the batch demo's steps carry counts but no tokens_basis, so every token is counted under unknown, never measured
        self.assertEqual(_one(s, "budget_tokens", agent="atlas-v2", task="t01_acme_revenue", run="r1", basis="unknown"), 840)
        self.assertEqual(_one(s, "budget_tokens", agent="atlas-v2", task="t01_acme_revenue", run="r1", basis="measured"), 0)
        self.assertEqual(sum(_find(s, "fetches", agent="atlas-v2", task="t01_acme_revenue")), 3)
        self.assertEqual(dict(next(l for (m, l), _v in s.items() if m == PREFIX + "budget_tokens")).get("synthetic"), "false")


# ---------------------------------------------------------------- the lineage

class EvolutionExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _output("evo")
        cls.collected = collect(cls.out)
        cls.samples = _samples(cls.out)

    def test_the_exposition_validates_with_the_lineage_families(self):
        self.assertEqual(validate_exposition(render_prom(self.collected["samples"])), [])
        self.assertGreater(len(self.collected["samples"]), 1000)
        families = {m for m, _l, _v in self.collected["samples"]}
        for name in ("evolution_iqm", "evolution_iqm_lo", "evolution_iqm_hi", "evolution_pass_rate",
                     "evolution_prompt_chars", "evolution_rules", "evolution_memory", "evolution_budget",
                     "evolution_step_verdict", "evolution_step_verdict_code", "evolution_step_improvement_lo",
                     "evolution_protected_touched", "evolution_best", "evolution_recommended", "runs_per_task_min"):
            self.assertIn(PREFIX + name, families, name)

    def test_the_lineage_shape_is_the_demo_lineage(self):
        s = self.samples
        self.assertEqual(_one(s, "evolution_generations", family="ledger-agent"), 7)
        self.assertEqual(len(_find(s, "evolution_step_verdict")), 6)
        self.assertEqual(_one(s, "evolution_step_verdict", **{"from": "g2", "to": "g3"}), 1)
        self.assertEqual(_find(s, "evolution_step_verdict", **{"from": "g2", "to": "g3", "verdict": "gamed"}), [1])
        self.assertEqual(_one(s, "evolution_step_verdict_code", **{"from": "g2", "to": "g3"}), -4)
        self.assertEqual(_one(s, "evolution_generation_index", generation="g6"), 6)
        self.assertEqual(_one(s, "evolution_budget", what="memory"), 20)
        self.assertEqual(_one(s, "evolution_over_budget", generation="g5", what="memory"), 40)
        self.assertEqual(_one(s, "evolution_memory", generation="g5"), 40)
        touched = _find(s, "evolution_protected_touched", path="config.checks", direction="weakened")
        self.assertEqual(touched, [1])
        self.assertEqual(_one(s, "runs_per_task_min", level="below-floor"), 5)
        self.assertEqual(_find(s, "evolution_recommended", is_last="false"), _find(s, "evolution_recommended"))
        for (metric, labels), _v in s.items():
            if metric.startswith(PREFIX + "evolution_"):
                self.assertEqual(dict(labels).get("synthetic"), "true", metric)

    def test_the_eval_that_evolved_beside_the_lineage_is_exported(self):
        s = self.samples
        families = {m for m, _l, _v in self.collected["samples"]}
        for name in ("coevolution_eval_generations", "coevolution_metric", "coevolution_metric_lo", "coevolution_metric_hi",
                     "coevolution_metric_status", "coevolution_candidates", "coevolution_candidate",
                     "coevolution_step_flag_delta", "coevolution_step_flag_delta_lo", "coevolution_hindsight",
                     "coevolution_hindsight_lag", "coevolution_drift", "coevolution_min_adjusted_alpha",
                     "coevolution_closures", "coevolution_recommended_agree"):
            self.assertIn(PREFIX + name, families, name)
        # the demo's eval grew from e0 to e3, learning verification rate at the verifier step
        self.assertEqual(_one(s, "coevolution_eval_generations", family="ledger-agent"), 4)
        self.assertEqual(_with(s, "coevolution_metric_status", {"metric": "verified_rate", "status": "adopted", "probe": "axes"})[0], 1)
        self.assertEqual(_with(s, "coevolution_metric", {"metric": "verified_rate", "generation": "g2"})[0], 1.0)
        self.assertEqual(_with(s, "coevolution_metric", {"metric": "verified_rate", "generation": "g3"})[0], 0.0)
        adopted = _one(s, "coevolution_candidates", decision="adopted")
        rejected = _one(s, "coevolution_candidates", decision="rejected")
        self.assertEqual((adopted, rejected), (3, 19))
        self.assertEqual(len(_find(s, "coevolution_candidate")), adopted + rejected)
        self.assertEqual(_with(s, "coevolution_candidate", {"metric": "worst_task_pass", "decision": "rejected"}), [1, 1])
        self.assertEqual(_with(s, "coevolution_hindsight_lag", {"metric": "verified_rate"})[0], 0)
        self.assertEqual(_with(s, "coevolution_hindsight_lag", {"metric": "frugal_pass_rate"})[0], 3)
        self.assertEqual(_with(s, "coevolution_step_flag_delta", {"from": "g2", "to": "g3", "metric": "verified_rate"})[0], -1.0)
        self.assertEqual(_one(s, "coevolution_recommended_agree", base="g4", evolved="g4"), 1)
        for (metric, labels), _v in s.items():
            if metric.startswith(PREFIX + "coevolution_"):
                self.assertEqual(dict(labels).get("synthetic"), "true", metric)
        for metric in ("coevolution_metric", "coevolution_step_flag_delta"):
            self.assertEqual(len(_find(s, metric)), len(_find(s, metric + "_lo")))
            self.assertEqual(len(_find(s, metric)), len(_find(s, metric + "_hi")))

    def test_the_adopted_step_label_is_the_step_not_a_stringified_dict(self):
        # finding 7: adopted_step read str(m["adopted_at"]) — "{'step': 'g2→g3', 'index': 3, …}"
        s = self.samples
        rows = {dict(l)["metric"]: dict(l)["adopted_step"] for (m, l), _v in s.items() if m == PREFIX + "coevolution_metric_status"}
        self.assertEqual(rows["verified_rate"], "g2→g3")
        self.assertEqual(rows["clean_pass_rate"], "g3→g4")
        self.assertEqual(rows["frugal_pass_rate"], "g5→g6")
        self.assertEqual(rows["pass_rate"], "none", "a base metric was never adopted at a step")
        self.assertFalse(any("{" in v or "'" in v for v in rows.values()), rows)
        self.assertEqual(_with(s, "coevolution_metric_status", {"metric": "verified_rate", "adopted_step": "g2→g3", "confirmation": "confirmed"}), [1])


# ---------------------------------------------------------------- one trace

class TraceExportTest(unittest.TestCase):
    def test_one_run_exports_per_step_series(self):
        collected = collect(TRACE)
        self.assertEqual(collected["kind"], "trace")
        self.assertEqual(len(collected["samples"]), 188)
        self.assertEqual(len({m for m, _l, _v in collected["samples"]}), 13)
        self.assertEqual(validate_exposition(render_prom(collected["samples"])), [])
        s = _samples(TRACE)
        self.assertEqual(_one(s, "run_success", agent="policy-v1", task="rl01_ledger_reconcile", run="r1"), 0)
        self.assertEqual(_one(s, "run_steps", run="r1"), 43)
        self.assertEqual(_one(s, "run_return", run="r1"), -7.3)
        self.assertEqual(_one(s, "step_return_cum", step="42"), -7.3)
        self.assertEqual(len(_find(s, "step_reward")), 43)
        self.assertEqual(_one(s, "step_seconds", step="0", type="plan"), 1.4)
        self.assertEqual(len(_find(s, "step_value")), 8)  # the demo records a value on eight steps only

    def test_a_trace_without_rewards_or_harness_is_not_synthetic_and_has_no_reward(self):
        data = json.loads(TRACE.read_text(encoding="utf-8"))
        data.pop("harness", None)
        for step in data["steps"]:
            step.pop("reward", None)
            step.pop("value", None)
        base = Path(tempfile.mkdtemp())
        try:
            path = base / "t__plain-agent__r9.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            collected = collect(path)
            families = {m for m, _l, _v in collected["samples"]}
            self.assertNotIn(PREFIX + "step_reward", families)
            self.assertNotIn(PREFIX + "run_return", families)
            self.assertTrue(any("no step carries a recorded reward" in n for n in collected["notes"]))
            for _m, labels, _v in collected["samples"]:
                self.assertEqual(labels["synthetic"], "false")
        finally:
            shutil.rmtree(base)

    def test_a_pair_report_is_refused_with_directions(self):
        report = next((_output("train")).glob("report_*.json"))
        with self.assertRaises(ValueError) as ctx:
            collect(report)
        self.assertIn("output directory", str(ctx.exception))


# ---------------------------------------------------------------- the command

class CommandTest(unittest.TestCase):
    def test_help_and_a_trace_export_through_the_cli(self):
        self.assertEqual(_cli("grafana", "--help").returncode, 0)
        base = Path(tempfile.mkdtemp())
        try:
            result = _cli("grafana", str(TRACE), "-o", str(base))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("188 sample(s) in 13 metric families", result.stdout)
            for name in ("metrics.prom", "metrics.json", "metrics.csv"):
                self.assertTrue((base / name).is_file(), name)
        finally:
            shutil.rmtree(base)

    def test_a_missing_target_exits_2(self):
        result = _cli("grafana", str(ROOT / "nowhere"), "-o", tempfile.mkdtemp())
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)

    def test_the_command_module_exposes_register_and_run(self):
        from deepcompare.commands import grafana as command
        self.assertTrue(callable(command.register) and callable(command.run))


# ---------------------------------------------------------------- the dashboards

class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboards = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in DASHBOARDS}
        cls.families = {PREFIX + n for n in FAMILIES}

    def test_seven_dashboards_with_uid_title_and_schema_version(self):
        self.assertEqual(len(self.dashboards), 7)
        self.assertEqual({d["uid"] for d in self.dashboards.values()}, UIDS)
        for name, d in self.dashboards.items():
            self.assertTrue(d["title"].startswith("AgentDiff"), name)
            self.assertIsInstance(d["schemaVersion"], int)
            self.assertGreaterEqual(d["schemaVersion"], 39, name)
            self.assertEqual(d["__inputs"][0]["name"], "DS_PROMETHEUS", name)
            variables = {v["name"]: v for v in d["templating"]["list"]}
            self.assertEqual(variables["DS_PROMETHEUS"]["type"], "datasource", name)
            self.assertEqual(variables["DS_PROMETHEUS"]["query"], "prometheus", name)

    def test_panel_ids_are_unique_and_every_panel_asks_a_question(self):
        for name, d in self.dashboards.items():
            ids = [p["id"] for p in d["panels"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            self.assertGreater(len(ids), 4, name)
            for panel in d["panels"]:
                self.assertTrue(panel["title"].endswith("?"), (name, panel["title"]))
                self.assertTrue(panel.get("description"), (name, panel["title"]))
                self.assertEqual(panel["datasource"], {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}, name)
                self.assertTrue(panel["targets"], (name, panel["title"]))
                for key in ("x", "y", "w", "h"):
                    self.assertIn(key, panel["gridPos"], name)

    def test_every_query_names_only_metrics_the_exporter_emits(self):
        for name, d in self.dashboards.items():
            for panel in d["panels"]:
                for target in panel["targets"]:
                    self.assertEqual(target["datasource"]["uid"], "${DS_PROMETHEUS}", name)
                    names = METRIC_IN_EXPR.findall(target["expr"])
                    self.assertTrue(names, (name, panel["title"], target["expr"]))
                    for metric in names:
                        self.assertIn(metric, self.families, (name, panel["title"], metric))

    def test_no_panel_draws_a_point_without_its_interval(self):
        for name, d in self.dashboards.items():
            for panel in d["panels"]:
                metrics = {m for t in panel["targets"] for m in METRIC_IN_EXPR.findall(t["expr"])}
                for metric in metrics:
                    if metric + "_lo" in self.families:
                        self.assertIn(metric + "_lo", metrics, (name, panel["title"], metric))
                        self.assertIn(metric + "_hi", metrics, (name, panel["title"], metric))

    def test_the_advisory_stat_goes_amber_below_the_floor(self):
        for name in ("agents.json", "training.json", "evolution.json"):
            panels = [p for p in self.dashboards[name]["panels"] if "agentdiff_runs_per_task_min" in json.dumps(p)]
            self.assertEqual(len(panels), 1, name)
            steps = panels[0]["fieldConfig"]["defaults"]["thresholds"]["steps"]
            self.assertEqual(steps[0]["color"], "orange")
            self.assertEqual([s["value"] for s in steps[1:]], [8, 32])

    def test_the_budget_dashboard_draws_counts_and_says_so(self):
        d = self.dashboards["budget.json"]
        self.assertEqual(d["uid"], "agentdiff-budget")
        metrics = {m for p in d["panels"] for t in p["targets"] for m in METRIC_IN_EXPR.findall(t["expr"])}
        self.assertEqual(metrics, {PREFIX + n for n in BUDGET_FAMILIES})
        self.assertFalse(any(m.endswith(("_lo", "_hi")) for m in metrics), "counts have no interval to draw")
        for panel in d["panels"]:
            self.assertIn("count", panel["description"].lower(), panel["title"])
        self.assertTrue(any("wasted" in p["title"] for p in d["panels"]))
        self.assertTrue(any("used" in p["title"] for p in d["panels"]))

    def test_the_verdict_timeline_maps_every_verdict(self):
        panel = next(p for p in self.dashboards["evolution.json"]["panels"] if p["type"] == "state-timeline")
        mapped = panel["fieldConfig"]["defaults"]["mappings"][0]["options"]
        self.assertEqual({v["text"] for v in mapped.values()}, set(grafana.VERDICT_CODES))
        self.assertEqual({int(k) for k in mapped}, set(grafana.VERDICT_CODES.values()))


# ---------------------------------------------------------------- provisioning

def _yaml(text: str):
    """A reader for the block-style subset these files use: mappings,
    lists of scalars or mappings, scalars, comments.  Not a YAML parser;
    enough to check the files agree with each other."""
    lines = [l.rstrip() for l in text.splitlines()]
    lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    pos = 0

    def scalar(raw: str):
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            return raw[1:-1]
        if raw in ("true", "false"):
            return raw == "true"
        try:
            return int(raw)
        except ValueError:
            return raw

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip())

    def block(indent: int):
        nonlocal pos
        if lines[pos].lstrip().startswith("- "):
            items = []
            while pos < len(lines) and indent_of(lines[pos]) == indent and lines[pos].lstrip().startswith("- "):
                item = lines[pos].lstrip()[2:]
                key, sep, rest = item.partition(":")
                if sep and (rest.startswith(" ") or not rest) and not item.startswith(("\"", "'")) and " " not in key.strip():
                    lines[pos] = " " * (indent + 2) + item
                    items.append(block(indent + 2))
                else:
                    pos += 1
                    items.append(scalar(item))
            return items
        out = {}
        while pos < len(lines):
            line = lines[pos]
            ind = indent_of(line)
            if ind < indent or (ind == indent and line.lstrip().startswith("- ")):
                break
            if ind > indent:
                raise ValueError(f"unexpected indent at: {line!r}")
            key, sep, rest = line.strip().partition(":")
            if not sep or (rest and not rest.startswith(" ")):
                raise ValueError(f"not a key: value line: {line!r}")
            pos += 1
            if rest.strip():
                out[key] = scalar(rest)
            elif pos < len(lines) and (indent_of(lines[pos]) > indent or
                                       (indent_of(lines[pos]) == indent and lines[pos].lstrip().startswith("- "))):
                out[key] = block(indent_of(lines[pos]))
            else:
                out[key] = None
        return out

    return block(0)


class ProvisioningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = _yaml((GRAFANA / "docker-compose.yml").read_text(encoding="utf-8"))
        cls.prometheus = _yaml((GRAFANA / "prometheus.yml").read_text(encoding="utf-8"))
        cls.datasources = _yaml((GRAFANA / "provisioning" / "datasources" / "prometheus.yaml").read_text(encoding="utf-8"))
        cls.providers = _yaml((GRAFANA / "provisioning" / "dashboards" / "agentdiff.yaml").read_text(encoding="utf-8"))

    def test_the_compose_file_brings_up_the_three_services(self):
        services = self.compose["services"]
        self.assertEqual(set(services), {"node-exporter", "prometheus", "grafana"})
        self.assertIn("--collector.textfile.directory=/textfile", services["node-exporter"]["command"])
        self.assertIn("./out:/textfile:ro", services["node-exporter"]["volumes"])
        self.assertIn("./prometheus.yml:/etc/prometheus/prometheus.yml:ro", services["prometheus"]["volumes"])
        self.assertIn("./provisioning:/etc/grafana/provisioning:ro", services["grafana"]["volumes"])
        dashboards_mount = next(v for v in services["grafana"]["volumes"] if v.startswith("./dashboards:"))
        self.assertEqual(dashboards_mount.split(":")[1], self.providers["providers"][0]["options"]["path"])
        for service in services.values():
            self.assertRegex(service["image"], r":v?[0-9]", "every image is pinned to a version")

    def test_prometheus_scrapes_the_node_exporter(self):
        targets = self.prometheus["scrape_configs"][0]["static_configs"][0]["targets"]
        self.assertEqual(targets, ["node-exporter:9100"])
        self.assertEqual(self.compose["services"]["node-exporter"]["ports"], ["9100:9100"])

    def test_the_datasource_uid_is_what_the_dashboards_resolve_to(self):
        ds = self.datasources["datasources"][0]
        self.assertEqual(ds["type"], "prometheus")
        self.assertEqual(ds["url"], "http://prometheus:9090")
        self.assertTrue(ds["isDefault"])
        for path in DASHBOARDS:
            d = json.loads(path.read_text(encoding="utf-8"))
            variable = next(v for v in d["templating"]["list"] if v["name"] == "DS_PROMETHEUS")
            self.assertEqual(variable["current"]["value"], ds["uid"], path.name)

    def test_the_provider_reads_the_dashboards_folder_from_disk(self):
        provider = self.providers["providers"][0]
        self.assertEqual(provider["type"], "file")
        self.assertEqual(provider["folder"], "AgentDiff")
        self.assertEqual(self.providers["apiVersion"], 1)


if __name__ == "__main__":
    unittest.main()
