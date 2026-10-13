import unittest

from semver import compare


class SemverTest(unittest.TestCase):
    def test_core(self):
        self.assertEqual(compare("1.0.0", "2.0.0"), -1)
        self.assertEqual(compare("2.1.0", "2.0.9"), 1)
        self.assertEqual(compare("1.10.0", "1.9.0"), 1)
        self.assertEqual(compare("1.0.0", "1.0.0"), 0)

    def test_prerelease_chain(self):
        chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
                 "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        for lo, hi in zip(chain, chain[1:]):
            with self.subTest(lo=lo, hi=hi):
                self.assertEqual(compare(lo, hi), -1)
                self.assertEqual(compare(hi, lo), 1)

    def test_build_metadata_is_ignored(self):
        self.assertEqual(compare("1.0.0+20130313144700", "1.0.0+exp.sha.5114f85"), 0)
        self.assertEqual(compare("1.0.0-alpha+001", "1.0.0-alpha"), 0)

    def test_invalid(self):
        for bad in ("1.0", "01.0.0", "1.0.0-", "1.0.0-01", "1.0.0-alpha..1", "v1.0.0", "1.0.0+", "1.0.0-a_b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    compare(bad, "1.0.0")


if __name__ == "__main__":
    unittest.main()
