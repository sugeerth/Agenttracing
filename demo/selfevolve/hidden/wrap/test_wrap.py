import unittest

from wrap import wrap


class WrapTest(unittest.TestCase):
    def test_greedy(self):
        self.assertEqual(wrap("the quick brown fox", 10), ["the quick", "brown fox"])
        self.assertEqual(wrap("a  b\nc", 80), ["a b c"])

    def test_paragraphs(self):
        self.assertEqual(wrap("one two\n\nthree", 7), ["one two", "", "three"])
        self.assertEqual(wrap("a\n\n\n\nb", 5), ["a", "", "b"])

    def test_long_words(self):
        self.assertEqual(wrap("abcdefghij xy", 4), ["abcd", "efgh", "ij", "xy"])
        self.assertEqual(wrap("ab abcdefgh", 4), ["ab", "abcd", "efgh"])

    def test_edges(self):
        self.assertEqual(wrap("", 5), [])
        self.assertEqual(wrap("   ", 5), [])
        with self.assertRaises(ValueError):
            wrap("x", 0)


if __name__ == "__main__":
    unittest.main()
