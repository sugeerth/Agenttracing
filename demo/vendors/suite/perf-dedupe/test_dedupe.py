import time
import unittest

from dedupe import unique_in_order


class DedupeTest(unittest.TestCase):
    def test_order_kept(self):
        self.assertEqual(unique_in_order([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_strings(self):
        self.assertEqual(unique_in_order(["b", "a", "b"]), ["b", "a"])

    def test_empty(self):
        self.assertEqual(unique_in_order([]), [])

    def test_fast_on_a_real_log(self):
        items = [i % 15000 for i in range(60000)]
        start = time.perf_counter()
        result = unique_in_order(items)
        elapsed = time.perf_counter() - start
        self.assertEqual(result, list(range(15000)))
        self.assertLess(elapsed, 0.5, f"took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
