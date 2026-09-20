from odoo.tests import TransactionCase, tagged

from ..tools.redline import html_text
from ..tools.splice import splice_html


def _readable(text):
    """What ``html_text`` would return for a readable text spliced as a string.

    The comparison the whole feature rests on is that splicing the TREE and
    splicing the TEXT agree about what the text now says. Empty blocks drop and
    whitespace collapses on the way back, so the expectation is put through the
    same reduction rather than compared raw.
    """
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


@tagged("post_install", "-at_install")
class TestSpliceHtml(TransactionCase):
    """Accepting an edit on HTML rewrites the words and keeps the markup.

    Every case here is stated twice: what the readable text becomes, which is
    what every mark's offsets are measured against, and what survived in the
    tree, which is what the reader actually sees. A splice that gets the first
    right and the second wrong is the failure this replaces — it reads as a
    correct edit right up until someone notices the document lost its lists.
    """

    def _splice(self, value, quote, replacement, expected=None):
        """Splice out ``quote`` and check what the text became.

        With no ``expected``, the check is that splicing the TREE said the same
        thing as splicing the readable text as a string. That equivalence holds
        only while the range stays inside one block: a string splice across the
        newline between two blocks joins them, and the tree splice does not,
        because a block boundary is structure and deleting words either side of
        it is not an instruction to remove it. Those cases state their answer.
        """
        text = html_text(value)
        start = text.find(quote)
        self.assertNotEqual(start, -1, f"{quote!r} is not in {text!r}")
        result = splice_html(value, start, start + len(quote), replacement)
        if expected is None:
            expected = _readable(text[:start] + replacement + text[start + len(quote):])
        self.assertEqual(html_text(result), expected)
        return str(result)

    def test_a_word_is_replaced_where_it_sits(self):
        """The simplest edit, and the shape of every other one."""
        result = self._splice("<p>Read it out loud.</p>", "loud", "aloud")
        self.assertEqual(result, "<p>Read it out aloud.</p>")

    def test_the_markup_around_an_edit_survives_it(self):
        """A replacement that spans a tag keeps the tag."""
        result = self._splice("<p>The <b>quick</b> brown fox.</p>", "quick brown", "slow grey")
        self.assertIn("<b>", result)
        self.assertIn("fox.", result)

    def test_a_list_stays_a_list(self):
        result = self._splice("<ul><li>one</li><li>two</li></ul>", "two", "three")
        self.assertIn("<ul>", result)
        self.assertIn("<li>one</li>", result)
        self.assertIn("three", result)

    def test_an_untouched_block_is_untouched(self):
        """The paragraph the edit did not reach comes back byte for byte."""
        value = "<h2>Title</h2><p>First para.</p><p>Second para.</p>"
        result = self._splice(value, "Second", "Third")
        self.assertIn("<h2>Title</h2>", result)
        self.assertIn("<p>First para.</p>", result)

    def test_an_edit_spanning_two_blocks_keeps_both_blocks(self):
        """The words go; the paragraph break they straddled does not.

        A string splice would join the two paragraphs here, which is the one
        place tree and text legitimately part company — and the tree is right.
        Accepting an edit is a change of words, never a change of structure.
        """
        value = "<p>spanning blocks</p><p>second para</p>"
        result = self._splice(value, "blocks\nsecond", "merged", "spanning merged\npara")
        self.assertEqual(result.count("<p"), 2)

    def test_a_deletion_leaves_the_block_standing(self):
        """An emptied block contributes no line, so tree and text still agree."""
        value = "<p>gone</p><p>kept</p>"
        result = self._splice(value, "gone", "", "kept")
        self.assertIn("kept", result)

    def test_an_insertion_point_writes_between_words(self):
        value = "<p>hello world</p>"
        result = splice_html(value, 5, 5, " brave")
        self.assertEqual(html_text(result), "hello brave world")

    def test_an_emptied_inline_tag_is_dropped_not_left_hollow(self):
        """Deleting everything a ``<b>`` held takes the ``<b>`` with it."""
        result = self._splice("<p>Keep <b>this</b> and drop.</p>", "this", "")
        self.assertNotIn("<b>", result)
        self.assertIn("Keep", result)

    def test_a_break_separates_blocks_like_the_server_counts_them(self):
        result = self._splice("<p>a<br>b</p>", "b", "c")
        self.assertEqual(html_text(result), "a\nc")

    def test_collapsed_whitespace_is_edited_where_the_reader_saw_it(self):
        """Three spaces read as one, and the mark on that one is a real place."""
        value = "<p>alpha</p>\n  <p>beta   gamma</p>"
        result = self._splice(value, "beta gamma", "delta")
        self.assertEqual(html_text(result), "alpha\ndelta")

    def test_an_empty_value_becomes_a_paragraph(self):
        """There is no tree to keep, so the edit makes the smallest one."""
        self.assertEqual(html_text(splice_html("", 0, 0, "new")), "new")
        self.assertEqual(html_text(splice_html(False, 0, 0, "new")), "new")

    def test_the_replacement_is_text_not_markup(self):
        """A suggested edit proposes words; it never injects a tag."""
        result = splice_html("<p>safe</p>", 0, 4, "<script>x</script>")
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)
