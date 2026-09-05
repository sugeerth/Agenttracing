"""The agentic loop end to end, with no network: a prompt-aware fake
provider through the Python API, scripted providers and a command
agent through the CLI, a seed hypothesis, resume, and the ledger."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers_loop import GROUNDING, TASKS, run_demo_loop  # noqa: E402
from deepcompare.scorecard import load_golden  # noqa: E402
from deepcompare.harness.providers import provider_from_spec  # noqa: E402
from deepcompare.harness.loop import PROMPT_ENV, render_ledger_markdown  # noqa: E402


class LoopApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "loop"
        cls.ledger = run_demo_loop(cls.out, template=None)
        cls.iters = cls.ledger["state"]["iterations"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_baseline_then_hypothesis_then_kept_then_remeasure(self):
        actions = [it["action"] for it in self.iters]
        self.assertEqual(actions[:2], ["compare", "test-prompt"])
        first = self.iters[0]
        self.assertEqual(first["results"]["sloppy"]["successes"], 0)
        self.assertEqual(first["results"]["steady"]["successes"], first["results"]["steady"]["runs"])
        self.assertGreaterEqual(first["suggestions_added"], 1, "the reading of the failing run queued a hypothesis")
        d = self.iters[1]["decision"]
        self.assertEqual(d["kind"], "unsourced_answer_value")
        self.assertIn(GROUNDING, d["text"])
        self.assertTrue(d["status"].startswith("kept"), d)
        self.assertEqual((d["evidence"]["wins"], d["evidence"]["losses"], d["evidence"]["regressions"]), (4, 0, []))
        self.assertIn("count as sloppy from here on", d["relabel"])
        self.assertEqual(self.ledger["state"]["prompts"]["sloppy"]["version"], 1)
        self.assertIn(GROUNDING, self.ledger["state"]["prompts"]["sloppy"]["current"])
        later = [it for it in self.iters[2:] if it["action"] == "compare"]
        self.assertTrue(later, "after a keep the comparison is re-measured")
        self.assertEqual(later[-1]["results"]["sloppy"]["successes"], later[-1]["results"]["sloppy"]["runs"],
                         "under the kept prompt the agent grounds its answers")

    def test_a_hypothesis_whose_failure_stopped_reproducing_is_dropped_not_run(self):
        hist = self.ledger["state"]["prompts"]["sloppy"]["history"]
        statuses = {h["kind"]: h["status"] for h in hist}
        self.assertTrue(statuses["unsourced_answer_value"].startswith("kept"))
        dropped = [h for h in hist if h["status"] == "dropped"]
        self.assertTrue(dropped, statuses)
        self.assertIn("no longer reproduces", dropped[0]["why"])
        self.assertEqual(self.ledger["summary"]["dropped_changes"], len(dropped))

    def test_every_iteration_is_a_full_runs_analysis_and_the_pool_is_honest(self):
        for it in self.iters:
            d = Path(it["dir"])
            self.assertTrue((d / "aggregate.json").is_file(), it["dir"])
            self.assertTrue(any(d.glob("report_*.json")))
            agg = json.loads((d / "aggregate.json").read_text(encoding="utf-8"))
            self.assertIn("routing", agg)
            self.assertIn("equality", agg)
        # the closing page carries the ledger and the pooled runs
        final = json.loads((self.out / "aggregate.json").read_text(encoding="utf-8"))
        self.assertEqual(final["loop"]["summary"]["iterations"], len(self.iters))
        spent = sum(len(list(Path(it["dir"], "traces").glob("*__*.json"))) for it in self.iters)
        self.assertEqual(self.ledger["summary"]["spent_runs"], spent, "every run spent is a trace on disk")
        kept_runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p, _ in self.ledger["pools"]["sloppy"]]
        self.assertTrue(all(t["agent"]["version"] == "p1" for t in kept_runs), "the pool holds only prompt-v1 runs after the keep")
        # run ids never repeat for one task and agent across iterations
        seen = set()
        for it in self.iters:
            for path in Path(it["dir"], "traces").glob("*__*.json"):
                self.assertNotIn(path.name, seen, path.name)
                seen.add(path.name)

    def test_the_stop_reason_and_the_markdown_ledger(self):
        stop = self.ledger["state"]["stop"]
        self.assertIn(stop["kind"], ("iterations", "converged"))
        md = render_ledger_markdown(self.ledger)
        self.assertIn("# Agent loop", md)
        self.assertIn("kept", md)
        self.assertIn(f"## Stopped: {stop['reason']}", md)
        self.assertIn("no model is in the control path", md)
        self.assertEqual((self.out / "LOOP.md").read_text(encoding="utf-8"), md)

    def test_a_golden_set_and_a_judging_model_enter_every_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            golden = Path(tmp) / "golden.json"
            golden.write_text(json.dumps({"policy": {"forbidden_tools": ["shell"]},
                                          "tasks": [{"id": t["id"], "expected_tools": ["get_refund"]} for t in TASKS]}), encoding="utf-8")
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps([{"text": '{"success": true, "score": 0.9, "rationale": "scripted"}'}]), encoding="utf-8")
            out = Path(tmp) / "loop"
            ledger = run_demo_loop(out, template=None, max_iterations=1, golden=load_golden(golden),
                                   judge_factory=lambda: provider_from_spec(f"scripted:{script}"))
            it = ledger["state"]["iterations"][0]
            card = it["results"]["sloppy"]["scorecard"]
            self.assertEqual(card["rates"]["tool_correct"]["rate"], 0.0, "sloppy never called the tool")
            self.assertEqual(card["rates"]["policy_compliant"]["rate"], 1.0)
            self.assertEqual(card["judge"]["judged"], 8)
            self.assertEqual(card["judge"]["confusion"]["grade_fail_judge_pass"], 8, "the exact match stays the reference")
            trace = json.loads(sorted(Path(it["dir"], "traces").glob("refund-BK1__sloppy*.json"))[0].read_text(encoding="utf-8"))
            self.assertEqual(trace["outcome"]["graded_by"], "model")
            self.assertTrue(trace["outcome"]["success"], "the judge's verdict is the grade when a judge is given")
            self.assertEqual(trace["outcome"]["judge"]["prior"], {"success": False, "graded_by": "exact-match"})
            agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(agg["scorecard"]["mode"], "offline — golden set")

    def test_resume_continues_from_the_ledger_without_rerunning(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "loop"
            first = run_demo_loop(out, template=None, max_iterations=1)
            self.assertEqual(first["summary"]["iterations"], 1)
            second = run_demo_loop(out, template=None, max_iterations=2, resume=True)
            self.assertEqual(second["summary"]["iterations"], 2)
            self.assertEqual(second["state"]["iterations"][0], first["state"]["iterations"][0])
            self.assertEqual(second["state"]["iterations"][1]["action"], "test-prompt")


class LoopCliTest(unittest.TestCase):
    def _tasks(self, tmp):
        path = Path(tmp) / "tasks.json"
        path.write_text(json.dumps(TASKS), encoding="utf-8")
        return path

    def test_scripted_providers_a_seed_hypothesis_and_a_reverted_no_effect_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps([{"text": "The refund for BK1 is $120.00, for BK2 $45.00, for BK3 $300.00 and for BK4 $12.00."}]), encoding="utf-8")
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps([{"text": "The refund is $99.00."}]), encoding="utf-8")
            out = Path(tmp) / "loop"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "loop", "--tasks", str(self._tasks(tmp)),
                                   "--provider", f"good=scripted:{good}", "--provider", f"bad=scripted:{bad}",
                                   "--runs", "2", "--iterations", "3", "-o", str(out),
                                   "--suggest", "bad=Always look the refund up.", "--template", str(ROOT / "web" / "blocks.html")],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("iteration 1 · compare", proc.stdout)
            self.assertIn("iteration 2 · test-prompt", proc.stdout)
            ledger = json.loads((out / "loop.json").read_text(encoding="utf-8"))
            d = ledger["state"]["iterations"][1]["decision"]
            self.assertEqual((d["kind"], d["status"]), ("seed-1", "reverted"))
            self.assertIn("given on the command line", d["source"])
            self.assertIn("reverted", proc.stdout)
            self.assertTrue((out / "report.html").is_file())
            self.assertTrue((out / "LOOP.md").is_file())

    def test_a_command_agent_receives_prompt_changes_through_its_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "agent.py"
            script.write_text(
                "import json, os, sys\n"
                "task = json.load(open(sys.argv[1]))\n"
                f"careful = {GROUNDING!r} in os.environ.get({PROMPT_ENV!r}, '')\n"
                "facts = {'BK1': '$120.00', 'BK2': '$45.00', 'BK3': '$300.00', 'BK4': '$12.00'}\n"
                "ref = task['prompt'].split('booking ')[1].rstrip('?')\n"
                "value = facts[ref] if careful else '$99.00'\n"
                "json.dump([{'role': 'user', 'content': task['prompt']},"
                " {'role': 'assistant', 'content': f'The refund for {ref} is {value}.'}], open(sys.argv[2], 'w'))\n",
                encoding="utf-8")
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps([{"text": "The refund for BK1 is $120.00, for BK2 $45.00, for BK3 $300.00 and for BK4 $12.00."}]), encoding="utf-8")
            out = Path(tmp) / "loop"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "loop", "--tasks", str(self._tasks(tmp)),
                                   "--provider", f"good=scripted:{good}",
                                   "--agent", f"tool=cmd:{sys.executable} {script} {{prompt_file}} {{out_file}}",
                                   "--runs", "1", "--iterations", "2", "-o", str(out), "--template", str(ROOT / "web" / "blocks.html"),
                                   "--suggest", f"tool=Every value {GROUNDING}."],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ledger = json.loads((out / "loop.json").read_text(encoding="utf-8"))
            self.assertIn("tool", ledger["config"]["prompt_tunable"])
            d = ledger["state"]["iterations"][1]["decision"]
            self.assertTrue(d["status"].startswith("kept"), d)
            self.assertEqual(d["evidence"]["variant"]["successes"], 4)
            self.assertNotIn(PROMPT_ENV, os.environ, "the environment is restored after the run")

    def test_the_loop_refuses_anything_but_two_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps([{"text": "x"}]), encoding="utf-8")
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "loop", "--tasks", str(self._tasks(tmp)),
                                   "--provider", f"good=scripted:{good}", "-o", str(Path(tmp) / "o")],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("exactly two agents", proc.stderr)


if __name__ == "__main__":
    unittest.main()
