"""What a caller may say about a mark, and what only the server may say.

A mark is created as the superuser so it can be written on a record its author
may read but not write. That makes every column on it reachable unless the call
says otherwise, so these assert each forbidden key is IGNORED rather than
honoured — one test per key, because a single "a mark was created" would pass
against every one of them.

They run as a bare member. A suite that builds its fixtures as the superuser
cannot see this class of defect at all: root passes every check the guard is
there to impose.

The host is ``res.partner``, made markable by :class:`MarkablePartnerCase`.
"""
from odoo.tests import tagged
from odoo.exceptions import AccessError, UserError

from .common import MarkablePartnerCase

NOTES = "<p>Read it out loud if you can.</p><p>Argue with it.</p>"
PASSAGE = "Argue with it."


@tagged("post_install", "-at_install")
class TestMarkCreateGuard(MarkablePartnerCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.member = cls.env["res.users"].create({
            "name": "Bare Member",
            "login": "markup_mixin_guard_member",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.host = cls.env["res.partner"].create({
            "name": "Marked Record", "comment": NOTES,
        })
        cls.victim = cls.env["res.partner"].create({"name": "Someone Else"})

    def _add(self, values):
        """Mark the passage as a bare member, and read the row back as root."""
        host = self.host.with_user(self.member)
        text = host.markup_text("comment")
        start = text.index(PASSAGE)
        mark_id = host.markup_add(
            "comment", start, start + len(PASSAGE), values,
        )["id"]
        return self.env["markup.mark"].sudo().browse(mark_id)

    def test_a_caller_cannot_point_a_mark_at_another_record(self):
        """The record a mark lands on is the one whose access was checked."""
        mark = self._add({
            "res_model": "res.users", "res_id": self.victim.id,
            "field_name": "name", "body": "not here",
        })
        self.assertEqual(mark.res_model, "res.partner")
        self.assertEqual(mark.res_id, self.host.id)
        self.assertEqual(mark.field_name, "comment")

    def test_a_caller_cannot_forge_who_spoke(self):
        """Provenance a caller may set is not provenance."""
        mark = self._add({"author_id": self.victim.id, "author_kind": "agent"})
        self.assertEqual(mark.author_id, self.member.partner_id)
        self.assertEqual(mark.author_kind, "human")

    def test_a_mark_is_signed_by_the_member_who_made_it(self):
        """Creating as root bypasses the checks, not the identity.

        ``sudo()`` does not change the current user, so this passes on the
        unguarded code too — it is here to hold the column to the real caller
        whichever way ``author_id`` comes to be set.
        """
        mark = self._add({"body": "mine"})
        self.assertEqual(mark.author_id, self.member.partner_id)
        self.assertNotEqual(mark.author_id, self.env.ref("base.partner_root"))

    def test_a_caller_cannot_land_a_mark_already_resolved(self):
        """A mark arrives open, so the history cannot be written pre-settled."""
        mark = self._add({"state": "accepted", "motivation": "editing", "body": "x"})
        self.assertEqual(mark.state, "open")

    def test_a_caller_cannot_graft_a_mark_onto_another_thread(self):
        """Replies and supersession are the server's to record."""
        parent = self._add({"body": "root"})
        mark = self._add({"parent_id": parent.id, "superseded_by_id": parent.id})
        self.assertFalse(mark.parent_id)
        self.assertFalse(mark.superseded_by_id)

    def test_reply_keeps_the_admitted_parent_reading_and_human_author(self):
        parent = self._add({'body': 'Explain this.'})
        self.host.comment = '<p>The original passage has been replaced.</p>'
        reply_id = self.host.with_user(self.member).markup_add('comment', 0, 1, {
            'motivation': 'replying', 'parent_id': parent.id, 'body': 'Here is why.',
            'quote': 'forged quote', 'author_id': self.victim.id, 'state': 'accepted',
        })['id']
        reply = self.env['markup.mark'].sudo().browse(reply_id)
        self.assertEqual(reply.parent_id, parent)
        self.assertEqual(reply.quote, parent.quote)
        self.assertEqual(reply.start_pos, parent.start_pos)
        self.assertEqual(reply.author_id, self.member.partner_id)
        self.assertEqual(reply.state, 'open')
        self.assertEqual(self.host.comment, '<p>The original passage has been replaced.</p>')

    def test_reply_cannot_cross_record_or_field_or_nest_under_a_reply(self):
        parent = self._add({'body': 'Original question'})
        for key, value in [('res_id', self.victim.id), ('field_name', 'name')]:
            foreign = self._add({'body': 'Another passage'})
            foreign.write({key: value})
            with self.assertRaises(UserError):
                self._add({'motivation': 'replying', 'parent_id': foreign.id, 'body': 'No'})
        reply = self._add({'motivation': 'replying', 'parent_id': parent.id, 'body': 'A reply'})
        with self.assertRaises(UserError):
            self._add({'motivation': 'replying', 'parent_id': reply.id, 'body': 'Nested'})

    def test_reply_requires_an_admitted_host_a_parent_and_a_message(self):
        parent = self._add({'body': 'Question'})
        for values in [{'parent_id': parent.id, 'body': ' '}, {'body': 'No parent'},
                       {'parent_id': True, 'body': 'Invalid'}, {'parent_id': -1, 'body': 'Invalid'}]:
            with self.assertRaises(UserError):
                self._add({'motivation': 'replying', **values})
        with self.assertRaises(AccessError):
            self.host.with_user(self.env.ref('base.public_user')).markup_add('comment', 0, 1, {
                'motivation': 'replying', 'parent_id': parent.id, 'body': 'Not admitted',
            })

    def test_a_caller_cannot_place_a_mark_off_the_passage_it_marked(self):
        """Offsets are measured against the text the server read.

        ``quote`` is deliberately NOT one of these. A caller sends the words it
        was looking at and the server re-places the mark on those words if the
        text moved underneath — that is the anchoring contract, and it is why
        ``quote`` is taken out of ``values`` before the guard ever sees it.
        Offsets carry no such warrant.
        """
        expected = self.host.markup_text("comment").index(PASSAGE)
        mark = self._add({"start_pos": 0, "end_pos": 1})
        self.assertEqual(mark.start_pos, expected)
        self.assertEqual(mark.end_pos, expected + len(PASSAGE))
        self.assertEqual(mark.quote, PASSAGE)

    def test_the_content_a_caller_owns_still_lands(self):
        """The guard is a whitelist, not a wall: what a caller means is heard."""
        mark = self._add({
            "motivation": "questioning", "body": "Why this?",
            "editorial_level": "developmental", "gesture": "circle",
            "ink": {"stroke": [[0, 0], [1, 1]]},
        })
        self.assertEqual(mark.motivation, "questioning")
        self.assertEqual(mark.body, "Why this?")
        self.assertEqual(mark.editorial_level, "developmental")
        self.assertEqual(mark.gesture, "circle")
        self.assertEqual(mark.ink, {"stroke": [[0, 0], [1, 1]]})
