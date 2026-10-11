import unittest

from isodur import parse


class DurationTest(unittest.TestCase):
    def test_values(self):
        for text, secs in (("PT1S", 1), ("PT1M", 60), ("PT1H", 3600), ("P1D", 86400), ("P1W", 604800),
                           ("P1M", 2592000), ("P1Y", 31536000), ("P1DT2H30M", 86400 + 7200 + 1800),
                           ("PT0.5S", 0.5), ("PT1.5M", 90), ("P0,5D", 43200), ("PT36H", 129600),
                           ("P1Y2M3DT4H5M6S", 31536000 + 5184000 + 259200 + 14400 + 300 + 6), ("P0D", 0)):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse(text), secs)

    def test_month_and_minute_are_told_apart_by_t(self):
        self.assertEqual(parse("P1M"), 2592000)
        self.assertEqual(parse("PT1M"), 60)

    def test_invalid(self):
        for bad in ("P", "PT", "P1DT", "1D", "p1d", "P1D2H", "PT1S2M", "P1W2D", "P-1D", "-P1D", "P1.5DT2H",
                    "PT1.5H2M", "P1H", "PT1D", "P1D1D", " P1D", "P1DT1H ", "P.5D", "P1..5D", "PTH"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse(bad)
