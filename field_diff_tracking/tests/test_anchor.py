"""Placing a quote again after the text around it moved."""
from odoo.tests import TransactionCase, tagged

from ..tools import anchor

PREAMBLE = (
    "Read it out loud if you can.\n"
    "Argue with it.\n"
    "Cross out what is wrong and write in what you would have said."
)


@tagged("post_install", "-at_install")
class TestAnchor(TransactionCase):
    """Placing a quote again after the text around it moved."""

    def _selector(self, text, phrase):
        start = text.index(phrase)
        return anchor.selector(text, start, start + len(phrase))

    def test_selector_keeps_context_either_side(self):
        sel = self._selector(PREAMBLE, "Argue with it.")
        self.assertEqual(sel["quote"], "Argue with it.")
        self.assertTrue(sel["prefix"].endswith("if you can.\n"))
        self.assertTrue(sel["suffix"].startswith("\nCross out"))

    def test_text_inserted_before_moves_the_mark(self):
        sel = self._selector(PREAMBLE, "Argue with it.")
        moved = "An opening line.\n" + PREAMBLE
        start, end, how = anchor.resolve(moved, **sel)
        self.assertEqual(moved[start:end], "Argue with it.")
        self.assertEqual(how, "unique")

    def test_quote_edited_resolves_fuzzily(self):
        sel = self._selector(PREAMBLE, "Cross out what is wrong")
        edited = PREAMBLE.replace("Cross out what is wrong", "Cross out whatever is wrong")
        placed = anchor.resolve(edited, **sel)
        self.assertIsNotNone(placed)
        self.assertEqual(placed[2], "fuzzy")

    def test_quote_deleted_returns_nothing_rather_than_a_guess(self):
        sel = self._selector(PREAMBLE, "Argue with it.")
        self.assertIsNone(anchor.resolve(PREAMBLE.replace("Argue with it.\n", ""), **sel))

    def test_repeated_phrase_is_told_apart_by_context(self):
        text = "say yes here. say yes there. say yes everywhere."
        second = text.index("say yes there")
        sel = anchor.selector(text, second, second + 7)
        start, _end, _how = anchor.resolve(text, **sel)
        self.assertEqual(start, second)

    def test_insertion_point_follows_its_neighbours(self):
        sel = anchor.selector(PREAMBLE, 29, 29)
        self.assertEqual(sel["quote"], "")
        start, end, _how = anchor.resolve("An opening line.\n" + PREAMBLE, **sel)
        self.assertEqual(start, end)
        self.assertEqual(start, 29 + len("An opening line.\n"))
