"""Self-evolving agents in the download: the six demo tasks it carries (`self-evolve --demo`), the folder the
Claude Code hub lists for them, the Evolve page's way in, and a session's step towards one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff import claude_sessions as cs  # noqa: E402
from agentdiff.commands.self_evolve import demo_tasks  # noqa: E402
from agentdiff.hub.catalog import Catalog  # noqa: E402
from agentdiff.selfevolve import DEMO_TASKS  # noqa: E402
from test_claude_sessions import write_session  # noqa: E402

EXAMPLES = ROOT / "demo" / "selfevolve" / "examples"


class DemoTasksTest(unittest.TestCase):
    def test_the_demo_tasks_are_graded_by_tests_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "with space"
            tasks = json.loads(demo_tasks(dest).read_text())["tasks"]
            self.assertEqual([t["id"] for t in tasks], [t for t, _ in DEMO_TASKS])
            pricing = tasks[0]
            self.assertTrue((dest / pricing["workspace"]).is_dir())
            self.assertNotIn(str(dest / "tasks"), pricing["check"], "the grader is not in the workspace")
            # the check runs as written, path with a space and all, and fails on the untouched code
            done = subprocess.run(pricing["check"], shell=True, cwd=dest / pricing["workspace"],
                                  capture_output=True, text=True, timeout=120)
            self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("Ran ", done.stderr)

    def test_demo_without_python_says_why(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(OSError, "Python tests"):
                demo_tasks(Path(tmp))

    def test_demo_writes_where_the_hub_looks(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, AGENTDIFF_HOME=tmp, PYTHONPATH=str(ROOT))
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--demo", "--dry-run"],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(str(Path(tmp) / "self-evolve" / "demo-haiku"), done.stdout)
            self.assertIn("6 task(s)", done.stdout)
            self.assertTrue((Path(tmp) / "self-evolve" / "demo-tasks" / "tasks.json").is_file())

    def test_no_task_and_no_demo_is_an_error(self):
        done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve"], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--demo", done.stderr)


class FolderTest(unittest.TestCase):
    def test_a_folder_outside_the_root_is_listed_and_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, evo = Path(tmp) / "examples", Path(tmp) / "self-evolve"
            (root / "x").mkdir(parents=True)
            src = EXAMPLES / "stand-in-learns-to-check" / "self-evolve.json"
            (root / "x" / "self-evolve.json").write_text(src.read_text())
            (evo / "x").mkdir(parents=True)
            (evo / "x" / "self-evolve.json").write_text(src.read_text())
            cat = Catalog(root, depth=4, limit=50)
            self.assertEqual(len(cat.entries()), 1)
            cat.add_root(evo)
            cat.add_root(root / "x")  # under the root already: nothing to add
            ids = [x.id for x in cat.entries()]
            self.assertEqual(len(set(ids)), 2, "the same folder name in two roots is two runs")
            self.assertTrue(cat.inside(evo / "x" / "self-evolve.json"))
            self.assertFalse(cat.inside(Path(tmp) / "elsewhere"))

    def test_the_examples_say_what_they_are(self):
        cat = Catalog(EXAMPLES, depth=3, limit=50)
        kinds = {x.path.name: (x.summary.get("example") or "").split(":")[0] for x in cat.entries()}
        self.assertEqual(kinds, {"stand-in-learns-to-check": "synthetic", "haiku-on-six-tasks": "recorded"})


class EvolvePageTest(unittest.TestCase):
    def _page(self, entries, datas):
        from agentdiff.hub import views
        from agentdiff.hub.evolvecards import start_card
        start = start_card(prog="/opt/agentdiff", folder="/home/me/.agentdiff/self-evolve", claude=None)
        return views.evolve_page(brand="AgentDiff", user="demo", csrf="x", entries=entries, rivers={}, datas=datas,
                                 start=start)

    def test_the_way_in_leads_until_you_have_your_own(self):
        data = json.loads((EXAMPLES / "stand-in-learns-to-check" / "self-evolve.json").read_text())
        ex = SimpleNamespace(id="a" * 12, title="stand-in · self-evolving harness", summary={"example": "synthetic: x"})
        mine = SimpleNamespace(id="b" * 12, title="demo-haiku · self-evolving harness", summary={})
        only_examples = self._page([ex], {ex.id: data})
        self.assertLess(only_examples.index("Evolve your own agent"), only_examples.index(ex.title))
        self.assertIn("/opt/agentdiff self-evolve --demo", only_examples)
        self.assertIn("/opt/agentdiff fix --evolve 3 -o /home/me/.agentdiff/self-evolve/your-project", only_examples)
        self.assertIn("no <code>claude</code> on PATH", only_examples)
        self.assertIn('<span class="tag" title="synthetic: x">synthetic</span>', only_examples)
        both = self._page([ex, mine], {ex.id: data, mine.id: data})
        self.assertLess(both.index(mine.title), both.index(ex.title), "yours first")
        self.assertGreater(both.index("Evolve your own agent"), both.index(ex.title), "then how to start another")

    def test_a_home_with_a_space_is_quoted(self):
        from agentdiff.hub.evolvecards import start_card
        out = start_card(prog="agentdiff", folder="/Users/A B/.agentdiff/self-evolve", claude="/usr/bin/claude")
        self.assertIn("-o &quot;/Users/A B/.agentdiff/self-evolve/your-project&quot;", out)
        self.assertIn("<code>/usr/bin/claude</code>", out)


class ExportTest(unittest.TestCase):
    def test_an_exported_evolve_page_names_nothing_of_the_machine_it_was_made_on(self):
        from agentdiff.hub.export import export
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "runs", Path(tmp) / "site"
            shutil.copytree(EXAMPLES, root)  # the hub keeps its state under the root it serves
            export(str(root), str(out))
            page = (out / "evolve.html").read_text()
            self.assertIn("Evolve your own agent", page)
            self.assertIn("agentdiff fix --evolve 3 -o ~/.agentdiff/self-evolve/your-project", page)
            self.assertNotIn(tmp, page)
            self.assertNotIn("is on this machine", page)


class SessionStepTest(unittest.TestCase):
    def test_a_session_with_a_check_is_offered_a_harness_to_evolve(self):
        from agentdiff.insight import mitigation
        from agentdiff.timeline import timeline
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_session(base / "projects")
            cs.sync(base / "home", projects=base / "projects")
            data = json.loads(next(p for p in (base / "home").rglob("*.json") if not p.name.startswith(".")).read_text())
            data["source"]["cwd"] = "/work/my shop"
            steps = mitigation(data, timeline(data))["steps"]
            self.assertEqual([s["title"] for s in steps[:2]], ["Give it a check", "Let a harness evolve on it"])
            self.assertEqual(steps[1]["command"], "cd '/work/my shop'\nagentdiff fix --evolve 3 --check 'pytest -q' "
                                                  "-o ~/.agentdiff/self-evolve/shop")


if __name__ == "__main__":
    unittest.main()
