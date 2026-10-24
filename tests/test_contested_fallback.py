"""A contested diagnosis still names a step: where the failing run first parts from the passing one
(diagnosis.first_twinless), labelled as a structural fallback. The cause stays contested; abstentions, where no
agent step exists to correct, never reach the fallback."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.diagnosis import CONTESTED_BASIS, _decisive_step, first_twinless  # noqa: E402
from agentdiff.report import compare  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402
from agentdiff.verdict import verdict_card  # noqa: E402
from test_structural_baselines import _traj  # noqa: E402

GENERATOR = ROOT / "demo" / "diagnosis_bench" / "generate_scale.py"


def _generate(out: Path, pairs: int) -> dict:
    spec = importlib.util.spec_from_file_location("diag_gen_scale", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(out, pairs, strip=True)


class FirstTwinlessTest(unittest.TestCase):
    def test_the_first_step_the_passing_run_never_took(self):
        failing = _traj([{"type": "tool_call", "name": "read", "input": "a", "output": "x"},
                         {"type": "tool_call", "name": "read", "input": "b", "output": "y"},
                         {"type": "answer", "name": "final", "input": "", "output": "no"}])
        passing = _traj([{"type": "tool_call", "name": "read", "input": "a", "output": "other output"},
                         {"type": "answer", "name": "final", "input": "", "output": "yes"}])
        self.assertEqual(first_twinless(failing, passing), 1, "a twin matches on type, name and input, not output")
        self.assertIsNone(first_twinless(passing, passing))

    def test_without_the_passing_run_a_contested_diagnosis_names_nothing(self):
        failing = _traj([{"type": "tool_call", "name": "read", "input": "b", "output": "y"},
                         {"type": "answer", "name": "final", "input": "", "output": "no"}])
        got = _decisive_step("single_failure", None, None, [], [], failing, "a")
        self.assertIsNone(got["step"])
        self.assertTrue(got["reason"].startswith("contested"))


class GeneratedPairsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        manifest = _generate(out, 45)
        cls.cases = []
        for sc in manifest["scenarios"]:
            f, p = Trajectory.from_json(out / sc["fail"]), Trajectory.from_json(out / sc["pass"])
            cls.cases.append((sc, f, p, compare(f, p)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_contested_diagnosis_names_where_the_runs_part_and_claims_no_cause(self):
        contested = [(sc, f, p, r) for sc, f, p, r in self.cases
                     if r["diagnosis"]["leading"] is None and r["diagnosis"]["mode"] == "single_failure"]
        self.assertTrue(contested, "the stripped corpus has contested diagnoses")
        named = 0
        for sc, f, p, report in contested:
            dec = report["diagnosis"]["decisive_step"]
            self.assertTrue(dec["reason"].startswith("contested: no hypothesis leads, so no cause is committed"))
            self.assertEqual(dec["step"], first_twinless(f, p), sc["id"])
            if dec["step"] is None:
                continue
            named += 1
            self.assertEqual(dec["basis"], CONTESTED_BASIS)
            self.assertEqual(dec["verification"], "hypothesized")
            self.assertEqual(dec["window"], {"earliest": dec["step"], "point_of_no_return": dec["step"], "steps": 1})
            self.assertEqual(report["diagnosis"]["confidence"]["level"], "low", "the cause is still contested")
            cause = next(line for line in verdict_card(report)["lines"] if line["key"] == "cause")
            self.assertIn("which cause acted there is contested", cause["text"])
        self.assertGreater(named, 0)

    def test_abstentions_are_untouched(self):
        for sc, f, p, report in self.cases:
            dec = report["diagnosis"]["decisive_step"]
            if report["diagnosis"]["leading"] is not None:
                self.assertNotEqual(dec["basis"], CONTESTED_BASIS, sc["id"])
            if dec["reason"] and not dec["reason"].startswith("contested"):
                self.assertIsNone(dec["step"], sc["id"])


if __name__ == "__main__":
    unittest.main()
