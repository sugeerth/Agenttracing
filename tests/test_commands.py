"""The command layout: ``deepcompare/cli.py`` is the parser and the
dispatch, every command is a module under ``deepcompare/commands/``
exposing ``register`` and ``run``, and the load-run-write shape lives
once in ``commands/_io.py``.

Two kinds of test.  The structural ones pin the surface: the subcommand
list in ``--help`` order, the ``register``/``run`` contract, the
``<name>_cmd`` import alias that keeps a command module from shadowing
the engine function it is named after.  The end-to-end ones run the real
commands through ``python -m deepcompare`` — a surface test cannot
catch a shadowed name; a run can, and that is exactly the mistake this
decomposition risks at every step.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare import cli  # noqa: E402
from deepcompare.commands import _io  # noqa: E402
from deepcompare.commands.paths import DEFAULT_TEMPLATE  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

DEMO = ROOT / "demo" / "traces"
TRAIN = ROOT / "demo" / "rl" / "train"
RL_TRACES = ROOT / "demo" / "rl" / "traces"

#: the subcommands, in --help order; a new command is appended here on purpose
SUBCOMMANDS = [
    "compare", "demo", "batch", "fleet", "gate", "runs", "profile", "progress", "bench",
    "experiments", "narrate", "variance", "cohort", "check", "select", "convert", "frameworks",
    "rl", "evolve", "evolve-compare", "run", "loop", "replay", "rerun", "checkpoint", "context",
    "judge", "why", "db", "hook", "eval", "route", "feedback", "rlexport", "grafana", "watch",
    "explain",
]


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "deepcompare", *args],
                          cwd=str(cwd or ROOT), capture_output=True, text=True)


def subcommand_names(parser) -> list:
    return list(parser._subparsers._group_actions[0].choices)


# ------------------------------------------------------------- the layout

class TestCommandModules(unittest.TestCase):
    def test_every_command_module_exposes_register_and_run(self):
        for module in cli.COMMANDS:
            with self.subTest(module=module.__name__):
                self.assertIsInstance(module, types.ModuleType)
                self.assertTrue(callable(getattr(module, "register", None)), "no register()")
                self.assertTrue(callable(getattr(module, "run", None)), "no run()")
                self.assertIn("register", module.__all__)
                self.assertIn("run", module.__all__)

    def test_registering_every_module_yields_the_pinned_subcommands_in_order(self):
        self.assertEqual(subcommand_names(cli.build_parser()), SUBCOMMANDS)

    def test_one_module_per_command_and_one_command_per_module(self):
        # each module registers exactly one subcommand, and a module's name
        # is that command's (evolve-compare is evolve_compare: a module name)
        import argparse
        for module in cli.COMMANDS:
            with self.subTest(module=module.__name__):
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers(dest="command")
                module.register(sub)
                names = list(sub.choices)
                self.assertEqual(len(names), 1)
                self.assertEqual(names[0].replace("-", "_"), module.__name__.rsplit(".", 1)[1])

    def test_every_subcommand_dispatches_to_its_module_run(self):
        parser = cli.build_parser()
        by_name = {m.__name__.rsplit(".", 1)[1]: m for m in cli.COMMANDS}
        for name, subparser in parser._subparsers._group_actions[0].choices.items():
            with self.subTest(command=name):
                self.assertIs(subparser.get_default("func"), by_name[name.replace("-", "_")].run)

    def test_command_modules_are_imported_under_a_cmd_alias(self):
        # several commands are named after engine functions (compare, run,
        # explain, context, eval); a bare import shadows the function and
        # breaks the next person who imports it beside the modules
        for module in cli.COMMANDS:
            bare = module.__name__.rsplit(".", 1)[1]
            with self.subTest(module=bare):
                self.assertIs(getattr(cli, bare + "_cmd", None), module)
                self.assertNotIsInstance(getattr(cli, bare, None), types.ModuleType)

    def test_the_names_the_tests_import_from_cli_still_exist(self):
        self.assertIs(cli._run_id_from_name, _io.run_id_from_name)
        self.assertIs(cli._with_harness, _io.with_harness)
        self.assertIs(cli._safe_name, _io.safe_name)
        self.assertTrue(callable(cli.main) and callable(cli.build_parser))

    def test_cli_is_the_parser_and_the_dispatch_only(self):
        source = (ROOT / "deepcompare" / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn("def _cmd_", source)
        self.assertNotIn("add_parser(", source.split("def build_parser")[0])
        self.assertLess(len(source.splitlines()), 300)

    def test_no_command_module_imports_the_harness_at_module_level(self):
        import ast
        for path in sorted((ROOT / "deepcompare" / "commands").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "harness" in node.module:
                    self.assertNotEqual(node.col_offset, 0, f"{path.name}: {node.module} at module level")


# ------------------------------------------------------------- _io: loading

class TestLoadTraces(unittest.TestCase):
    def test_a_directory_loads_every_valid_trace_in_file_order(self):
        traces = _io.load_traces(DEMO)
        files = sorted(DEMO.glob("*.json"))
        self.assertEqual(len(traces), len(files))
        self.assertTrue(all(isinstance(t, Trajectory) for t in traces))
        self.assertEqual([t.trace_id for t in traces],
                         [json.loads(p.read_text(encoding="utf-8"))["trace_id"] for p in files])

    def test_the_harness_block_rides_beside_the_typed_trajectory(self):
        for path, t in _io.iter_traces(DEMO):
            harness = json.loads(path.read_text(encoding="utf-8")).get("harness")
            if isinstance(harness, dict):
                self.assertEqual(getattr(t, "harness", None), harness, path.name)
            else:
                self.assertFalse(hasattr(t, "harness"), path.name)

    def test_run_ids_come_from_the_filename_in_the_runs_layout(self):
        loaded = list(_io.iter_traces(TRAIN, run_ids=True))
        self.assertGreater(len(loaded), 1)
        for path, t in loaded:
            self.assertEqual(t.run_id, path.stem.split("__")[2], path.name)

    def test_run_ids_are_left_alone_unless_asked(self):
        for path, t in _io.iter_traces(TRAIN):
            self.assertEqual(t.run_id, json.loads(path.read_text(encoding="utf-8")).get("run_id", "r1"))

    def test_run_id_from_name(self):
        self.assertEqual(_io.run_id_from_name(Path("t01__atlas__r3.json")), "r3")
        self.assertIsNone(_io.run_id_from_name(Path("t01__atlas.json")))
        self.assertIsNone(_io.run_id_from_name(Path("atlas.json")))

    def test_a_list_of_files_loads_those_files(self):
        files = sorted(DEMO.glob("*.json"))[:2]
        self.assertEqual([t.trace_id for t in _io.load_traces(files)],
                         [json.loads(p.read_text(encoding="utf-8"))["trace_id"] for p in files])

    def test_an_invalid_trace_is_reported_through_warn_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = sorted(DEMO.glob("*.json"))[0]
            (Path(tmp) / "a.json").write_text(good.read_text(encoding="utf-8"), encoding="utf-8")
            (Path(tmp) / "b.json").write_text('{"not": "a trace"}', encoding="utf-8")
            warnings = []
            traces = _io.load_traces(tmp, warnings.append)
            self.assertEqual(len(traces), 1)
            self.assertEqual(len(warnings), 1)
            self.assertTrue(warnings[0].startswith("skipping invalid trace: "), warnings[0])

    def test_an_absent_directory_loads_nothing(self):
        self.assertEqual(_io.load_traces(Path("/nonexistent/traces")), [])

    def test_safe_name(self):
        self.assertEqual(_io.safe_name("t01 flight/duration"), "t01_flight_duration")
        self.assertEqual(_io.safe_name("a.b-c_d"), "a.b-c_d")


# ------------------------------------------------------------- _io: writing

class TestWriteOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deepcompare.metrics import aggregate
        from deepcompare.report import compare
        by_task = {}
        for t in _io.load_traces(DEMO):
            by_task.setdefault(t.task.id, {})[t.agent.name] = t
        pairs = [sorted(v.items()) for v in by_task.values() if len(v) == 2][:2]
        cls.reports = [compare(a[1], b[1]) for (a, b) in pairs]
        cls.aggregate = aggregate(cls.reports)

    def test_the_three_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            written = _io.write_outputs(out, self.reports, self.aggregate, DEFAULT_TEMPLATE)
            names = sorted(p.name for p in out.iterdir())
            expected = sorted([f"report_{_io.safe_name(r['task']['id'])}.json" for r in self.reports]
                              + ["aggregate.json", "report.html"])
            self.assertEqual(names, expected)
            self.assertEqual([p.name for p in written["reports"]],
                             [f"report_{_io.safe_name(r['task']['id'])}.json" for r in self.reports])
            self.assertEqual(written["aggregate"].name, "aggregate.json")
            self.assertEqual(written["html"].name, "report.html")
            self.assertIsNone(written["fleet"])
            self.assertEqual(json.loads(written["aggregate"].read_text(encoding="utf-8")), self.aggregate)
            self.assertIn("window.DEEPCOMPARE_DATA", written["html"].read_text(encoding="utf-8"))

    def test_html_off_skips_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = _io.write_outputs(tmp, self.reports, self.aggregate, DEFAULT_TEMPLATE, html=False)
            self.assertIsNone(written["html"])
            self.assertFalse((Path(tmp) / "report.html").exists())

    def test_a_fleet_writes_fleet_json_instead_of_the_pair_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fleet = {"agents": [], "tasks": [], "spotlight_pairs": []}
            written = _io.write_outputs(tmp, self.reports, {}, DEFAULT_TEMPLATE, fleet=fleet)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["fleet.json", "report.html"])
            payload = json.loads(written["fleet"].read_text(encoding="utf-8"))
            self.assertEqual(list(payload), ["fleet", "reports", "aggregate"])
            self.assertEqual(payload["fleet"], fleet)

    def test_a_missing_template_is_a_warning_not_a_failure(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                written = _io.write_outputs(tmp, self.reports, self.aggregate, Path("/nonexistent.html"))
            self.assertIsNone(written["html"])
            self.assertIn("viewer template not found", err.getvalue())
            self.assertTrue((Path(tmp) / "aggregate.json").is_file())


# ---------------------------------------------------------- end to end

class TestEndToEnd(unittest.TestCase):
    """The real commands through ``python -m deepcompare``: exit 0 and the
    artifacts on disk.  This is what catches a shadowed name."""

    def assert_three_artifacts(self, out: Path):
        self.assertTrue((out / "aggregate.json").is_file())
        self.assertTrue((out / "report.html").is_file())
        self.assertTrue(list(out.glob("report_*.json")))

    def test_batch_on_the_demo_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("batch", str(DEMO), "-o", tmp)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assert_three_artifacts(Path(tmp))
            self.assertIn("Agents: A=atlas-v2  B=bolt-v3", result.stdout)
            self.assertIn("Done:", result.stdout)

    def test_runs_on_the_rl_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("runs", str(RL_TRACES), "-o", tmp)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assert_three_artifacts(Path(tmp))
            self.assertIn("Paired inference over", result.stdout)
            self.assertIn("Reliability (repeated runs):", result.stdout)
            agg = json.loads((Path(tmp) / "aggregate.json").read_text(encoding="utf-8"))
            self.assertIn("rl", agg)

    def test_compare_on_one_demo_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            page = Path(tmp) / "report.html"
            result = run_cli("compare", str(DEMO / "t05_flight_duration__atlas-v2.json"),
                             str(DEMO / "t05_flight_duration__bolt-v3.json"),
                             "-o", str(out), "--html", str(page))
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue(out.is_file() and page.is_file())
            self.assertIn("Task: t05_flight_duration", result.stdout)
            self.assertIn("Attribution:", result.stdout)

    def test_help_lists_the_pinned_subcommands_in_order(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{" + ",".join(SUBCOMMANDS) + "}", result.stdout)


if __name__ == "__main__":
    unittest.main()
