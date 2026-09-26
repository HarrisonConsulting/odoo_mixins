"""The cross-record listing answers only with marks on records the caller may read.

``markup.mark`` is closed to everyone but the system, so a listing across
records is a system read filtered through the caller's own access to each host.
These run as a bare member of one company, against marks on a partner of that
company and on a partner of another — the second is the one that must never
appear, whichever filter or page is asked for.
"""
from odoo.tests import tagged

from ..models.markup_mark import LISTING_CAP
from .common import MarkablePartnerCase


@tagged("post_install", "-at_install")
class TestMarkListing(MarkablePartnerCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.home = cls.env.company
        cls.elsewhere = cls.env["res.company"].create({"name": "Elsewhere Ltd"})
        cls.member = cls.env["res.users"].create({
            "name": "Listing Member",
            "login": "markup_listing_member",
            "company_id": cls.home.id,
            "company_ids": [(6, 0, [cls.home.id])],
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.readable = cls.env["res.partner"].create({
            "name": "Readable Host", "comment": "<p>Seen.</p>", "company_id": cls.home.id,
        })
        cls.hidden = cls.env["res.partner"].create({
            "name": "Hidden Host", "comment": "<p>Unseen.</p>", "company_id": cls.elsewhere.id,
        })
        cls.agent = cls.env["res.partner"].create({"name": "Listing Agent"})
        cls.seen = cls._mark(cls.readable, body="on the readable host")
        cls.settled = cls._mark(cls.readable, body="settled", state="accepted")
        cls.by_agent = cls._mark(cls.readable, body="from an agent", author_kind="agent", author_id=cls.agent.id)
        cls.reply = cls._mark(cls.readable, body="a reply", parent_id=cls.seen.id, motivation="replying")
        cls.unseen = cls._mark(cls.hidden, body="on the hidden host")

    @classmethod
    def _mark(cls, host, **values):
        return cls.env["markup.mark"].sudo().create({
            "res_model": host._name, "res_id": host.id, "field_name": "comment",
            "quote": "", "motivation": "commenting",
            "author_id": cls.env.user.partner_id.id, **values,
        })

    def _listing(self, filters=None, **kwargs):
        return self.env["markup.mark"].with_user(self.member)._readable_listing(filters, **kwargs)

    def test_the_member_cannot_read_the_hidden_host(self):
        """The premise: the rule, not the listing, is what hides the host."""
        self.assertFalse(self.hidden.with_user(self.member).has_access("read"))
        self.assertTrue(self.readable.with_user(self.member).has_access("read"))

    def test_a_mark_on_an_unreadable_host_is_absent(self):
        for phase in (None, "open", "settled"):
            ids = [row["id"] for row in self._listing({"phase": phase} if phase else {}, limit=LISTING_CAP)["rows"]]
            self.assertNotIn(self.unseen.id, ids, phase)
        listing = self._listing({"model": "res.partner", "states": ["open"]})
        self.assertEqual(
            {row["id"] for row in listing["rows"]}, {self.seen.id, self.by_agent.id},
        )
        self.assertEqual(listing["total"], 2)

    def test_the_system_still_sees_both(self):
        """The same listing as root sees the hidden host, so the member's gap is access."""
        ids = {row["id"] for row in self.env["markup.mark"]._readable_listing({"states": ["open"]})["rows"]}
        self.assertIn(self.unseen.id, ids)

    def test_no_author_or_record_label_leaks_from_the_hidden_host(self):
        listing = self._listing()
        self.assertNotIn("Hidden Host", [row["record"] for row in listing["rows"]])
        self.assertEqual({row["res_id"] for row in listing["rows"]}, {self.readable.id})

    def test_a_row_says_where_the_mark_lives(self):
        row = next(row for row in self._listing()["rows"] if row["id"] == self.seen.id)
        self.assertEqual(row["kind"], "text")
        self.assertEqual(row["phase"], "open")
        self.assertEqual(row["record"], "Readable Host")
        self.assertEqual(row["reply_count"], 1)
        self.assertEqual(row["url"], f"/odoo/res.partner/{self.readable.id}?mark={self.seen.id}")

    def test_replies_ride_with_their_thread(self):
        ids = [row["id"] for row in self._listing(limit=LISTING_CAP)["rows"]]
        self.assertNotIn(self.reply.id, ids)

    def test_filters_narrow_by_hand_and_phase(self):
        agent = self._listing({"author_kind": "agent"})
        self.assertEqual([row["id"] for row in agent["rows"]], [self.by_agent.id])
        settled = self._listing({"phase": "settled"})
        self.assertEqual([row["id"] for row in settled["rows"]], [self.settled.id])

    def test_only_markable_models_are_admitted(self):
        """A mark naming a model outside the mixin is never listed, readable or not."""
        country = self.env.ref("base.us")
        stray = self.env["markup.mark"].sudo().create({
            "res_model": "res.country", "res_id": country.id, "field_name": "name",
            "author_id": self.env.user.partner_id.id,
        })
        ids = [row["id"] for row in self._listing(limit=LISTING_CAP)["rows"]]
        self.assertNotIn(stray.id, ids)
        self.assertEqual(self._listing({"model": "res.country"})["total"], 0)

    def test_a_mark_on_a_deleted_host_is_skipped(self):
        gone = self.env["res.partner"].create({"name": "Gone", "company_id": self.home.id})
        orphan = self._mark(gone)
        gone.unlink()
        ids = [row["id"] for row in self._listing(limit=LISTING_CAP)["rows"]]
        self.assertNotIn(orphan.id, ids)

    def test_pages_are_bounded(self):
        first = self._listing(limit=1, offset=0)
        second = self._listing(limit=1, offset=1)
        self.assertEqual(len(first["rows"]), 1)
        self.assertNotEqual(first["rows"][0]["id"], second["rows"][0]["id"])
        self.assertLessEqual(len(self._listing(limit=10 ** 6)["rows"]), LISTING_CAP)

    def test_author_choices_come_only_from_readable_marks(self):
        names = {row["name"] for row in self._listing()["authors"]}
        self.assertIn("Listing Agent", names)
        hidden_author = self.env["res.partner"].create({"name": "Only Elsewhere"})
        self._mark(self.hidden, author_id=hidden_author.id)
        self.assertNotIn("Only Elsewhere", {row["name"] for row in self._listing()["authors"]})
