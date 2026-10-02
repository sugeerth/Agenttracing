import unittest

from text_utils import slugify


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_accents(self):
        self.assertEqual(slugify("Crème brûlée"), "creme-brulee")

    def test_runs_and_ends(self):
        self.assertEqual(slugify("  --a__b  c--  "), "a-b-c")

    def test_cut_at_a_hyphen(self):
        self.assertEqual(slugify("one two three four", max_length=12), "one-two")

    def test_one_long_word(self):
        self.assertEqual(slugify("abcdefghij", max_length=4), "abcd")

    def test_nothing_usable(self):
        self.assertEqual(slugify("!!!"), "n-a")
        self.assertEqual(slugify(""), "n-a")


if __name__ == "__main__":
    unittest.main()
