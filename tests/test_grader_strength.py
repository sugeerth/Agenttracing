"""How strong a from-git task's grader is (gittasks.mutants, grader_strength, build(mutant_limit=...)): small
changes to the commit's own lines, each graded by the task's held-out tests."""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.gittasks import build, grader_strength, mutants  # noqa: E402
from test_gittasks import _commit, _git  # noqa: E402

CLAMP = "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n    return x\n"
HEAD = "import unittest\n\nfrom box import clamp\n\n\nclass ClampTest(unittest.TestCase):\n"
WEAK = HEAD + "    def test_middle(self):\n        self.assertEqual(clamp(5, 0, 10), 5)\n"
STRONG = WEAK + ("\n    def test_edges(self):\n        self.assertEqual(clamp(-1, 0, 10), 0)\n"
                 "        self.assertEqual(clamp(0, 0, 10), 0)\n        self.assertEqual(clamp(11, 0, 10), 10)\n"
                 "        self.assertEqual(clamp(10, 0, 10), 10)\n")


def _repo(where: Path, test: str) -> Path:
    repo = where / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "Start the box module", {"box.py": "def noop():\n    return None\n",
                                           "test_box.py": "import unittest\n\nimport box\n\n\nclass T(unittest.TestCase):\n"
                                                          "    def test_noop(self):\n        self.assertIsNone(box.noop())\n"})
    _commit(repo, "Add clamp, which keeps a number between a low and a high bound",
            {"box.py": "def noop():\n    return None\n\n\n" + CLAMP, "test_box.py": test})
    return repo


class MutantsTest(unittest.TestCase):
    def test_each_kind_of_change_on_the_given_lines_only(self):
        src = "def f(a, b):\n    if a < b and b != 0:\n        return a + 1\n    return True\n\nX = 1 + 2\n"
        m = mutants(src, {2, 3, 4})
        changes = [(x["line"], x["change"]) for x in m["mutants"]]
        self.assertIn((2, "Lt → LtE"), changes)
        self.assertIn((2, "And → Or"), changes)
        self.assertIn((2, "condition negated"), changes)
        self.assertIn((3, "Add → Sub"), changes)
        self.assertIn((4, "True → False"), changes)
        self.assertNotIn(6, [line for line, _ in changes], "line 6 was not changed by the commit")
        for x in m["mutants"]:
            self.assertNotEqual(ast.dump(ast.parse(x["source"])), ast.dump(ast.parse(m["baseline"])))
        self.assertEqual(m["sites"], len(m["mutants"]))

    def test_a_limit_picks_an_even_fixed_selection(self):
        src = "\n".join(f"x{i} = {i}" for i in range(40)) + "\n"
        a, b = mutants(src, set(range(1, 41)), limit=5), mutants(src, set(range(1, 41)), limit=5)
        self.assertEqual([x["line"] for x in a["mutants"]], [x["line"] for x in b["mutants"]])
        self.assertEqual([x["line"] for x in a["mutants"]], [1, 11, 21, 30, 40])
        self.assertEqual(a["sites"], 40)
        self.assertIsNone(mutants("def (:\n", {1}))


@unittest.skipIf(shutil.which("git") is None, "needs git")
class GraderStrengthTest(unittest.TestCase):
    def _strength(self, test: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), test)
            made = build(repo, Path(tmp) / "out", want=1, checks=1, mutant_limit=20,
                         test_cmd=f"{sys.executable} -m unittest -v {{files}}", python=sys.executable)
            self.assertEqual(len(made["kept"]), 1, made["skipped"])
            again = grader_strength(repo, made["kept"][0], limit=20)
            self.assertEqual({k: again[k] for k in ("tried", "caught", "missed")},
                             {k: made["kept"][0]["grader"][k] for k in ("tried", "caught", "missed")})
            return made["kept"][0]["grader"]

    def test_edge_tests_catch_what_a_middle_value_misses(self):
        weak, strong = self._strength(WEAK), self._strength(STRONG)
        self.assertEqual(weak["tried"], strong["tried"])
        self.assertEqual(weak["caught"] + weak["missed"] + weak["no_verdict"], weak["tried"])
        self.assertGreater(weak["missed"], strong["missed"])
        # the edge tests catch everything a test can: the two they miss change nothing, since at x == lo
        # (or hi) both versions return lo (or hi). A missed change is a gap or a change that does nothing.
        self.assertEqual({m["change"] for m in strong["missed_at"]}, {"Lt → LtE", "Gt → GtE"})
        self.assertGreater(len({(m["line"], m["change"]) for m in weak["missed_at"]}), 2)

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), WEAK)
            made = build(repo, Path(tmp) / "out", want=1, checks=1,
                         test_cmd=f"{sys.executable} -m unittest -v {{files}}", python=sys.executable)
            self.assertNotIn("grader", made["kept"][0])


if __name__ == "__main__":
    unittest.main()
