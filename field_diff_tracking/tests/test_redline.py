"""The redline engine, independent of any model."""
from unittest.mock import patch

from markupsafe import Markup

from odoo.tests import TransactionCase, tagged

from ..tools.redline import WORD_DIFF_BUDGET, redline


@tagged("post_install", "-at_install")
class TestRedline(TransactionCase):
    """The redline engine, independent of any model."""

    def test_word_level_replacement_keeps_the_sentence(self):
        out = redline("Read it out loud if you can.", "Read it aloud if you can.")
        self.assertEqual(
            out,
            Markup('<div class="o_redline o_redline_text"><p>Read it <del>out loud</del>'
                   '<ins>aloud</ins> if you can.</p></div>'),
        )

    def test_unchanged_runs_are_elided_with_context(self):
        old = "\n".join(f"line {n}" for n in range(1, 11))
        new = old.replace("line 1\n", "line one\n").replace("line 10", "line ten")
        out = redline(old, new, "code", context=1)
        self.assertIn("⋯ 6 unchanged", out)
        self.assertIn("line 2", out)
        self.assertIn("line 9", out)
        self.assertNotIn("line 5", out)
        self.assertTrue(out.startswith('<pre class="o_redline o_redline_code">'))

    def test_removed_line_break_reads_as_struck_pilcrow(self):
        self.assertIn("<del>¶</del>", redline("one\ntwo", "one two"))

    def test_html_compares_readable_text_by_block(self):
        out = redline(
            "<h1>Title</h1><p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul>",
            "<h1>Title</h1><p>Hello there <i>world</i></p><ul><li>one</li></ul>",
            "html",
        )
        self.assertIn("Hello <ins>there </ins>world", out)
        self.assertIn("<del>• two</del>", out)
        self.assertNotIn("<b>", out)

    def test_formatting_only_html_change_is_no_redline(self):
        self.assertEqual(redline("<p>same</p>", "<p><b>same</b></p>", "html"), Markup(""))

    def test_content_is_escaped(self):
        out = redline("safe", "<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_oversized_block_falls_back_to_whole_units(self):
        words = int(WORD_DIFF_BUDGET ** 0.5) // 2 + 1
        old = " ".join(["a"] * words)
        new = " ".join(["b"] * words)
        self.assertEqual(redline(old, new).count("<del>"), 1)

    def test_unknown_style_is_refused(self):
        with self.assertRaises(ValueError):
            redline("a", "b", "markdown")
