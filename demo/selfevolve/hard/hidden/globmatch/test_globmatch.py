import unittest

from globmatch import match


class GlobTest(unittest.TestCase):
    def test_star_stays_in_one_segment(self):
        self.assertTrue(match("*.py", "a.py"))
        self.assertTrue(match("*.py", ".py"))
        self.assertFalse(match("*.py", "src/a.py"))
        self.assertTrue(match("src/*.py", "src/a.py"))
        self.assertFalse(match("*", "a/b"))

    def test_double_star_crosses_segments(self):
        self.assertTrue(match("**/*.py", "src/pkg/a.py"))
        self.assertTrue(match("src/**", "src/a/b/c"))
        self.assertTrue(match("**", ""))
        self.assertFalse(match("**.txt", "a.py"))

    def test_question_mark(self):
        self.assertTrue(match("a?c", "abc"))
        self.assertFalse(match("a?c", "a/c"))
        self.assertFalse(match("a?c", "ac"))

    def test_classes(self):
        self.assertTrue(match("[abc].txt", "b.txt"))
        self.assertFalse(match("[abc].txt", "d.txt"))
        self.assertTrue(match("[a-c]x", "bx"))
        self.assertTrue(match("[!a-c]x", "dx"))
        self.assertFalse(match("[!a-c]x", "ax"))
        self.assertFalse(match("a[/]b", "a/b"))
        self.assertTrue(match("[]]", "]"))
        self.assertTrue(match("[!]]", "a"))
        self.assertFalse(match("[!]]", "]"))

    def test_escapes_and_literals(self):
        self.assertTrue(match("\\*", "*"))
        self.assertFalse(match("\\*", "a"))
        self.assertTrue(match("a[b", "a[b"))
        self.assertTrue(match("\\?", "?"))
        self.assertFalse(match("A*", "abc"))

    def test_whole_path(self):
        self.assertFalse(match("a", "ab"))
        self.assertTrue(match("a*b*c", "aXbYc"))
        self.assertFalse(match("a*b*c", "aXbY/c"))
