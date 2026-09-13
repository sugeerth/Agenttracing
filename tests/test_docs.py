"""The docs stay short, current and consistent with each other."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestDocs(unittest.TestCase):
    def test_the_readme_is_a_product_page_not_a_lab_notebook(self):
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 200, f"README is {len(lines)} lines")
        text = "\n".join(lines)
        self.assertLess(text.index("## Quick start"), text.index("## The loop"))
        self.assertIn("agentdiff demo", text)

    def test_every_readme_scorecard_number_is_in_the_benchmark_doc(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        bench = (ROOT / "docs" / "BENCHMARK.md").read_text(encoding="utf-8")
        table = readme[readme.index("## Is the diagnoser any good"):readme.index("## Repository layout")]
        numbers = set(re.findall(r"\d+/\d+|\d\.\d{2,3}|[+−-]\d\.\d\d", table))
        self.assertTrue(numbers)
        for number in numbers:
            self.assertIn(number, bench, f"README scorecard number {number} is not in docs/BENCHMARK.md")

    def test_the_schema_is_a_contract_and_the_changelog_is_the_history(self):
        schema = (ROOT / "SCHEMA.md").read_text(encoding="utf-8")
        changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertNotRegex(schema, r"^## .*\(v\d+\)", "versioned sections belong in docs/CHANGELOG.md")
        self.assertIn("## Trajectory", schema)
        self.assertIn("## CLI commands", schema)
        self.assertIn("(v41)", changelog)
        self.assertIn("(v27)", changelog)
        self.assertLess(len(schema.splitlines()), 300)

    def test_the_external_validation_reports_its_floors(self):
        bench = (ROOT / "docs" / "BENCHMARK.md").read_text(encoding="utf-8")
        self.assertIn("always the first step", bench)
        self.assertIn("negative", bench)
        self.assertIn("8/184", bench)


if __name__ == "__main__":
    unittest.main()
