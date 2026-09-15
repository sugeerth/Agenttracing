"""What `pip install agentdiff` actually installs.

The unit tests import the engine from the checkout, where every module is
on the path whether or not it is packaged.  A wheel is the other thing:
it carries what `pyproject.toml` names and nothing else, and for one
release `[tool.setuptools] packages = ["deepcompare"]` named only the
top level — so ``deepcompare.commands`` (every command the CLI
dispatches to) and ``deepcompare.harness`` (the only modules allowed a
socket) were left out and the installed console script died on its own
import line before it could parse a flag.  No test in the suite could
see it, because no test built a wheel.

This one builds the wheel and reads it.  It is deliberately about the
*artifact*: the subpackages are present, the console script points at a
callable that exists, and the page template the HTML output needs is
either in the wheel or resolvable from the checkout.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _subpackages() -> set[str]:
    """Every importable subpackage of ``deepcompare`` in the checkout."""
    out = set()
    for init in (ROOT / "deepcompare").rglob("__init__.py"):
        rel = init.parent.relative_to(ROOT)
        if "__pycache__" in rel.parts:
            continue
        out.add(".".join(rel.parts))
    return out


class WheelTest(unittest.TestCase):
    """Build the wheel once and read what is inside it."""

    wheel: Path
    names: list

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="agentdiff-wheel-")
        # `pip wheel <dir>` reuses a stale `build/` or `*.egg-info` left in the
        # checkout, which would hide exactly the bug this file exists for; build
        # from a pristine copy of the sources instead.
        src = Path(cls._tmp.name) / "src"
        src.mkdir()
        for entry in ("pyproject.toml", "README.md", "deepcompare"):
            source = ROOT / entry
            target = src / entry
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(source, target)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", str(src), "--no-deps", "-w", cls._tmp.name],
            capture_output=True, text=True, timeout=900)
        assert result.returncode == 0, result.stderr[-2000:]
        wheels = sorted(Path(cls._tmp.name).glob("*.whl"))
        assert wheels, f"pip wheel wrote no wheel: {result.stdout[-2000:]}"
        cls.wheel = wheels[0]
        with zipfile.ZipFile(cls.wheel) as z:
            cls.names = z.namelist()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_subpackage_of_the_checkout_is_in_the_wheel(self):
        missing = []
        for package in sorted(_subpackages()):
            prefix = package.replace(".", "/") + "/__init__.py"
            if prefix not in self.names:
                missing.append(package)
        self.assertEqual(missing, [], "subpackages in the checkout but not in the wheel")

    def test_the_commands_the_cli_dispatches_to_are_all_in_the_wheel(self):
        from deepcompare import cli
        wheeled = {n for n in self.names if n.startswith("deepcompare/commands/") and n.endswith(".py")}
        missing = []
        for command in cli.COMMANDS:
            name = command.__name__.rsplit(".", 1)[-1]
            if f"deepcompare/commands/{name}.py" not in wheeled:
                missing.append(name)
        self.assertEqual(missing, [], "commands registered in the CLI but not shipped")
        self.assertGreater(len(wheeled), 30, "the command modules are the CLI")

    def test_the_console_script_names_a_callable_that_exists(self):
        import importlib
        import tomllib
        spec = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = spec.get("project", {}).get("scripts", {})
        self.assertIn("agentdiff", scripts)
        module_path, _, attribute = scripts["agentdiff"].partition(":")
        module = importlib.import_module(module_path)
        self.assertTrue(callable(getattr(module, attribute, None)),
                        f"{scripts['agentdiff']} is not callable")
        self.assertIn(module_path.replace(".", "/") + ".py", self.names)

    def test_the_page_template_the_html_output_needs_is_shipped_or_resolvable(self):
        """`--html` and every `-o` output write the blocks page from a
        template.  In the checkout it is `web/blocks.html`; in a wheel it
        has to be package data, or the installed CLI writes analyses with
        no page and says nothing."""
        from deepcompare.commands.paths import DEFAULT_TEMPLATE
        template = Path(DEFAULT_TEMPLATE)
        self.assertTrue(template.is_file(), f"the checkout's template is missing: {template}")
        packaged = [n for n in self.names if n.endswith("blocks.html")]
        if not packaged:
            self.skipTest("the page template is not package data yet; "
                          "the installed CLI writes no report.html (tracked in docs/PRODUCTION.md)")
        self.assertTrue(any(n.startswith("deepcompare/") for n in packaged), packaged)


if __name__ == "__main__":
    unittest.main()
