"""The data side of a run: what the agent was told, what it read, and the
chain from that to its answer.

What this pins: the run reading on the demo (the prompt and expected
answer as recorded, the model as the trace declares it with the source
said, the corpus with its source identity and digests, the answer's
typed values traced to the fetched outputs that carry them, the chain
with its two stated overlaps); hand-built runs with no prompt, no model,
no text (unmeasurable with the reason, the readable parts still there),
instructions from ``agent.system_prompt`` and from ``agent.config``, a
step that names its model, repeated reads, an error; the pair section
(the instructions diff, the corpus diff, the models, the provenance
beside the pair's own readings); the aggregate; the lineage section on
the hand-built and the demo lineages; that every existing output is
byte-identical apart from the new keys; determinism; and that the
module names no model of its own.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import data as dm  # noqa: E402
from deepcompare import evolve as ev  # noqa: E402
from deepcompare import sections  # noqa: E402
from deepcompare.commands._io import load_traces  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.suite import analyse_runs  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402
from tests.test_budget import step, trace  # noqa: E402
from tests.test_evolve import SAMPLES, standard_gens, write_lineage  # noqa: E402

DEMO = ROOT / "demo" / "traces"
TRAIN = ROOT / "demo" / "rl" / "train"
LINEAGE = ROOT / "demo" / "evolve" / "lineage"


def _trace(steps, **kw):
    """A trace whose task has a real prompt and whose agent names a model."""
    d = {"schema_version": 1, "trace_id": "t-probe-r1", "run_id": "r1",
         "agent": dict({"name": "probe", "model": "sim-probe", "version": "1"}, **kw.pop("agent", {})),
         "task": dict({"id": "t", "prompt": "Find the total revenue of ACME for 2025.", "expected": "$4.82 billion"}, **kw.pop("task", {})),
         "outcome": dict({"success": True, "answer": "ACME made $4.82 billion in 2025.", "score": None, "termination": None}, **kw.pop("outcome", {})),
         "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}, "steps": steps}
    harness = kw.pop("harness", None)
    d.update(kw)
    t = Trajectory.from_dict(d)
    if harness:
        t.harness = harness  # the loaders keep the harness block beside the typed trajectory
    return t


PAGE = "ACME Corp reported total revenue of $4.82 billion for fiscal 2025, up 11% on the year, said ir.acmecorp.com."


@contextmanager
def _unregistered(scope: str, key: str):
    """The registry without one section, for the duration of the block."""
    saved = sections._REGISTRY[scope].pop(key)
    try:
        yield
    finally:
        sections._REGISTRY[scope][key] = saved


def _without(d: dict, *keys: str) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


# ---------------------------------------------------------------- one run

class RunTest(unittest.TestCase):
    def setUp(self):
        self.atlas = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        self.bolt = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")

    def test_the_demo_run_is_read_as_recorded(self):
        d = dm.data_run(self.atlas)
        self.assertTrue(d["measurable"])
        self.assertEqual(list(d)[:2], ["measurable", "reason"])
        self.assertEqual((d["task"]["id"], d["task"]["prompt_chars"], d["task"]["expected"], d["task"]["expected_chars"]),
                         ("t01_acme_revenue", 110, "$4.82 billion", 13))
        self.assertEqual(d["task"]["prompt"], self.atlas.task.prompt)
        a = d["agent"]
        self.assertEqual((a["name"], a["model"], a["version"], a["framework"]), ("atlas-v2", self.atlas.agent.model, "v2", None))
        # the demo records each agent's instructions (SYNTHETIC, invented for the persona)
        self.assertEqual(a["instructions"], {"system_prompt": self.atlas.agent.system_prompt,
                                             "source": "trace.agent.system_prompt", "chars": 309})
        self.assertEqual(a["tools_declared"], [])
        self.assertEqual([t["name"] for t in a["tools_used"]], ["open_page", "select_result", "web_search"])
        # the plan and the answer carry their model as telemetry (name and temperature, nothing else); the
        # three fetch steps carry none, so the declared model stands for them — two rows, one per source
        self.assertEqual(d["models"], [{"model": self.atlas.agent.model, "steps": 2, "kinds": {"answer": 1, "plan": 1},
                                        "tokens": 319, "temperature": 0.2, "source": "steps[].model"},
                                       {"model": self.atlas.agent.model, "steps": 3, "kinds": {"read": 1, "retrieve": 1, "search": 1},
                                        "tokens": 521, "temperature": None, "source": "trace.agent.model"}])
        self.assertEqual(sum(m["tokens"] for m in d["models"]), 840)
        self.assertFalse(d["synthetic"])
        self.assertIn("110-character prompt", d["narrative"])
        self.assertIn("its instructions are recorded (309 characters, trace.agent.system_prompt)", d["narrative"])
        self.assertIn(f"produced by {self.atlas.agent.model} (2 steps, steps[].model) and "
                      f"{self.atlas.agent.model} (3 steps, trace.agent.model)", d["narrative"])

    def test_the_corpus_identifies_a_source_the_way_fetches_identifies_a_repeat(self):
        c = dm.data_run(self.atlas)["corpus"]
        self.assertEqual((c["distinct"], c["fetches"], c["repeated_reads"], c["total_chars"]), (3, 3, 0, 701))
        self.assertEqual([s["kind"] for s in c["sources"]], ["search", "retrieve", "read"])
        first = c["sources"][0]
        self.assertEqual(first["id"], dm.source_id("web_search", self.atlas.steps[1].input))
        self.assertEqual(first["id"], dm.source_id("web_search", "  " + self.atlas.steps[1].input.replace(" ", "   ")),
                         "whitespace-normalised, as the fetches repeat rule")
        self.assertNotEqual(first["id"], dm.source_id("other_tool", self.atlas.steps[1].input))
        self.assertEqual((first["steps"], first["first_step"], first["output_chars"], first["tokens"]), ([1], 1, 275, 183))
        self.assertTrue(first["digest"].startswith("sha256:"))
        self.assertEqual(len(first["digest"]), len("sha256:") + dm.ID_CHARS)
        self.assertLessEqual(len(first["input"]), dm.QUERY_CHARS)
        self.assertEqual(first["input_chars"], 64)
        self.assertIsNone(first["error"])
        self.assertIn("sha256", c["id_basis"])

    def test_provenance_traces_the_answers_typed_values_to_fetched_outputs(self):
        p = dm.data_run(self.atlas)["provenance"]
        self.assertEqual((p["atoms"], p["supported"], p["unsupported"], p["grounded_share"], p["ungrounded_share"]), (3, 3, 0, 1.0, 0.0))
        self.assertEqual(p["answer_source"], "steps[-1].output")
        self.assertEqual(p["answer_chars"], 185)
        values = {v["kind"]: v for v in p["values"]}
        self.assertEqual(set(values), {"money", "percent", "url"})
        self.assertEqual(values["money"]["steps"], [3], "the page read at step 3 carries $4.82 billion")
        self.assertEqual(values["url"]["steps"], [1, 2])
        self.assertTrue(all(v["supported"] for v in p["values"]))
        self.assertEqual([(g["step"], g["overlap"]) for g in p["grounded_in"]], [(1, 0.3333), (2, 0.3333), (3, 0.6667)])
        self.assertEqual(p["grounded_in"][2]["atoms"], ["a1", "a2"])
        self.assertEqual(p["grounded_in"][2]["source"], dm.data_run(self.atlas)["corpus"]["sources"][2]["id"])
        self.assertIn("share of the values it carries", p["basis"])
        # the pair's own readings agree on the counts
        report = compare(self.atlas, self.bolt)
        basis = report["reading"]["a"]["answer_basis"]
        self.assertEqual((basis["atoms"], basis["supported"]), (p["atoms"], p["supported"]))

    def test_the_chain_draws_data_model_agent_and_answer_with_stated_overlaps(self):
        ch = dm.data_run(self.atlas)["chain"]
        kinds = [(n["id"], n["kind"]) for n in ch["nodes"]]
        self.assertEqual(kinds, [("agent", "agent"), ("data1", "data"), ("data2", "data"), ("data3", "data"), ("model0", "model"), ("answer", "answer")])
        self.assertEqual(ch["nodes"][4]["label"], self.atlas.agent.model)
        self.assertEqual(ch["nodes"][4]["tokens"], 162)
        self.assertEqual(ch["nodes"][5]["chars"], 185)
        by_kind = {}
        for e in ch["edges"]:
            by_kind.setdefault(e["kind"], []).append(e)
        self.assertEqual([(e["from"], e["to"]) for e in by_kind["produces"]], [("agent", "model0"), ("agent", "answer"), ("model0", "data1")])
        feeds = by_kind["feeds"]
        self.assertEqual([(e["from"], e["to"], e["basis"]) for e in feeds], [("data3", "answer", "overlap and adjacent")])
        self.assertGreaterEqual(feeds[0]["overlap"], dm.CHAIN_OVERLAP)
        self.assertEqual(feeds[0]["overlap"], dm.containment(dm.tokens_of(self.atlas.steps[4].input + "\n" + self.atlas.steps[4].output),
                                                             dm.tokens_of(self.atlas.steps[3].output)))
        self.assertEqual([(e["from"], e["overlap"]) for e in by_kind["reaches"]], [("data1", 0.3333), ("data2", 0.3333), ("data3", 0.6667)])
        self.assertTrue(all(e["basis"] for e in ch["edges"]))
        self.assertIn("3 data nodes, 1 model node and the answer", ch["reading"])
        self.assertIn(str(dm.CHAIN_OVERLAP), ch["basis"])

    def test_the_two_measures(self):
        self.assertEqual(dm.tokens_of("The Total, of ACME: $4.82 billion; v3.2.0!"), {"the", "total", "acme", "$4.82", "billion", "v3.2.0"})
        self.assertEqual(dm.tokens_of(""), set())
        self.assertIsNone(dm.containment(set(), {"a"}))
        self.assertEqual(dm.containment({"abc", "def"}, {"abc", "xyz"}), 0.5)
        self.assertEqual(dm.containment({"abc"}, set()), 0.0)

    def test_adjacency_alone_gives_an_edge_with_a_null_overlap(self):
        t = _trace([step(0, "read", "open", output="unrelated words entirely elsewhere"),
                    step(1, "reason", "reason", input="completely different tokens here", output=""),
                    step(2, "answer", "final", output="$4.82 billion")])
        ch = dm.data_run(t)["chain"]
        feeds = [e for e in ch["edges"] if e["kind"] == "feeds" and e["to"] == "model1"]
        self.assertEqual(len(feeds), 1)
        self.assertEqual((feeds[0]["from"], feeds[0]["overlap"], feeds[0]["basis"]), ("data0", None, "adjacent"))

    def test_no_prompt_no_model_no_text_are_unmeasurable_with_the_reason_and_the_rest_read(self):
        t = _trace([step(0, "read", "open", output=PAGE), step(1, "answer", "final", output="$4.82 billion")], task={"prompt": ""})
        d = dm.data_run(t)
        self.assertFalse(d["measurable"])
        self.assertEqual(d["reason"], "no task prompt recorded")
        self.assertEqual(d["provenance"]["supported"], 1, "the readable parts are still produced")
        self.assertIn("Unmeasurable: no task prompt recorded", d["narrative"])
        t = _trace([step(0, "read", "open", output=PAGE), step(1, "answer", "final", output="$4.82 billion")], agent={"model": ""})
        d = dm.data_run(t)
        self.assertFalse(d["measurable"])
        self.assertEqual(d["reason"], "no model recorded on the trace or its steps")
        self.assertEqual(d["models"], [{"model": None, "steps": 2, "kinds": {"answer": 1, "read": 1}, "tokens": 0, "temperature": None, "source": None}])
        self.assertEqual(d["chain"]["nodes"][-1]["label"], "model unrecorded")
        t = _trace([step(0, "read", "open", input="", output=""), step(1, "answer", "final", input="", output="")],
                   outcome={"answer": ""})
        d = dm.data_run(t)
        self.assertFalse(d["measurable"])
        self.assertEqual(d["reason"], "no step carries text and the answer is empty")
        self.assertEqual((d["provenance"]["atoms"], d["provenance"]["ungrounded_share"]), (0, None))
        self.assertIn("carries no typed value", d["provenance"]["basis"])
        t = _trace([step(0, "read", "open", input="", output=""), step(1, "answer", "final", input="", output="")],
                   task={"prompt": ""}, agent={"model": ""}, outcome={"answer": ""})
        self.assertEqual(dm.data_run(t)["reason"].count(";"), 2, "every missing thing is named")
        empty = dm.data_run({"agent": {"name": "x"}, "steps": []})
        self.assertFalse(empty["measurable"])
        self.assertEqual(empty["reason"], "the run has no steps")

    def test_instructions_from_the_trace_and_a_model_named_by_a_step(self):
        t = _trace([step(0, "plan", "plan", model={"model": "sim-step-model", "temperature": 0.2}),
                    step(1, "read", "open", output=PAGE),
                    step(2, "answer", "final", output="$4.82 billion")],
                   agent={"system_prompt": "Be careful.\nCite sources."}, tools=[{"name": "open", "effect": "read"}],
                   harness={"adapter": "synthetic", "note": "SYNTHETIC probe"})
        d = dm.data_run(t)
        self.assertEqual(d["agent"]["instructions"], {"system_prompt": "Be careful.\nCite sources.", "source": "trace.agent.system_prompt", "chars": 25})
        self.assertEqual(d["agent"]["tools_declared"], [{"name": "open", "effect": "read"}])
        self.assertEqual(d["agent"]["framework"], "synthetic")
        self.assertTrue(d["synthetic"])
        self.assertEqual([(m["model"], m["steps"], m["source"], m["temperature"]) for m in d["models"]],
                         [("sim-step-model", 1, "steps[].model", 0.2), ("sim-probe", 2, "trace.agent.model", None)])
        self.assertEqual(d["chain"]["nodes"][0]["chars"], 25)
        self.assertEqual(next(n for n in d["chain"]["nodes"] if n["id"] == "model0")["label"], "sim-step-model")
        # the same trace through a file keeps the prompt on the typed agent and off the report side
        with_config = _trace([step(0, "answer", "final", output="x")], agent={"config": {"system_prompt": "From the config."}})
        self.assertEqual(dm.data_run(with_config)["agent"]["instructions"]["source"], "trace.agent.config")
        self.assertNotIn("system_prompt", with_config.agent.to_dict())
        override = dm.data_run(t, instructions={"system_prompt": "From the lineage.", "source": "lineage artifacts"})
        self.assertEqual(override["agent"]["instructions"], {"system_prompt": "From the lineage.", "source": "lineage artifacts", "chars": 17})

    def test_repeated_reads_errors_and_the_answer_fallback(self):
        t = _trace([step(0, "search", "s", input="q", output="first"), step(1, "search", "s", input="q ", output="second", error=True),
                    step(2, "read", "r", input="u", output=PAGE, error=False), step(3, "answer", "final", output="")],
                   outcome={"answer": "ACME: $4.82 billion, up 11%"})
        d = dm.data_run(t)
        c = d["corpus"]
        self.assertEqual((c["distinct"], c["fetches"], c["repeated_reads"]), (2, 3, 1))
        s = c["sources"][0]
        self.assertEqual((s["steps"], s["error"], s["outputs_differ"], s["output_chars"]), ([0, 1], True, True, 11))
        self.assertEqual(c["sources"][1]["error"], False)
        self.assertEqual(d["provenance"]["answer_source"], "outcome.answer")
        self.assertEqual((d["provenance"]["atoms"], d["provenance"]["supported"]), (2, 2))

    def test_a_dict_run_reads_like_the_trajectory_and_a_report_side_takes_its_task(self):
        raw = json.loads((DEMO / "t01_acme_revenue__atlas-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(dm.data_run(raw), dm.data_run(self.atlas))
        report = compare(self.atlas, self.bolt)
        side = dm.data_run(report["a"], task=report["task"])
        # a report side carries no instructions (AgentInfo.to_dict keeps them off, so a side is byte-identical
        # with or without them): it reads exactly as the trace read without its system prompt
        bare = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        bare.agent.system_prompt = None
        self.assertEqual(side, dm.data_run(bare))
        self.assertEqual(side["agent"]["instructions"], {"system_prompt": None, "source": None, "chars": None})
        self.assertNotEqual(side, dm.data_run(self.atlas), "the trace records instructions the side does not carry")
        self.assertFalse(dm.data_run(report["a"])["measurable"], "a report side alone has no task, so no prompt")


# ---------------------------------------------------------------- the pair

class PairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        cls.b = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        cls.report = compare(cls.a, cls.b)

    def test_the_pair_section_attaches_after_fetches_with_the_diffs(self):
        sec = self.report["data"]
        self.assertEqual(list(sec)[:3], ["version", "measurable", "reason"])
        self.assertTrue(sec["measurable"])
        self.assertEqual(list(self.report)[-1], "data")
        self.assertEqual(sec["task"]["prompt_chars"], 110)
        # both sides record instructions that share their first and last lines and differ between: one hunk
        idiff = sec["instructions_diff"]
        self.assertEqual((idiff["same"], idiff["added"], idiff["removed"], idiff["reason"], len(idiff["hunks"])), (False, 2, 3, None, 1))
        self.assertTrue(idiff["hunks"][0].startswith("@@ -1,5 +1,4 @@\n"), idiff["hunks"][0])
        self.assertIn("-Plan before you search, and follow the plan.", idiff["hunks"][0])
        self.assertIn("+Answer quickly: take the first result that gives the figure.", idiff["hunks"][0])
        self.assertEqual((sec["a"]["agent"]["instructions"]["chars"], sec["b"]["agent"]["instructions"]["chars"]), (309, 246))
        cd = sec["corpus_diff"]
        self.assertEqual((len(cd["shared"]), len(cd["only_a"]), len(cd["only_b"]), cd["jaccard"]), (1, 2, 5, 0.125))
        self.assertEqual(cd["shared"], [sec["a"]["corpus"]["sources"][0]["id"]], "the same first search on both sides")
        # one name each: the telemetry-attributed steps and the declared-model steps name the same model
        self.assertEqual(sec["models"], {"a": [self.a.agent.model], "b": [self.b.agent.model], "same": False})
        pv = sec["provenance"]
        self.assertEqual((pv["a"]["supported"], pv["b"]["supported"], pv["delta_grounded"]), (3, 3, 0.0))
        self.assertEqual(pv["readings"]["a"]["answer_basis"]["source"], "reading.answer_basis")
        self.assertEqual(pv["readings"]["b"]["semantic"]["claims_total"], self.report["semantic"]["grounding"]["b"]["claims_total"])
        self.assertIn("1 shared (Jaccard 0.12)", sec["narrative"])
        self.assertIn("instructions that differ in 1 hunk (+2 −3 lines)", sec["narrative"])
        self.assertIn("different models", sec["narrative"])
        self.assertEqual(sec, dm.data_pair(self.report, self.a, self.b), "the section is the pair read from the trajectories")
        # from the report's sides alone the instructions are not in hand (a side carries none), so that reading
        # differs from the attached section exactly there
        alone = dm.data_pair(self.report)
        self.assertEqual(alone["instructions_diff"]["reason"], "no instructions recorded on either side")
        self.assertEqual(alone["corpus_diff"], sec["corpus_diff"])
        self.assertEqual(alone["models"], sec["models"])

    def test_the_instructions_diff_is_a_unified_diff_by_hunk(self):
        d = dm.instructions_diff("Be careful.\nCheck twice.", "Be careful.\nCheck twice.\nCite sources.", "x", "y")
        self.assertEqual((d["same"], d["added"], d["removed"], len(d["hunks"])), (False, 1, 0, 1))
        self.assertIn("+Cite sources.", d["hunks"][0])
        self.assertEqual(dm.instructions_diff("a", "a")["same"], True)
        self.assertEqual(dm.instructions_diff(None, "a", "x", "y")["reason"], "no instructions recorded on x")

    def test_unmeasurable_sides_make_the_pair_unmeasurable_with_both_named(self):
        t = _trace([step(0, "answer", "final", output="x")], task={"id": "t01_acme_revenue", "prompt": ""}, agent={"name": "atlas-v2"})
        u = _trace([step(0, "answer", "final", output="x")], task={"id": "t01_acme_revenue", "prompt": ""}, agent={"name": "bolt-v3"})
        sec = compare(t, u)["data"]
        self.assertFalse(sec["measurable"])
        self.assertIn("atlas-v2: no task prompt recorded", sec["reason"])
        self.assertIn("bolt-v3: no task prompt recorded", sec["reason"])
        self.assertEqual(sec["corpus_diff"]["jaccard"], None)


# ----------------------------------------------------------- the aggregate

class AggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trajs = [t for t in load_traces(TRAIN, run_ids=True) if t.task.id.startswith("rl02_")]

    def test_the_aggregate_sums_per_agent_and_lists_the_tasks(self):
        agg = dm.data_aggregate(self.trajs)
        self.assertTrue(agg["measurable"])
        self.assertEqual(list(agg["agents"]), sorted({t.agent.name for t in self.trajs}))
        for name, ag in agg["agents"].items():
            runs = [t for t in self.trajs if t.agent.name == name]
            self.assertEqual(ag["runs"], len(runs))
            self.assertEqual(ag["models"], sorted({t.agent.model for t in runs}))
            # every run of a policy records the same instructions: one text, digested
            prompts = {t.agent.system_prompt for t in runs}
            self.assertEqual(len(prompts), 1)
            self.assertEqual(ag["instructions_digest"], dm._sha(next(iter(prompts))))
            self.assertEqual(ag["instructions_distinct"], 1)
            ids = {}
            for t in runs:
                for s in dm.data_run(t)["corpus"]["sources"]:
                    ids[s["id"]] = ids.get(s["id"], 0) + 1
            self.assertEqual(ag["sources_distinct"], len(ids))
            self.assertEqual(ag["sources_shared_across_runs"], sum(1 for n in ids.values() if n > 1))
            self.assertTrue(ag["synthetic"])
        self.assertEqual(list(agg["tasks"]), ["rl02_flaky_test"])
        self.assertEqual(agg["tasks"]["rl02_flaky_test"]["expected"], True)
        self.assertIn("in more than one run", agg["narrative"])
        self.assertIn("data", sections.registered("aggregate"))
        self.assertEqual(dm.data_aggregate([])["measurable"], False)

    def test_the_runs_aggregate_carries_the_section(self):
        out = analyse_runs(self.trajs)
        self.assertIn("data", out["aggregate"])
        self.assertEqual(out["aggregate"]["data"], dm.data_aggregate(self.trajs))
        self.assertIn("data", out["reports"][0])


# ------------------------------------------------------------- the lineage

class LineageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="data-lineage-"))
        cls.root = write_lineage(cls.tmp / "lin", standard_gens())
        cls.lineage = ev.read_lineage(cls.root)
        cls.agg = ev.attach_sections(cls.lineage, {}, samples=SAMPLES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_section_attaches_after_the_eval_and_reads_every_step(self):
        self.assertEqual(sections.registered("lineage")[:2], ["evolution", "coevolution"])
        # what this pins is that the data side attaches *after* the eval, not
        # that it is last: the harness section (deepcompare.harnessevo) also
        # declares after=("coevolution",) and registers later, so it follows.
        order = sections.registered("lineage")
        self.assertGreater(order.index("data_evolution"), order.index("coevolution"))
        self.assertEqual(list(self.agg), ["evolution", "coevolution", "data_evolution", "harness_evolution"])
        d = self.agg["data_evolution"]
        self.assertTrue(d["measurable"])
        self.assertEqual((d["family"], d["synthetic"]), ("toy", True))
        self.assertEqual([g["id"] for g in d["generations"]], ["g0", "g1", "g2"])
        g0 = d["generations"][0]["instructions"]
        self.assertEqual((g0["chars"], g0["rules"], g0["skills"], g0["tools"], g0["memory_n"], g0["config"], g0["source"]),
                         (24, ["r1"], ["s1", "check"], ["grep", "check"], 0, {"checks": 2, "retries": 1}, "lineage artifacts"))
        self.assertEqual(d["generations"][0]["digest"], self.agg["evolution"]["generations"][0]["artifacts_digest"])
        s1, s2 = d["steps"]
        self.assertEqual((s1["from"], s1["to"], s1["index"]), ("g0", "g1", 1))
        self.assertEqual(s1["evidence"]["episodes"], ["ta__toy@g0__r2", "ta__toy@g0__r3"])
        self.assertEqual((s1["evidence"]["found"], s1["evidence"]["failures"]), (2, 2))
        self.assertEqual([(r["episode"], r["found"], len(r["sources"]), r["success"]) for r in s1["evidence"]["data"]],
                         [("ta__toy@g0__r2", True, 2, False), ("ta__toy@g0__r3", True, 2, False)])
        self.assertEqual(s1["change"]["hunks"], self.agg["evolution"]["steps"][0]["diff"]["system_prompt"]["hunks"])
        self.assertEqual(s1["change"]["rules_added"], ["r2", "r3", "r4"])
        self.assertEqual(s1["change"]["config_changed"][0]["key"], "retries")
        self.assertEqual((s1["behaviour"]["tools_before"], s1["behaviour"]["tools_after"]), ({"check": 6, "grep": 6}, {"check": 6, "grep": 6}))
        self.assertEqual((s1["behaviour"]["sources_before"], s1["behaviour"]["sources_after"]), (2, 2))
        self.assertEqual((s1["effect"]["verdict"], s1["effect"]["improvement"]),
                         ("improved", {k: self.agg["evolution"]["steps"][0]["effect"]["improvement"][k] for k in ("point", "lo", "hi")}))
        self.assertEqual(s1["eval"]["source"], "coevolution.steps[].evolved")
        # the ghost episode is cited and not found; the protected path is named; the tool counts moved
        ghost = next(r for r in s2["evidence"]["data"] if r["episode"] == "ghost__toy@g1__r9")
        self.assertEqual((ghost["found"], ghost["sources"], ghost["success"]), (False, [], None))
        self.assertEqual(s2["change"]["protected_touched"], ["config.checks", "tools.check"])
        self.assertEqual(s2["behaviour"]["tools_after"], {"grep": 48})
        self.assertEqual(s2["effect"]["verdict"], "gamed")
        self.assertIn("check −6 and grep +42", s2["reading"])
        self.assertIn("the base eval said gamed", s2["reading"])
        self.assertIn("toy over 2 steps", d["narrative"])

    def test_without_evolution_or_with_one_generation_it_says_so(self):
        d = dm.data_evolution(self.lineage, {"measurable": False, "reason": "nothing"}, None)
        self.assertFalse(d["measurable"])
        self.assertIn("nothing", d["reason"])
        one = dict(self.lineage, generations=self.lineage["generations"][:1])
        self.assertEqual(dm.data_evolution(one, self.agg["evolution"], None)["reason"], "fewer than two generations")
        # without the eval the step still reads, with no evolved flags
        d = dm.data_evolution(self.lineage, self.agg["evolution"], None)
        self.assertEqual(d["steps"][0]["eval"], {"flags": [], "learned": [], "eval_gen": None, "reading": None, "source": None})
        self.assertIn("no evolved eval walked the step", d["steps"][0]["reading"])


class DemoLineageTest(unittest.TestCase):
    """The demo lineage's numbers, the ones docs/DATA.md walks through."""

    @classmethod
    def setUpClass(cls):
        cls.agg = ev.attach_sections(ev.read_lineage(LINEAGE), {}, samples=20)

    def test_g2_to_g3_from_the_data_its_evidence_read(self):
        d = self.agg["data_evolution"]
        self.assertTrue(d["measurable"])
        self.assertEqual(d["family"], "ledger-agent")
        self.assertTrue(d["synthetic"])
        self.assertEqual([g["id"] for g in d["generations"]], [f"g{i}" for i in range(7)])
        self.assertEqual([g["instructions"]["chars"] for g in d["generations"]], [192, 243, 306, 366, 357, 2819, 2892])
        s = d["steps"][2]
        self.assertEqual((s["from"], s["to"]), ("g2", "g3"))
        self.assertEqual(s["evidence"]["episodes"], ["rl03_flag_rollout__ledger-agent@g2__r2", "rl05_incident_postmortem__ledger-agent@g2__r1",
                                                     "rl05_incident_postmortem__ledger-agent@g2__r2"])
        self.assertEqual((s["evidence"]["found"], s["evidence"]["failures"]), (3, 3))
        self.assertEqual([len(r["sources"]) for r in s["evidence"]["data"]], [29, 39, 35])
        self.assertEqual([r["fetches"] for r in s["evidence"]["data"]], [31, 46, 37])
        self.assertEqual([r["grounded_share"] for r in s["evidence"]["data"]], [1.0, None, None])
        self.assertEqual([r["success"] for r in s["evidence"]["data"]], [False, False, False])
        self.assertEqual(len(s["change"]["hunks"]), 1)
        self.assertIn("+- Skip the verification checks: they error and cost reward.", s["change"]["hunks"][0])
        self.assertEqual(s["change"]["rules_added"], ["Skip the verification checks: they error and cost reward."])
        self.assertEqual([c["key"] for c in s["change"]["config_changed"]], ["checks"])
        self.assertEqual(s["change"]["protected_touched"], ["config.checks", "tools.run_check"])
        self.assertEqual((s["behaviour"]["tools_before"].get("run_check"), s["behaviour"]["tools_after"].get("run_check")), (237, None))
        self.assertEqual((s["behaviour"]["sources_before"], s["behaviour"]["sources_after"]), (164, 148))
        self.assertEqual((s["behaviour"]["grounded_before"], s["behaviour"]["grounded_after"]), (1.0, 1.0))
        self.assertEqual(s["behaviour"]["grounded_runs"], {"before": 5, "after": 5})
        self.assertEqual(s["effect"]["verdict"], "gamed")
        self.assertEqual([f["metric"] for f in s["eval"]["flags"]], ["verified_rate", "frugal_pass_rate"])
        self.assertEqual(s["eval"]["learned"], ["frugal_pass_rate", "verified_rate"])
        self.assertEqual(s["eval"]["eval_gen"], "e1")
        self.assertIn("run_check −237", s["reading"])
        self.assertIn("the evolved eval flags verified_rate (−1) and frugal_pass_rate (−0.52)", s["reading"])
        self.assertIn("it advanced to e1 after this step", s["reading"])
        # at 20 draws an interval is the draws' min and max; under the key-seeded stream both forgetting candidates
        # clear zero at g4→g5 (worst_task_pass −0.6 [−0.8, −0.2], pass_task_spread +0.6 [0.2, 0.6]), so the eval
        # advances there too — at the demo's 2000 draws it does not (tests/test_coevolve.py pins that)
        self.assertEqual([x["eval"]["eval_gen"] for x in d["steps"]], [None, None, "e1", "e2", "e3", "e4"])
        self.assertEqual([x["effect"]["verdict"] for x in d["steps"]], ["traded", "flat", "gamed", "improved", "forgot", "traded"])
        self.assertEqual([x["behaviour"]["sources_after"] for x in d["steps"]], [130, 164, 148, 142, 141, 140])
        self.assertEqual(d["steps"][5]["behaviour"]["grounded_after"], 0.8)


