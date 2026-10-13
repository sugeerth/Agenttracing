import unittest

from scheduler import next_runs, runs_per_day


class SchedulerTest(unittest.TestCase):
    def test_next_runs(self):
        self.assertEqual(next_runs(1000, {"backup": "1h30m", "ping": "90s"}),
                         {"backup": 1000 + 5400, "ping": 1000 + 90})

    def test_runs_per_day(self):
        self.assertEqual(runs_per_day("15m"), 96)
        self.assertEqual(runs_per_day("2h"), 12)

    def test_zero(self):
        self.assertEqual(runs_per_day("0m"), 0)


if __name__ == "__main__":
    unittest.main()
