"""Drawing saves inherit host admission and compare revisions before writing."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
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
        reviewer = new_test_user(self.env, login="markup-drawing-reviewer")
        self.assertTrue(self.host.with_user(reviewer).markup_can_comment("comment"))
        self.assertFalse(self.host.with_user(reviewer).markup_marks("comment")[0]["can_edit_ink"])
        with self.assertRaises(AccessError):
            self.host.with_user(reviewer).markup_drawing("comment", self.data, drawing["id"], 1, **self.options)

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
