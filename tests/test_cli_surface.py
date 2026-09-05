"""Tests for the command-line surface itself (v23).

Not what the commands compute — whether a person can reach them. These are
the failures that make a working tool feel broken: a format the help
advertises but the parser rejects, or usage text naming a command the user
does not have installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare.cli import build_parser
from deepcompare.registry import formats


def run_cli(*args, **kwargs):
    return subprocess.run([sys.executable, "-m", "deepcompare", *args],
                          cwd=str(ROOT), capture_output=True, text=True, **kwargs)


class TestFormatChoices(unittest.TestCase):
    """Everything the registry advertises must be selectable.

    The choice list was hardcoded, so `--list-formats` named `ollama` while
    `--format ollama` was refused — and a third party registering an adapter
    could never select it at all, which defeats the point of a registry.
    """

    def parser_choices(self):
        parser = build_parser()
        for action in parser._subparsers._group_actions[0].choices["convert"]._actions:
            if "--format" in getattr(action, "option_strings", []):
                return set(action.choices)
        self.fail("convert has no --format option")

    def test_every_registered_format_is_selectable(self):
        registered = {entry["name"] for entry in formats()}
        self.assertTrue(registered, "no formats registered")
        self.assertTrue(registered <= self.parser_choices(),
                        f"advertised but unselectable: {registered - self.parser_choices()}")

    def test_auto_is_offered_alongside_the_named_formats(self):
        self.assertIn("auto", self.parser_choices())

    def test_no_choice_is_offered_that_the_registry_cannot_serve(self):
        registered = {entry["name"] for entry in formats()} | {"auto"}
        self.assertEqual(self.parser_choices() - registered, set())

    def test_a_newly_registered_format_becomes_selectable(self):
        # The point of the registry is extension without touching the CLI.
        from deepcompare import registry
        registry.register("probeformat", lambda data: (0.0, "test only"),
                          lambda data: ({}, []), "test-only format")
        try:
            self.assertIn("probeformat", self.parser_choices())
        finally:
            registry._ADAPTERS.pop("probeformat", None)

    def test_list_formats_and_the_parser_agree(self):
        listed = run_cli("convert", "--list-formats")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        for entry in formats():
            self.assertIn(entry["name"], listed.stdout)
            self.assertIn(entry["name"], self.parser_choices())


class TestProgramName(unittest.TestCase):
    """Usage text must name the command the reader actually has."""

    def test_module_invocation_says_python_dash_m(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python -m deepcompare", result.stdout)

    def test_console_script_names_itself(self):
        # Simulate the installed entry point: argv[0] is the script name.
        script = (
            "import sys; sys.argv[0] = '/usr/local/bin/agentdiff';"
            "sys.path.insert(0, %r);"
            "from deepcompare.cli import build_parser;"
            "print(build_parser().format_usage())" % str(ROOT)
        )
        result = subprocess.run([sys.executable, "-c", script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agentdiff", result.stdout)
        self.assertNotIn("python -m deepcompare", result.stdout)


class TestPackaging(unittest.TestCase):
    def test_pyproject_declares_the_console_script(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[project.scripts]", text)
        self.assertIn("deepcompare.cli:main", text)

    def test_the_package_still_has_no_dependencies(self):
        # The zero-dependency property is a feature: it is why the tool runs
        # in an air-gapped CI container without a wheel download.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)


class TestEveryCommandRuns(unittest.TestCase):
    """Each advertised subcommand must at least parse and print help."""

    def test_help_works_for_every_subcommand(self):
        parser = build_parser()
        names = list(parser._subparsers._group_actions[0].choices)
        self.assertGreater(len(names), 5)
        for name in names:
            with self.subTest(command=name):
                result = run_cli(name, "--help")
                self.assertEqual(result.returncode, 0,
                                 f"{name} --help failed: {result.stderr}")


class TestDiagnosisInCompareOutput(unittest.TestCase):
    """The terminal shows the adjudication, not just attribution's story."""

    @classmethod
    def setUpClass(cls):
        traces = ROOT / "demo" / "process" / "traces"
        cls.result = run_cli(
            "compare",
            str(traces / "p01_cancel_booking__steady-v1.json"),
            str(traces / "p01_cancel_booking__hasty-v2.json"))

    def test_diagnosis_section_prints_after_attribution(self):
        out = self.result.stdout
        self.assertIn("Diagnosis (attribution is one hypothesis", out)
        self.assertLess(out.index("Attribution:"),
                        out.index("Diagnosis (attribution is one hypothesis"))

    def test_ranked_hypotheses_and_contradictions_print(self):
        out = self.result.stdout
        self.assertIn("[  leading]", out)
        self.assertIn("grader_or_label", out)
        self.assertIn("! the failed run's answer matched the expected answer",
                      out)

    def test_discriminator_and_confidence_print(self):
        out = self.result.stdout
        self.assertIn("to settle it:", out)
        self.assertIn("confidence:", out)


if __name__ == "__main__":
    unittest.main()
