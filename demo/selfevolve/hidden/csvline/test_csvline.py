import unittest

from csvline import split_csv_line


class CsvTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_csv_line("a,b,c"), ["a", "b", "c"])
        self.assertEqual(split_csv_line(" a , b "), [" a ", " b "])
        self.assertEqual(split_csv_line(""), [""])
        self.assertEqual(split_csv_line("a,,"), ["a", "", ""])

    def test_quoted(self):
        self.assertEqual(split_csv_line('"a,b",c'), ["a,b", "c"])
        self.assertEqual(split_csv_line('"say ""hi""",x'), ['say "hi"', "x"])
        self.assertEqual(split_csv_line('"",x'), ["", "x"])
        self.assertEqual(split_csv_line('a;"b;c"', delimiter=";"), ["a", "b;c"])

    def test_refuses(self):
        for bad in ('"abc', '"a"b,c', 'a,"b"x'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    split_csv_line(bad)


if __name__ == "__main__":
    unittest.main()