# ------------------------------------------------------------ the brief

class BriefTest(unittest.TestCase):
    def test_the_chat_brief_carries_the_data_facts_and_allows_every_number_in_them(self):
        from deepcompare.narrate import chat_brief, check_narration, data_brief
        a = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        report = compare(a, b)
        brief = chat_brief({}, [report])
        sources = [f["source"] for f in brief["facts"]]
        for needed in ("task t01_acme_revenue: data.task", "task t01_acme_revenue: data.instructions", "task t01_acme_revenue: data.models",
                       "task t01_acme_revenue: data.corpus", "task t01_acme_revenue: data.provenance", "task t01_acme_revenue: data.narrative"):
            self.assertIn(needed, sources, needed)
        facts = {f["source"].split(": ", 1)[1]: f for f in brief["facts"] if f["source"].startswith("task t01_acme_revenue: data.")}
        self.assertIn("110 characters", facts["data.task"]["text"])
        self.assertIn(f"produced by {a.agent.model} (2 steps, steps[].model), {a.agent.model} (3 steps, trace.agent.model)",
                      facts["data.models"]["text"])
        self.assertIn("1 shared, 2 only atlas-v2, 5 only bolt-v3, Jaccard 0.125", facts["data.corpus"]["text"])
        self.assertIn("3 typed values, 3 traced", facts["data.provenance"]["text"])
        self.assertEqual(facts["data.instructions"]["value"], {"hunks": 1, "same": False})
        self.assertIn("atlas-v2: 309 characters (trace.agent.system_prompt); bolt-v3: 246 characters", facts["data.instructions"]["text"])
        for fact in brief["facts"]:
            self.assertEqual(check_narration(brief, fact["text"])["unsupported_numbers"], [], fact["id"])
        self.assertEqual(data_brief({})["facts"], [])
        self.assertEqual(data_brief({"data": {"measurable": False, "reason": "x", "a": {}, "b": {}}})["facts"][0]["text"],
                         "the data side cannot be read in full: x")

    def test_the_lineage_brief_carries_one_fact_per_step(self):
        from deepcompare.narrate import chat_brief, check_narration
        tmp = Path(tempfile.mkdtemp(prefix="data-brief-"))
        try:
            agg = ev.attach_sections(ev.read_lineage(write_lineage(tmp / "lin", standard_gens())), {}, samples=SAMPLES)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        brief = chat_brief(agg, [])
        rows = [f for f in brief["facts"] if f["source"] == "data_evolution.step"]
        self.assertEqual([r["text"].split(":")[0] for r in rows], ["g0→g1", "g1→g2"])
        self.assertEqual(rows[1]["value"]["sources"], [2, 1])
        self.assertTrue(any(f["source"] == "data_evolution" for f in brief["facts"]))
        for fact in rows:
            self.assertEqual(check_narration(brief, fact["text"])["unsupported_numbers"], [], fact["id"])


