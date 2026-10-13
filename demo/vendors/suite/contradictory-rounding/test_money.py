import unittest

from money import round_units


class RoundingTest(unittest.TestCase):
    # finance asked for halves to round up
    def test_half_rounds_up(self):
        self.assertEqual(round_units(2.5), 3)

    # the ledger team asked for banker's rounding, halves to even
    def test_half_rounds_to_even(self):
        self.assertEqual(round_units(2.5), 2)

    def test_ordinary(self):
        self.assertEqual(round_units(7.2), 7)
        self.assertEqual(round_units(7.8), 8)


if __name__ == "__main__":
    unittest.main()
