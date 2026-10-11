import ast
import unittest
from pathlib import Path

import expr
from expr import evaluate


class ExprTest(unittest.TestCase):
    def test_precedence(self):
        for text, v in (("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("10 - 4 - 3", 3), ("2 * 3 % 4", 2),
                        ("100 // 7 // 2", 7), ("7", 7), ("  ( 7 )  ", 7)):
            with self.subTest(text=text):
                self.assertEqual(evaluate(text), v)

    def test_power(self):
        for text, v in (("-2 ** 2", -4), ("(-2) ** 2", 4), ("2 ** 3 ** 2", 512), ("2 ** -1 + 1", None),
                        ("2 * 3 ** 2", 18), ("-3 ** 2 * 2", -18), ("2 ** 2 ** 0", 2), ("2**-0", 1)):
            with self.subTest(text=text):
                if v is None:
                    with self.assertRaises(ValueError):
                        evaluate(text)
                else:
                    self.assertEqual(evaluate(text), v)

    def test_floor_division_and_modulo(self):
        for text, v in (("-7 // 2", -4), ("7 // -2", -4), ("-7 % 2", 1), ("7 % -2", -1), ("-7 % -2", -1),
                        ("7 // 2", 3)):
            with self.subTest(text=text):
                self.assertEqual(evaluate(text), v)

    def test_unary(self):
        for text, v in (("--3", 3), ("-+-3", 3), ("- 3", -3), ("4 - -3", 7), ("4--3", 7), ("-(2 + 3)", -5)):
            with self.subTest(text=text):
                self.assertEqual(evaluate(text), v)

    def test_errors(self):
        for bad in ("", "  ", "1 / 2", "1 // 0", "1 % 0", "(1 + 2", "1 + 2)", "+", "1 +", "2 3", "1.5", "a",
                    "1 *** 2", "()"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evaluate(bad)

    def test_no_eval(self):
        tree = ast.parse(Path(expr.__file__).read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        mods = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse({"eval", "exec", "compile"} & names)
        self.assertNotIn("ast", mods)
