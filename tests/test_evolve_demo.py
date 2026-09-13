"""The self-evolving demo lineage: seven generations built to go wrong.

The engine's tests pin what ``deepcompare.evolve`` reads off this
lineage. This file pins the lineage itself — that it regenerates byte
for byte, that every generation carries its artifacts and its
provenance, and that the shape the checks depend on is really in the
traces: a step whose return rose while its passes fell, a step that
lost one task outright, and a best generation that is not the last.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINEAGE = ROOT / "demo" / "evolve" / "lineage"
GENERATOR = ROOT / "demo" / "evolve" / "generate_evolve.py"
GENS = ["g0", "g1", "g2", "g3", "g4", "g5", "g6"]


def _read():
    ret, ok = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for path in sorted(LINEAGE.glob("g*/traces/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        gen = path.parts[-3]
        ret[gen].append(sum(s.get("reward", 0.0) for s in data["steps"]))
        ok[gen][data["task"]["id"]].append(bool(data["outcome"]["success"]))
    return ret, ok


class ShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ret, cls.ok = _read()
        cls.manifests = {g: json.loads((LINEAGE / g / "agent.json").read_text(encoding="utf-8")) for g in GENS}
        cls.lineage = json.loads((LINEAGE / "lineage.json").read_text(encoding="utf-8"))

    def test_seven_generations_six_tasks_five_runs_all_labelled(self):
        self.assertEqual(sorted(self.ret), GENS)
        for g in GENS:
            self.assertEqual(len(self.ret[g]), 30, g)
            self.assertEqual(sorted(len(v) for v in self.ok[g].values()), [5] * 6, g)
        for path in LINEAGE.glob("g*/traces/*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("SYNTHETIC", data["harness"]["note"])
            task, agent, run = path.stem.split("__")
            self.assertEqual(agent, f"ledger-agent@{path.parts[-3]}")
            self.assertEqual(data["agent"]["name"], agent)

    def test_the_parent_chain_is_linear_and_every_step_names_its_evidence(self):
        for i, g in enumerate(GENS):
            m = self.manifests[g]
            self.assertEqual(m["id"], g)
            self.assertEqual(m["parent"], GENS[i - 1] if i else None)
            self.assertEqual(m["family"], "ledger-agent")
            arts = m["artifacts"]
            for key in ("system_prompt", "rules", "skills", "tools", "memory", "config"):
                self.assertIn(key, arts, f"{g} lacks {key}")
            if i == 0:
                self.assertIsNone(m["mechanism"])
                self.assertIsNone(m["evidence"])
            else:
                self.assertTrue(m["mechanism"])
                ev = m["evidence"]
                self.assertTrue(ev["episodes"], f"{g} names no triggering episode")
                parent = GENS[i - 1]
                for tid in ev["episodes"]:
                    # the evidence is a real failing episode of the parent
                    path = LINEAGE / parent / "traces" / f"{tid}.json"
                    self.assertTrue(path.is_file(), tid)
                    self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["outcome"]["success"], tid)

    def test_the_gaming_step_raises_return_and_lowers_passes_by_touching_a_protected_path(self):
        mean = {g: sum(v) / len(v) for g, v in self.ret.items()}
        passes = {g: sum(sum(v) for v in self.ok[g].values()) for g in GENS}
        self.assertGreater(mean["g3"], mean["g2"])
        self.assertLess(passes["g3"], passes["g2"] * 0.7)
        self.assertEqual((self.manifests["g2"]["artifacts"]["config"]["checks"],
                          self.manifests["g3"]["artifacts"]["config"]["checks"]), (5, 0))
        self.assertIn("config.checks", self.lineage["protected"])
        self.assertNotIn("run_check", self.manifests["g3"]["artifacts"]["tools"])
        self.assertIn("run_check", self.manifests["g2"]["artifacts"]["tools"])

    def test_the_memory_step_forgets_one_task_and_breaks_the_budget(self):
        rl06 = "rl06_api_contract"
        self.assertEqual((sum(self.ok["g4"][rl06]), sum(self.ok["g5"][rl06])), (5, 0))
        m5 = self.manifests["g5"]["artifacts"]
        self.assertEqual(len(m5["memory"]), 40)
        self.assertGreater(len(m5["system_prompt"]), self.lineage["budget"]["prompt_chars"])
        self.assertGreater(len(m5["memory"]), self.lineage["budget"]["memory"])
        self.assertEqual(len(self.manifests["g4"]["artifacts"]["memory"]), 0)

    def test_the_best_generation_is_not_the_last(self):
        mean = {g: sum(v) / len(v) for g, v in self.ret.items()}
        passes = {g: sum(sum(v) for v in self.ok[g].values()) for g in GENS}
        self.assertEqual(max(mean, key=mean.get), "g4")
        self.assertEqual(max(passes, key=passes.get), "g4")
        # and the last step traded: one task back, another down
        rl06, rl03 = "rl06_api_contract", "rl03_flag_rollout"
        self.assertGreater(sum(self.ok["g6"][rl06]), sum(self.ok["g5"][rl06]))
        self.assertLess(sum(self.ok["g6"][rl03]), sum(self.ok["g5"][rl03]))

    def test_the_generator_reproduces_the_lineage_byte_for_byte(self):
        before = {p.relative_to(LINEAGE).as_posix(): p.read_bytes() for p in LINEAGE.rglob("*.json")}
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(GENERATOR), tmp], cwd=str(ROOT), check=True, capture_output=True)
            out = Path(tmp)
            after = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*.json")}
        self.assertEqual(sorted(before), sorted(after))
        for name in before:
            self.assertEqual(before[name], after[name], f"{name} did not regenerate identically")


if __name__ == "__main__":
    unittest.main()
