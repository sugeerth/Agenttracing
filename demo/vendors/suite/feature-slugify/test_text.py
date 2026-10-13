import unittest

from text import slugify


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_accents(self):
        self.assertEqual(slugify("Crème brûlée à la carte"), "creme-brulee-a-la-carte")

    def test_runs_collapse(self):
        self.assertEqual(slugify("  a -- b __ c  "), "a-b-c")

    def test_digits_kept(self):
        self.assertEqual(slugify("Top 10 of 2026"), "top-10-of-2026")

    def test_cut_at_a_word_boundary(self):
        self.assertEqual(slugify("alpha beta gamma delta", max_length=13), "alpha-beta")

    def test_cut_never_ends_in_hyphen(self):
        self.assertEqual(slugify("alpha beta", max_length=6), "alpha")

    def test_empty(self):
        self.assertEqual(slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
