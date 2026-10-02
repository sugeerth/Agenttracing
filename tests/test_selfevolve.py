"""The self-evolving harness: agents run, evals judge them, the harness acts on what they caught.

Most tests drive :func:`agentdiff.selfevolve.self_evolve` with a scripted
runner whose agents respond to the harness the way the test says, so each
rule is checked on its own. One runs the whole command through the vendor
harness with the Claude Code stand-in (``FAKE_VENDOR_MODE=careless``),
which skips the check unless the harness tells it to run it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.harness.selfevolve import harness_args
from agentdiff.harness.vendors import VendorSpec
from agentdiff.selfevolve import ESSENTIAL_TOOLS, Harness, decide, remedy_for, self_evolve

ROOT = Path(__file__).resolve().parent.parent
FAKES = ROOT / "tests" / "fixtures" / "vendors"


def _traj(task: str, ok: bool, *, checked: bool, run: str, tool: str = "") -> dict:
    steps = [{"index": 0, "type": "tool_call", "name": "Read", "input": "f.py", "output": "x"},
             {"index": 1, "type": "tool_call", "name": "Edit", "input": "f.py", "output": "applied", "effect": "write"}]
    if tool:
        steps.append({"index": len(steps), "type": "tool_call", "name": tool, "input": "x", "output": "y"})
    if checked:
        steps.append({"index": len(steps), "type": "tool_call", "name": "Bash", "input": "pytest -q",
                      "output": "3 passed" if ok else "1 failed"})
    steps.append({"index": len(steps), "type": "answer", "name": "answer", "input": "", "output": "Done."})
    return {"schema_version": 1, "trace_id": f"{task}-{run}", "run_id": run, "agent": {"name": "agent"},
            "task": {"id": task, "prompt": "fix"}, "outcome": {"success": ok, "answer": "Done."},
            "totals": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0, "latency_s": 1.0},
            "steps": steps}


TASKS = ("t1", "t2", "t3", "t4", "t5", "t6")


class RemedyTest(unittest.TestCase):
    def test_a_claim_with_no_check_is_answered_by_a_check_instruction(self):
        r = remedy_for("claims_without_check", check="pytest -q")
        self.assertEqual(r["knob"], "instruction")
        self.assertIn("`pytest -q`", r["value"])

    def test_a_tool_that_travels_with_failure_is_denied_unless_the_agent_needs_it(self):
        self.assertEqual(remedy_for("tool_called:WebFetch")["knob"], "deny_tool")
        for tool in ESSENTIAL_TOOLS:
            self.assertIn("unactionable", remedy_for(f"tool_called:{tool}"))

    def test_a_detector_with_no_knob_behind_it_says_so(self):
        self.assertIn("unactionable", remedy_for("answer_matches:sorry"))
        self.assertIsNone(remedy_for("not a rule"))

    def test_each_knob_reaches_claude_code_by_its_own_flag_and_codex_by_none(self):
        h = Harness().with_remedy(remedy_for("claims_without_check")).with_remedy(remedy_for("tool_called:WebFetch"))
        args = harness_args(VendorSpec("claude"), h)
        self.assertEqual(args[args.index("--disallowedTools") + 1], "WebFetch")
        self.assertIn("--append-system-prompt", args)
        self.assertEqual(harness_args(VendorSpec("codex"), h), [])
        self.assertEqual((h.version, len(h.remedies)), (2, 2))


class DecideTest(unittest.TestCase):
    def test_kept_only_on_more_wins_than_losses_and_nothing_broken(self):
        base = [_traj("a", False, checked=False, run="1"), _traj("b", True, checked=True, run="1")]
        better = [_traj("a", True, checked=True, run="2"), _traj("b", True, checked=True, run="2")]
        self.assertEqual(decide(base, better)["verdict"], "kept")
        same = [_traj("a", False, checked=False, run="2"), _traj("b", True, checked=True, run="2")]
        self.assertEqual(decide(base, same)["verdict"], "reverted", "a change that does nothing is not kept")
        broke = [_traj("a", True, checked=True, run="2"), _traj("b", False, checked=True, run="2"),
                 _traj("c", True, checked=True, run="2")]
        base3 = base + [_traj("c", False, checked=True, run="1")]
        d = decide(base3, broke)
        self.assertEqual(d["verdict"], "reverted")
        self.assertEqual(d["broke"], ["b"])


class LoopTest(unittest.TestCase):
    def test_the_harness_learns_to_check_and_the_run_converges(self):
        calls = []

        def arm(h, label):
            calls.append(label)
            told = any("check" in i.lower() for i in h.instructions)
            return [_traj(t, told or t in ("t5", "t6"), checked=told or t in ("t5", "t6"), run=f"{label}-{r}")
                    for t in TASKS for r in ("r1", "r2")]
        res = self_evolve(arm, generations=4, check="pytest -q")
        self.assertEqual(calls, ["g0-h0", "g0-h1"], "the kept arm's runs are the next generation's: no third arm")
        g0 = res["lineage"][0]
        self.assertEqual(g0["action"]["remedy"]["id"], "instruction:claims_without_check")
        self.assertEqual(g0["action"]["test"]["verdict"], "kept")
        self.assertEqual(g0["action"]["test"]["passed"], {"current": [4, 12], "changed": [12, 12]})
        self.assertIn("converged at g1", res["stop"])
        self.assertEqual(res["harness"]["version"], 1)

    def test_a_change_that_does_not_help_is_reverted_and_never_tried_again(self):
        calls = []

        def arm(h, label):
            calls.append((label, list(h.remedies)))
            return [_traj(t, t in ("t5", "t6"), checked=t in ("t5", "t6"), run=f"{label}-{r}")
                    for t in TASKS for r in ("r1", "r2")]
        res = self_evolve(arm, generations=3)
        verdicts = [(g.get("action") or {}).get("test", {}).get("verdict") for g in res["lineage"]]
        self.assertEqual(verdicts[0], "reverted")
        tried = [g["action"]["remedy"]["id"] for g in res["lineage"] if g.get("action")]
        self.assertEqual(len(tried), len(set(tried)), "a reverted change is not tried twice")
        self.assertEqual(res["harness"]["version"], 0)

    def test_the_evals_meet_what_the_harness_produced_and_retire_what_it_fixed(self):
        # two failure modes: a missing check (fixed by an instruction), then a
        # tool that breaks the work (fixed by denying it); the evals follow both
        def arm(h, label):
            told = any("check" in i.lower() for i in h.instructions)
            denied = "Scratch" in h.deny_tools
            out = []
            for t in TASKS:
                for r in ("r1", "r2", "r3"):
                    uses = t in ("t1", "t2") and not denied
                    ok = (told or t == "t6") and not uses
                    out.append(_traj(t, ok, checked=told or t == "t6", run=f"{label}-{r}",
                                     tool="Scratch" if uses else ("Note" if t in ("t3", "t4") else "")))
            return out
        res = self_evolve(arm, generations=5, check="pytest -q", patience=1)
        ids = [g["action"]["remedy"]["id"] for g in res["lineage"] if g.get("action")
               and g["action"]["test"]["verdict"] == "kept"]
        self.assertEqual(ids, ["instruction:claims_without_check", "deny_tool:tool_called:Scratch"])
        self.assertIn("converged", res["stop"])
        self.assertEqual(res["harness"]["deny_tools"], ["Scratch"])
        lineage = res["evals"]["lineage"]
        self.assertEqual(len(lineage), len(res["lineage"]), "the evals met every generation")
        evals = {e["id"]: e for e in res["evals"]["evals"]}
        # a kept change's rule joins the suite on the experiment's evidence ...
        self.assertEqual(evals["claims_without_check"]["source"], "intervention")
        self.assertEqual(lineage[0]["born_by_intervention"], ["claims_without_check"])
        # ... and retires once the harness has fixed what it caught, saying so
        self.assertEqual(evals["claims_without_check"]["status"], "retired")
        self.assertIn("harness v1 prevents it", evals["claims_without_check"]["reason"])
        self.assertEqual(evals["tool_called:Scratch"]["status"], "active")

    def test_a_knob_an_agent_lacks_is_refused_before_it_is_tried(self):
        def arm(h, label):
            return [_traj(t, t not in ("t1", "t2"), checked=True, run=f"{label}-{r}",
                          tool="Scratch" if t in ("t1", "t2") else "")
                    for t in TASKS for r in ("r1", "r2")]
        res = self_evolve(arm, generations=1, refuse_knobs=("deny_tool",))
        weighed = res["lineage"][0]["weighed"]
        self.assertTrue(any("cannot be given" in (w.get("skipped") or "") for w in weighed))

    def test_the_ledger_continues_where_it_stopped(self):
        def arm(h, label):
            return [_traj(t, False, checked=False, run=f"{label}-{r}") for t in TASKS[:2] for r in ("r1",)]
        first = self_evolve(arm, generations=1)
        again = self_evolve(arm, generations=1, ledger=first["ledger"])
        self.assertEqual([g["generation"] for g in again["lineage"]], ["g0", "g1"])


class CommandTest(unittest.TestCase):
    def test_the_command_evolves_real_cli_plumbing_with_the_stand_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tasks = []
            for i in range(3):
                shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", tmp / f"w{i}")
                tasks.append({"id": f"pricing-{i}", "prompt": "Fix the bug in pricing.py.", "workspace": f"w{i}",
                              "check": "python3 -m unittest -q"})
            (tmp / "tasks.json").write_text(json.dumps({"tasks": tasks}))
            env = dict(os.environ, FAKE_VENDOR_MODE="careless", PYTHONPATH=str(ROOT))
            out = tmp / "evo"
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--task", str(tmp / "tasks.json"),
                                   "--agent", "claude", "--claude-bin", str(FAKES / "fake_claude.py"), "--runs", "1",
                                   "--generations", "3", "-o", str(out)],
                                  capture_output=True, text=True, env=env, timeout=240)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            result = json.loads((out / "self-evolve.json").read_text())
            g0 = result["lineage"][0]
            self.assertEqual(g0["failed"], 3)
            self.assertEqual(g0["action"]["test"]["passed"]["changed"], [3, 3])
            self.assertIn("converged at g1", result["stop"])
            h = json.loads((out / "harness.json").read_text())
            self.assertEqual(len(h["instructions"]), 1)
            trace = json.loads(next((out / "g0-h1" / "traces").glob("*.json")).read_text())
            self.assertEqual(trace["harness"]["evolved"]["version"], 1, "every trace records its harness")
            self.assertTrue((out / "ledger.json").is_file())
            self.assertTrue((out / "evals" / "evolve-evals.json").is_file())
            # and the hub shows it
            from agentdiff.hub.catalog import Catalog
            entries = Catalog(tmp, depth=3, limit=50).entries()
            self.assertEqual([x.kind for x in entries], ["evolution"])


class FixEvolveTest(unittest.TestCase):
    def test_fix_evolve_then_apply_keeps_the_evolved_harness_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", repo)
            env = dict(os.environ, FAKE_VENDOR_MODE="careless", PYTHONPATH=str(ROOT),
                       AGENTDIFF_CLAUDE_BIN=str(FAKES / "fake_claude.py"))
            out = Path(tmp) / "evo"
            done = subprocess.run([sys.executable, "-m", "agentdiff", "fix", "--evolve", "2", "--agent", "claude",
                                   "--check", "python3 -m unittest -q", "-o", str(out)],
                                  cwd=repo, capture_output=True, text=True, env=env, timeout=240)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertIn("kept", done.stdout)
            keep = re.search(r"agentdiff apply --dir (\S+)", done.stdout).group(1)
            self.assertTrue(keep.endswith("g0-h1"), "the change comes from the evolved harness's arm")
            applied = subprocess.run([sys.executable, "-m", "agentdiff", "apply", "--dir", keep], cwd=repo,
                                     capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            check = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=repo, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()
