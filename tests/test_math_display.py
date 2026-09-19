import unittest

from interview_tool.ui import _math_to_unicode


class MathDisplayTests(unittest.TestCase):
    def test_common_latex_commands_become_math_symbols(self):
        rendered = _math_to_unicode(
            r"a_p + \cdots + a_{p+L-1} \equiv 0 \pmod m")

        self.assertIn("⋯", rendered)
        self.assertIn("≡", rendered)
        self.assertIn("mod m", rendered)
        self.assertIn("a[p + L-1]", rendered)
        self.assertNotIn("pmod", rendered)
        self.assertNotIn("\\equiv", rendered)
        self.assertNotIn("\\cdots", rendered)

    def test_fraction_root_relations_and_greek_are_readable(self):
        rendered = _math_to_unicode(
            r"\frac{\alpha + 1}{n} \leq \sqrt{x_2} \to \infty")

        self.assertIn("(α + 1)/(n)", rendered)
        self.assertIn("≤", rendered)
        self.assertIn("√(x₂)", rendered)
        self.assertIn("→ ∞", rendered)

    def test_aligned_markup_is_removed_but_lines_are_preserved(self):
        rendered = _math_to_unicode(
            r"\begin{aligned} x &= 1 \\ y &\equiv 2 \end{aligned}")

        self.assertEqual(rendered, "x = 1\ny ≡ 2")


if __name__ == "__main__":
    unittest.main()
