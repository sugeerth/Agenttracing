"""Whether a run on a task from a repository's history reached outside its workspace for the project's own code
(gittasks.reached_outside, retrieval_check, project_names)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.gittasks import project_names, reached_outside, retrieval_check  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

NAMES = ["python-slugify", "slugify"]


def _run(*calls, run="r1"):
    steps = [{"index": i, "type": "tool_call", "name": name, "input": inp, "output": ""} for i, (name, inp) in enumerate(calls)]
    steps.append({"index": len(steps), "type": "answer", "name": "final", "input": "", "output": "done"})
    return Trajectory.from_dict({"schema_version": 1, "trace_id": run, "run_id": run, "agent": {"name": "x"},
                                 "task": {"id": "t", "prompt": "p"}, "outcome": {"success": True, "answer": "done"},
                                 "totals": {}, "steps": steps})


def bash(cmd):
    return ("Bash", {"command": cmd})


class ProjectNamesTest(unittest.TestCase):
    def test_the_repository_and_its_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            for d in ("slugify", "src/extra_pkg", "tests", "docs"):
                (ws / d).mkdir(parents=True)
            for d in ("slugify", "src/extra_pkg", "tests"):
                (ws / d / "__init__.py").write_text("")
            self.assertEqual(project_names("python-slugify", ws), ["extra-pkg", "python-slugify", "slugify"])


class ReachedOutsideTest(unittest.TestCase):
    def test_each_way_out_is_named(self):
        cases = {
            "used git over the network": bash("cd /tmp && git clone https://example.org/other.git"),
            "asked a package index for the project": bash("python3 -m pip download 'python_slugify==8.0.4' -d /tmp/x"),
            "fetched a URL that names the project": ("WebFetch", {"url": "https://pypi.org/project/python-slugify/"}),
            "searched the web for the project": ("WebSearch", {"query": "slugify add_uppercase_char fix"}),
            "read an installed copy of the project": ("Read", {"file_path": "/usr/lib/python3/dist-packages/slugify/special.py"}),
        }
        for kind, call in cases.items():
            self.assertEqual([s["kind"] for s in reached_outside(_run(call), NAMES)], [kind], kind)
        piped = reached_outside(_run(bash("ls && cat /usr/local/lib/python3.11/site-packages/slugify/slugify.py | head")), NAMES)
        self.assertEqual([s["kind"] for s in piped], ["read an installed copy of the project"])
        extras = reached_outside(_run(bash("pip install 'python-slugify[unidecode]>=8'")), NAMES)
        self.assertEqual([s["kind"] for s in extras], ["asked a package index for the project"])

    def test_what_recorded_runs_really_did_is_not_flagged(self):
        """Commands from the recorded python-slugify runs that a plain text search took for a fetch."""
        clean = _run(
            bash("python3 -m pip install -q flake8 pycodestyle mypy 2>&1 | tail -2; python3 -m pycodestyle slugify/slugify.py"),
            bash('python3 -m pip install -q "coverage[toml]>=7" 2>&1 | tail -2; python3 -m coverage run -m pytest tests'),
            bash("python3 -m pip show twine packaging readme_renderer; python3 -m twine check dist/python_slugify-9.1.1.tar.gz"),
            bash("pip install -e . && git diff && git status && grep -rn slugify ."),
            ("Read", {"file_path": "/tmp/agentdiff-work/slugify/special.py"}),
            ("WebSearch", {"query": "how to write a unicode test in python"}))
        self.assertEqual(reached_outside(clean, NAMES), [])

    def test_counted_over_runs_and_reported_only(self):
        runs = [_run(bash("pytest -q"), run="a"), _run(("WebSearch", {"query": "python-slugify changelog"}), run="b"),
                _run(bash("git fetch origin && git log"), run="c")]
        r = retrieval_check(runs, NAMES)
        self.assertEqual((r["runs"], r["flagged"]), (3, 2))
        self.assertEqual([x["run"] for x in r["per_run"]], ["b", "c"])
        self.assertIn("2 of 3 run(s) reached outside the workspace", r["says"])
        self.assertEqual(retrieval_check([], NAMES)["says"], "no runs to check")


if __name__ == "__main__":
    unittest.main()
