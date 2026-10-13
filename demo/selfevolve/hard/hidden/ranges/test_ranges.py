import unittest

from ranges import satisfies


class RangeTest(unittest.TestCase):
    def check(self, spec, yes=(), no=()):
        for v in yes:
            with self.subTest(spec=spec, v=v):
                self.assertTrue(satisfies(v, spec))
        for v in no:
            with self.subTest(spec=spec, v=v):
                self.assertFalse(satisfies(v, spec))

    def test_plain_comparators(self):
        self.check("1.2.3", yes=["1.2.3"], no=["1.2.4"])
        self.check(">=1.2.3 <2.0.0", yes=["1.2.3", "1.9.9"], no=["1.2.2", "2.0.0"])
        self.check(">1.0.0 <=1.0.5", yes=["1.0.5"], no=["1.0.0", "1.0.6"])

    def test_caret(self):
        self.check("^1.2.3", yes=["1.2.3", "1.9.0"], no=["1.2.2", "2.0.0"])
        self.check("^0.2.3", yes=["0.2.3", "0.2.9"], no=["0.3.0", "0.2.2"])
        self.check("^0.0.3", yes=["0.0.3"], no=["0.0.4", "0.0.2"])

    def test_tilde_and_x(self):
        self.check("~1.2.3", yes=["1.2.3", "1.2.9"], no=["1.3.0"])
        self.check("~1.2", yes=["1.2.0", "1.2.7"], no=["1.3.0", "1.1.9"])
        self.check("~1", yes=["1.0.0", "1.9.9"], no=["2.0.0"])
        self.check("1.2.x", yes=["1.2.0", "1.2.9"], no=["1.3.0"])
        self.check("1.x", yes=["1.0.0", "1.99.0"], no=["2.0.0", "0.9.9"])
        self.check("*", yes=["0.0.1", "9.9.9"])
        self.check("", yes=["3.1.4"])

    def test_or_sets(self):
        self.check("^1.0.0 || ^3.0.0", yes=["1.5.0", "3.0.1"], no=["2.0.0"])
        self.check("  1.0.0   ||2.0.0 ", yes=["1.0.0", "2.0.0"], no=["1.5.0"])

    def test_prerelease(self):
        self.check("^1.2.3", no=["1.2.4-beta", "1.3.0-alpha"])
        self.check(">=1.2.3-alpha <1.3.0", yes=["1.2.3-beta", "1.2.3", "1.2.5"], no=["1.2.4-beta"])
        self.check(">1.2.3-alpha", yes=["1.2.3-beta"], no=["1.2.3-alpha"])
        self.check("*", no=["1.0.0-rc.1"])

    def test_malformed(self):
        for v, spec in (("1.2", "*"), ("1.2.3", ">=1.2"), ("1.2.3", "^"), ("a.b.c", "*"), ("1.2.3", ">>1.0.0")):
            with self.subTest(v=v, spec=spec):
                with self.assertRaises(ValueError):
                    satisfies(v, spec)
