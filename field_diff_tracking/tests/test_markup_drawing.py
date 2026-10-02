"""Drawing saves inherit host admission and compare revisions before writing."""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from .common import MarkablePartnerCase


@tagged("post_install", "-at_install")
class TestMarkupDrawing(MarkablePartnerCase):
    def setUp(self):
        super().setUp()
        self.host = self.env["res.partner"].create({"name": "Canvas host", "comment": "<p>Region</p>"})
        self.data = {"layers": [], "viewport": {"tx": 0, "ty": 0, "scale": 1}}
        self.options = {"bounds": {"width": 400, "height": 100}, "session_id": "test-session"}

    def test_save_and_stale_revision(self):
        """The latest drawing survives a stale tab's attempted overwrite."""
        first = self.host.markup_drawing("comment", self.data, **self.options)
        second = self.host.markup_drawing("comment", self.data, first["id"], 1, **self.options)
        self.assertEqual(second["ink"]["revision"], 2)
        self.assertTrue(second["can_withdraw"])
        with self.assertRaises(UserError):
            self.host.markup_drawing("comment", self.data, first["id"], 1, **self.options)
        self.assertEqual(self.host.markup_marks("comment")[0]["ink"]["revision"], 2)

    def test_foreign_record_and_author_are_refused(self):
        """Knowing a drawing identifier gives no authority to revise it."""
        drawing = self.host.markup_drawing("comment", self.data, **self.options)
        other = self.env["res.partner"].create({"name": "Other", "comment": "Other"})
        with self.assertRaises(UserError):
            other.markup_drawing("comment", self.data, drawing["id"], 1, **self.options)
        user = self.env.ref("base.public_user")
        with self.assertRaises(AccessError):
            self.host.with_user(user).markup_drawing("comment", self.data, drawing["id"], 1, **self.options)
        # An existing second user avoids unrelated signup/federation hooks in
        # full installations. Even an administrator cannot rewrite another author.
        reviewer = self.env.ref("base.user_admin")
        self.assertNotEqual(reviewer, self.env.user)
        self.assertTrue(self.host.with_user(reviewer).markup_can_comment("comment"))
        self.assertFalse(self.host.with_user(reviewer).markup_marks("comment")[0]["can_edit_ink"])
        self.assertFalse(self.host.with_user(reviewer).markup_marks("comment")[0]["can_withdraw"])
        with self.assertRaises(AccessError):
            self.host.with_user(reviewer).markup_drawing("comment", self.data, drawing["id"], 1, **self.options)

    def test_author_withdrawal_preserves_saved_note_and_version(self):
        saved = self.host.markup_add("comment", 0, 6, {"motivation": "commenting", "body": "Keep the context"})
        mark = self.env["markup.mark"].browse(saved["id"])
        version = mark.version_ref
        reviewer = self.env.ref("base.user_admin")
        with patch.object(type(self.host), "_markup_can_resolve", side_effect=AccessError("Host edit denied")) as admission:
            with self.assertRaises(AccessError):
                self.host.with_user(reviewer).markup_resolve(mark.id, "rejected")
            admission.reset_mock()
            result = self.host.markup_resolve(mark.id, "rejected")
            admission.assert_not_called()
        self.assertEqual(result["state"], "rejected")
        self.assertFalse(result["can_withdraw"])
        self.assertTrue(mark.exists())
        self.assertEqual(mark.body, "Keep the context")
        self.assertEqual(mark.version_ref, version)
        self.assertTrue(mark.resolved_on)

    def test_invalid_canvas_is_not_created(self):
        """Malformed data cannot leave an empty or unrenderable saved drawing."""
        with self.assertRaises(UserError):
            self.host.markup_drawing("comment", {"layers": "invalid"}, **self.options)
        with self.assertRaises(UserError):
            self.host.markup_drawing("comment", {**self.data, "layers": [None]}, **self.options)
        with self.assertRaises(UserError):
            self.host.markup_drawing("comment", {**self.data, "groups": "invalid"}, **self.options)
        with self.assertRaises(UserError):
            self.host.markup_drawing("comment", {**self.data, "viewport": {"tx": 0, "ty": 0, "scale": 0}}, **self.options)
        self.assertFalse(self.host.markup_marks("comment"))
