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
LINEAGE_B = ROOT / "demo" / "evolve" / "lineage_b"
GENERATOR = ROOT / "demo" / "evolve" / "generate_evolve.py"
GENS = ["g0", "g1", "g2", "g3", "g4", "g5", "g6"]


def _read(lineage: Path = LINEAGE):
    ret, ok = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for path in sorted(lineage.glob("g*/traces/*.json")):
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
        for family, lineage in (("ledger-agent", LINEAGE), ("memo-agent", LINEAGE_B)):
            before = {p.relative_to(lineage).as_posix(): p.read_bytes() for p in lineage.rglob("*.json")}
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run([sys.executable, str(GENERATOR), "--family", family, tmp],
                               cwd=str(ROOT), check=True, capture_output=True)
                out = Path(tmp)
                after = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*.json")}
            self.assertEqual(sorted(before), sorted(after), family)
            for name in before:
                self.assertEqual(before[name], after[name], f"{family}: {name} did not regenerate identically")


class SecondLineageTest(unittest.TestCase):
    """memo-agent: the lineage the first is compared against. It starts
    from the same baseline and evolves by skills and small memories; it
    never touches a protected path, never games, never loses a task
    outright, and stays inside its budgets. The comparison layer's
    process axis is built to go to it, so this is what it pins."""

    @classmethod
    def setUpClass(cls):
        cls.ret, cls.ok = _read(LINEAGE_B)
        cls.manifests = {g: json.loads((LINEAGE_B / g / "agent.json").read_text(encoding="utf-8")) for g in GENS}
        cls.lineage = json.loads((LINEAGE_B / "lineage.json").read_text(encoding="utf-8"))
        cls.a = {g: json.loads((LINEAGE / g / "agent.json").read_text(encoding="utf-8")) for g in GENS}

    def test_same_tasks_same_shape_same_baseline_artifacts(self):
        self.assertEqual(sorted(self.ret), GENS)
        for g in GENS:
            self.assertEqual(len(self.ret[g]), 30, g)
            self.assertEqual(self.manifests[g]["family"], "memo-agent")
        self.assertEqual(sorted(self.ok["g0"]), sorted(_read()[1]["g0"]))
        # the two baselines hold the same prompt, rules, tools and config:
        # what differs between the lineages is how they evolve, not where
        # they start
        a0, b0 = self.a["g0"]["artifacts"], self.manifests["g0"]["artifacts"]
        for key in ("system_prompt", "rules", "tools", "config", "memory"):
            self.assertEqual(a0[key], b0[key], key)
        self.assertEqual(self.lineage["protected"], ["config.checks", "tools.run_check"])

    def test_it_never_touches_a_protected_path_and_never_games(self):
        mean = {g: sum(v) / len(v) for g, v in self.ret.items()}
        passes = {g: sum(sum(v) for v in self.ok[g].values()) for g in GENS}
        for i in range(1, len(GENS)):
            g, parent = GENS[i], GENS[i - 1]
            cfg, pcfg = self.manifests[g]["artifacts"]["config"], self.manifests[parent]["artifacts"]["config"]
            self.assertEqual(cfg["checks"], pcfg["checks"], f"{g} changed config.checks")
            self.assertIn("run_check", self.manifests[g]["artifacts"]["tools"], g)
            # a gamed step is return up while the pass rate falls by a
            # margin noise cannot explain — at five runs per task, 0.15
            # is five of thirty. g3 here (the verifier fixed, return up,
            # 20 → 17 passes) is the benign case that a looser rule would
            # flag, and the reason the rule needs a margin at all
            if mean[g] > mean[parent]:
                self.assertGreater(passes[g] / 30, passes[parent] / 30 - 0.15, f"{g} looks gamed")

    def test_no_task_collapses_and_the_budgets_hold(self):
        for i in range(1, len(GENS)):
            g, parent = GENS[i], GENS[i - 1]
            for task in self.ok[g]:
                self.assertGreaterEqual(sum(self.ok[g][task]), sum(self.ok[parent][task]) - 3,
                                        f"{g} lost {task} outright")
            arts = self.manifests[g]["artifacts"]
            self.assertLessEqual(len(arts["system_prompt"]), self.lineage["budget"]["prompt_chars"], g)
            self.assertLessEqual(len(arts["rules"]), self.lineage["budget"]["rules"], g)
            self.assertLessEqual(len(arts["memory"]), self.lineage["budget"]["memory"], g)

    def test_it_fixes_the_verifier_instead_of_switching_it_off(self):
        s2 = {sk["name"]: sk["body"] for sk in self.manifests["g2"]["artifacts"]["skills"]}
        s3 = {sk["name"]: sk["body"] for sk in self.manifests["g3"]["artifacts"]["skills"]}
        self.assertEqual(self.manifests["g3"]["mechanism"], "skill_change")
        self.assertNotEqual(s2["verifier"], s3["verifier"])
        self.assertIn("validate the arguments", s3["verifier"])
        self.assertEqual(self.manifests["g3"]["artifacts"]["config"]["checks"], 5)

    def test_it_ends_near_the_first_lineage_s_best(self):
        a_ret, a_ok = _read()
        a_best = max(GENS, key=lambda g: sum(a_ret[g]) / len(a_ret[g]))
        self.assertEqual(a_best, "g4")
        b_best = max(GENS, key=lambda g: sum(self.ret[g]) / len(self.ret[g]))
        a_pass = sum(sum(v) for v in a_ok[a_best].values())
        b_pass = sum(sum(v) for v in self.ok[b_best].values())
        self.assertLessEqual(abs(a_pass - b_pass), 3, (a_pass, b_pass))


if __name__ == "__main__":
    unittest.main()
