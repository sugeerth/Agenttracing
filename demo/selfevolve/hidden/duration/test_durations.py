import unittest

from durations import parse_duration


class DurationTest(unittest.TestCase):
    def test_parts(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("2h 15m"), 8100)
        self.assertEqual(parse_duration("1h5s"), 3605)

    def test_decimal_and_case(self):
        self.assertEqual(parse_duration("1.5h"), 5400)
        self.assertEqual(parse_duration("2M"), 120)

    def test_refuses(self):
        for bad in ("", "abc", "5", "1m1h", "1h1h", "h"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_duration(bad)


if __name__ == "__main__":
    unittest.main()
