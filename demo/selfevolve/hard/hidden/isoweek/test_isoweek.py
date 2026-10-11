import ast
import unittest
from datetime import date, timedelta
from pathlib import Path

import isoweek
from isoweek import from_iso, iso_week


class WeekTest(unittest.TestCase):
    def test_known(self):
        for d, w in (((2026, 1, 1), (2026, 1, 4)), ((2027, 1, 1), (2026, 53, 5)), ((2024, 12, 30), (2025, 1, 1)),
                     ((2021, 1, 3), (2020, 53, 7)), ((2020, 12, 31), (2020, 53, 4)), ((2016, 1, 3), (2015, 53, 7)),
                     ((2000, 2, 29), (2000, 9, 2))):
            with self.subTest(d=d):
                self.assertEqual(iso_week(*d), w)
                self.assertEqual(from_iso(*w), d)

    def test_every_day_of_four_centuries_edges(self):
        for start in (date(1999, 12, 20), date(2004, 12, 20), date(2099, 12, 20), date(2100, 2, 20)):
            for k in range(30):
                d = start + timedelta(days=k)
                with self.subTest(d=d):
                    self.assertEqual(iso_week(d.year, d.month, d.day), tuple(d.isocalendar()))
                    self.assertEqual(from_iso(*d.isocalendar()), (d.year, d.month, d.day))

    def test_invalid(self):
        for bad in ((2026, 2, 29), (2026, 13, 1), (2026, 1, 0), (2100, 2, 29), (2026, 4, 31)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    iso_week(*bad)
        for bad in ((2025, 53, 1), (2026, 0, 1), (2026, 1, 8), (2026, 1, 0)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    from_iso(*bad)

    def test_by_hand(self):
        tree = ast.parse(Path(isoweek.__file__).read_text())
        mods = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse({"datetime", "calendar", "time"} & mods)
