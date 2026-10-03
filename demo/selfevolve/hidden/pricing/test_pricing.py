import unittest

from pricing import apply_discount, line_total, order_total


class PricingTest(unittest.TestCase):
    def test_line_total(self):
        self.assertEqual(line_total(2.5, 4), 10.0)

    def test_ten_percent_off(self):
        self.assertEqual(apply_discount(200.0, 10), 180.0)

    def test_no_discount(self):
        self.assertEqual(apply_discount(99.99, 0), 99.99)

    def test_order_total(self):
        self.assertEqual(order_total([(10.0, 3), (5.0, 2)], discount_percent=20), 32.0)


if __name__ == "__main__":
    unittest.main()
