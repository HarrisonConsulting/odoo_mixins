"""An assessment carries a number; nothing else does.

Run as a bare member through ``markup_add``, the one way a caller makes a mark,
so the score is proven to be caller content rather than a column only root can
reach. The host is ``res.partner``, made markable by :class:`MarkablePartnerCase`.
"""
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import MarkablePartnerCase

NOTES = "<p>The plan is sound.</p><p>The budget is not.</p>"
PASSAGE = "The budget is not."


@tagged("post_install", "-at_install")
class TestMarkScore(MarkablePartnerCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.member = cls.env["res.users"].create({
            "name": "Scoring Member",
            "login": "markup_mark_score_member",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.host = cls.env["res.partner"].create({"name": "Scored Record", "comment": NOTES})

    def _add(self, values):
        host = self.host.with_user(self.member)
        start = host.markup_text("comment").index(PASSAGE)
        return host.markup_add("comment", start, start + len(PASSAGE), values)

    def test_an_assessment_carries_its_score(self):
        row = self._add({"motivation": "assessing", "score": 0.25, "body": "Thin."})
        self.assertEqual(row["score"], 0.25)
        self.assertEqual(self.env["markup.mark"].browse(row["id"]).score, 0.25)

    def test_a_score_outside_zero_to_one_is_refused(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._add({"motivation": "assessing", "score": 1.5})

    def test_only_an_assessment_carries_a_score(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._add({"motivation": "commenting", "score": 0.5, "body": "Hm."})

    def test_a_mark_that_is_not_an_assessment_reports_no_score(self):
        row = self._add({"motivation": "commenting", "body": "Hm."})
        self.assertIsNone(row["score"])