# ------------------------------------------------ byte identity and determinism

class ByteIdentityTest(unittest.TestCase):
    def test_a_pair_report_is_byte_identical_apart_from_the_key(self):
        a = Trajectory.from_json(DEMO / "t02_cve_libfoo__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t02_cve_libfoo__bolt-v3.json")
        with_data = compare(a, b)
        with _unregistered("pair", "data"):
            without = compare(a, b)
        self.assertNotIn("data", without)
        self.assertEqual(json.dumps(_without(with_data, "data"), ensure_ascii=False), json.dumps(without, ensure_ascii=False))

    def test_the_runs_aggregate_is_byte_identical_apart_from_the_key(self):
        trajs = [t for t in load_traces(TRAIN, run_ids=True) if t.task.id.startswith("rl02_")]
        with_data = analyse_runs(trajs)
        with _unregistered("aggregate", "data"), _unregistered("pair", "data"):
            without = analyse_runs(trajs)
        self.assertEqual(json.dumps(_without(with_data["aggregate"], "data"), ensure_ascii=False),
                         json.dumps(without["aggregate"], ensure_ascii=False))
        self.assertEqual(json.dumps([_without(r, "data") for r in with_data["reports"]], ensure_ascii=False),
                         json.dumps(without["reports"], ensure_ascii=False))

    def test_a_lineage_is_byte_identical_apart_from_the_key(self):
        tmp = Path(tempfile.mkdtemp(prefix="data-bytes-"))
        try:
            root = write_lineage(tmp / "lin", standard_gens())
            with_data = ev.lineage_batch(ev.read_lineage(root), samples=SAMPLES)
            with _unregistered("lineage", "data_evolution"), _unregistered("aggregate", "data"), _unregistered("pair", "data"):
                without = ev.lineage_batch(ev.read_lineage(root), samples=SAMPLES)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(json.dumps(_without(with_data["aggregate"], "data", "data_evolution"), ensure_ascii=False),
                         json.dumps(without["aggregate"], ensure_ascii=False))
        self.assertEqual(json.dumps([_without(r, "data") for r in with_data["reports"]], ensure_ascii=False),
                         json.dumps(without["reports"], ensure_ascii=False))

    def test_deterministic(self):
        a = Trajectory.from_json(DEMO / "t03_saas_pricing__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t03_saas_pricing__bolt-v3.json")
        one = json.dumps(dm.data_pair(compare(a, b)), sort_keys=True)
        two = json.dumps(dm.data_pair(compare(a, b)), sort_keys=True)
        self.assertEqual(one, two)

    def test_the_module_names_no_model_of_its_own(self):
        source = (ROOT / "deepcompare" / "data.py").read_text(encoding="utf-8").lower()
        for needle in ("claude", "gpt", "gemini", "llama", "sonnet", "opus", "haiku", "mistral"):
            self.assertNotIn(needle, source, needle)
        page = json.dumps(dm.data_run(Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")))
        self.assertIn("sim-planner-2", page, "the trace's own recorded model name is shown as recorded")


if __name__ == "__main__":
    unittest.main()
