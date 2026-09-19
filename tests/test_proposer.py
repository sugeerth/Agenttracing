"""The external proposer seam: a scripted provider's reply parsed into
candidate specs in the eval's language, every refusal returned as a row
the ledger records, the origin naming the provider, no retries, and the
boundary — the engine never imports the harness, the seam never sets a
number.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import coevolve as co  # noqa: E402
from deepcompare.harness import proposer  # noqa: E402
from deepcompare.harness.providers import ProviderError, ScriptedProvider  # noqa: E402

BRIEF = {"step": "g2→g3", "from": "g2", "to": "g3",
         "features": [{"id": f, "kind": ft.kind, "basis": ft.basis} for f, ft in co.FEATURES.items()]
         + [{"id": "uses:run_check", "kind": "count", "basis": "calls of run_check"}],
         "language": {"agg": list(co.AGGS), "ops": list(co.OPS), "directions": list(co.DIRECTIONS)},
         "diff_summary": "-checks 5→0", "mechanism": "config", "base_verdict": "gamed", "base_flags": ["protected"],
         "adopted": [{"id": "pass_rate", "feature": "success", "agg": "rate"}],
         "shifts": [{"feature": "verified", "from": 1.0, "to": 0.0, "shift": -1.0, "standardised": None, "separates": True,
                     "sign_vs_outcome": "up"}]}


def reply(*entries) -> dict:
    return {"text": "Here you go:\n" + json.dumps(list(entries))}


class ProposeTest(unittest.TestCase):
    def test_a_parsed_proposal_becomes_a_candidate_with_the_provider_as_its_source(self):
        provider = ScriptedProvider([reply(
            {"id": "verified_rate", "name": "verification rate", "feature": "verified", "agg": "rate", "where": None,
             "direction": "up", "why": "the step switched verification off"},
            {"id": "runcheck_rate", "feature": "uses:run_check", "agg": "rate", "direction": "neutral", "why": "watch it"},
        )], model="model-under-test")
        rows = proposer.propose(provider, BRIEF)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["spec"]["id"], "verified_rate")
        self.assertEqual(rows[0]["spec"]["origin"], {"probe": "external", "source": "scripted-model-under-test"})
        self.assertEqual(rows[0]["origin"], {"probe": "external", "source": "scripted-model-under-test"})
        self.assertEqual(rows[0]["why"], "the step switched verification off")
        self.assertEqual(rows[1]["spec"]["feature"], "uses:run_check", "a dynamic feature the brief lists is known")
        self.assertEqual(rows[1]["spec"]["name"], "calls of run_check rate")
        self.assertNotIn("rejected", rows[0])

    def test_the_prompt_asks_for_json_only_and_carries_the_brief(self):
        seen = []
        provider = ScriptedProvider(lambda messages, tools: seen.append((messages, tools)) or reply())
        self.assertEqual(proposer.propose(provider, BRIEF, k=2), [])
        messages, tools = seen[0]
        self.assertIsNone(tools)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("Propose at most 2 metrics", messages[0]["content"])
        self.assertIn("Reply with a JSON array only, no prose.", messages[0]["content"])
        self.assertIn("mean, rate, iqm, task_mean, task_min, task_spread", messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"]), BRIEF)

    def test_every_refusal_is_a_row_the_ledger_can_record(self):
        provider = ScriptedProvider([reply(
            {"id": "ghost", "feature": "nope", "agg": "mean"},
            "not an object",
            {"id": "ok", "feature": "retries", "agg": "mean"},
            {"id": "too_many", "feature": "steps", "agg": "mean"},
        )])
        rows = proposer.propose(provider, BRIEF, k=3)
        self.assertEqual([("rejected" in r) for r in rows], [True, True, False, True])
        self.assertIn('unknown feature "nope"', rows[0]["rejected"])
        self.assertEqual(rows[0]["raw"], {"id": "ghost", "feature": "nope", "agg": "mean"})
        self.assertEqual(rows[1]["rejected"], 'not an object: "not an object"')
        self.assertEqual(rows[3]["rejected"], "beyond the 3 candidates asked for")
        self.assertTrue(all(r["origin"] == {"probe": "external", "source": "scripted-script"} for r in rows))

    def test_a_reply_without_json_and_a_provider_error_are_one_rejected_row_each_without_retry(self):
        provider = ScriptedProvider([{"text": "I cannot propose metrics."}])
        rows = proposer.propose(provider, BRIEF)
        self.assertEqual(rows, [{"rejected": "the proposer did not return the JSON array asked for",
                                 "raw": "I cannot propose metrics.", "origin": {"probe": "external", "source": "scripted-script"}}])
        with self.assertRaises(ProviderError):
            provider.complete([], None)
        rows = proposer.propose(provider, BRIEF)
        self.assertEqual(rows[0]["rejected"], "provider error: scripted provider has no more turns")
        self.assertIsNone(rows[0]["raw"])
        calls = []
        counting = ScriptedProvider(lambda m, t: calls.append(1) or (_ for _ in ()).throw(ProviderError("boom")))
        proposer.propose(counting, BRIEF)
        self.assertEqual(calls, [1], "no retry")

    def test_a_reply_that_is_an_object_not_an_array_is_refused(self):
        provider = ScriptedProvider([{"text": json.dumps({"id": "x", "feature": "retries", "agg": "mean"})}])
        rows = proposer.propose(provider, BRIEF)
        self.assertEqual(len(rows), 1)
        self.assertIn("did not return the JSON array", rows[0]["rejected"])

    def test_the_brief_vocabulary_governs_what_parses(self):
        provider = ScriptedProvider([reply({"id": "x", "feature": "uses:grep", "agg": "rate"})])
        rows = proposer.propose(provider, BRIEF)
        self.assertIn('unknown feature "uses:grep"', rows[0]["rejected"], "the brief did not list uses:grep")
        bare = ScriptedProvider([reply({"id": "x", "feature": "retries", "agg": "mean"})])
        rows = proposer.propose(bare, {"features": []})
        self.assertEqual(rows[0]["spec"]["feature"], "retries", "without a vocabulary the fixed one applies")


class BoundaryTest(unittest.TestCase):
    def test_the_seam_imports_only_the_engine_and_the_providers(self):
        tree = ast.parse((ROOT / "deepcompare" / "harness" / "proposer.py").read_text(encoding="utf-8"))
        modules = {(node.level, node.module) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual(modules - {(0, "__future__"), (0, "typing")}, {(2, "coevolve"), (1, "providers")})
        self.assertTrue({a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names} <= {"json", "re"})

    def test_the_engine_and_the_command_never_import_the_seam_at_module_level(self):
        for name in ("coevolve.py", "evolve.py", "commands/coevolve.py"):
            tree = ast.parse((ROOT / "deepcompare" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "harness" in node.module:
                    self.assertNotEqual(node.col_offset, 0, f"{name} imports {node.module} at module level")
                    self.assertTrue(name.startswith("commands/"), f"{name} must never import the harness")



def _lineage_and_evolution():
    import shutil
    import tempfile
    from deepcompare import evolve as ev
    sys.path.insert(0, str(ROOT / "tests"))
    from test_evolve import standard_gens, write_lineage
    tmp = Path(tempfile.mkdtemp(prefix="proposer-"))
    try:
        lineage = ev.read_lineage(write_lineage(tmp / "lin", standard_gens()))
        return lineage, ev.evolve(lineage, samples=100)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class IntegrationTest(unittest.TestCase):
    def test_proposals_reach_the_ledger_only_through_the_validators(self):
        lineage, evolution = _lineage_and_evolution()
        provider = ScriptedProvider([reply({"id": "retry_mean", "feature": "retries", "agg": "mean", "direction": "down"},
                                           {"id": "ghost", "feature": "nope", "agg": "mean"})])
        briefs = co.proposal_briefs(lineage, evolution)
        self.assertEqual([b["at"] for b in briefs], ["g0→g1", "g1→g2"])
        rows = proposer.propose(provider, briefs[1]["brief"])
        candidates = [{"at": "g1→g2", "source": r["origin"]["source"], **({"rejected": r["rejected"]} if "rejected" in r else {"spec": r["spec"]})}
                      for r in rows]
        c = co.coevolve(lineage, evolution, samples=100, candidates=candidates)
        ext = [r for r in c["ledger"] if r["probe"] == "external"]
        self.assertEqual([(r["step"], r["spec_id"], r["decision"]) for r in ext], [("g1→g2", "retry_mean", "rejected"), ("g1→g2", None, "rejected")])
        self.assertEqual(ext[0]["origin"]["source"], "scripted-script")
        self.assertTrue(ext[0]["reason"].startswith("distinct:"), "validated like any candidate: one reading with the base tool-call mean")
        self.assertIn('unparseable: unknown feature "nope"', ext[1]["reason"])
        self.assertEqual(c["integrity"]["external"], {"received": 2, "parsed": 1, "adopted": 0, "rejected": 2, "sources": ["scripted-script"]})
        self.assertEqual(c["recommended"], co.coevolve(lineage, evolution, samples=100)["recommended"],
                         "a proposer can never move the recommendation except through an adopted metric")


if __name__ == "__main__":
    unittest.main()
