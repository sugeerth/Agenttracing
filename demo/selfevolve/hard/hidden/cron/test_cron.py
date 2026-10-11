import unittest
from datetime import datetime

from cron import next_run


def at(*a):
    return datetime(*a)


class CronTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(next_run("* * * * *", at(2026, 1, 1, 10, 0, 30)), at(2026, 1, 1, 10, 1))
        self.assertEqual(next_run("30 9 * * *", at(2026, 1, 1, 9, 30)), at(2026, 1, 2, 9, 30))
        self.assertEqual(next_run("30 9 * * *", at(2026, 1, 1, 9, 29, 59)), at(2026, 1, 1, 9, 30))

    def test_steps_ranges_lists(self):
        self.assertEqual(next_run("*/15 * * * *", at(2026, 3, 1, 10, 7)), at(2026, 3, 1, 10, 15))
        self.assertEqual(next_run("0 9-17/4 * * *", at(2026, 3, 1, 14, 0)), at(2026, 3, 1, 17, 0))
        self.assertEqual(next_run("5,10 * * * *", at(2026, 3, 1, 10, 10)), at(2026, 3, 1, 11, 5))

    def test_names(self):
        # 2026-03-06 is a Friday
        self.assertEqual(next_run("0 8 * * MON-FRI", at(2026, 3, 6, 9, 0)), at(2026, 3, 9, 8, 0))
        self.assertEqual(next_run("0 0 1 jan *", at(2026, 3, 1)), at(2027, 1, 1))
        self.assertEqual(next_run("0 0 * * 7", at(2026, 3, 6)), at(2026, 3, 8))

    def test_day_of_month_or_day_of_week(self):
        # 2026-03-06 is a Friday; the next 13th is Friday 2026-03-13
        self.assertEqual(next_run("0 0 13 * FRI", at(2026, 3, 6, 12)), at(2026, 3, 13))
        self.assertEqual(next_run("0 0 13 * MON", at(2026, 3, 6, 12)), at(2026, 3, 9))
        self.assertEqual(next_run("0 0 13 * *", at(2026, 3, 6, 12)), at(2026, 3, 13))
        self.assertEqual(next_run("0 0 * * MON", at(2026, 3, 10, 12)), at(2026, 3, 16))

    def test_rare_days(self):
        self.assertEqual(next_run("0 0 29 2 *", at(2026, 3, 1)), at(2028, 2, 29))
        self.assertEqual(next_run("0 0 31 * *", at(2026, 4, 1)), at(2026, 5, 31))

    def test_malformed(self):
        for bad in ("* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *",
                    "5-1 * * * *", "a * * * *", "* * * FOO *", "*  * * * *"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    next_run(bad, at(2026, 1, 1))
