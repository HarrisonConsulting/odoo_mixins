"""The chrome setting belongs to the reader, including a portal reader."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMarkupLabelPreference(TransactionCase):
    """The chrome setting belongs to the reader, including a portal reader."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal_user = cls.env["res.users"].create({
            "name": "Portal Reader", "login": "markup_portal_reader",
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])],
        })

    def test_icons_are_the_default(self):
        self.assertEqual(self.portal_user.markup_label_style, "icon")

    def test_a_portal_user_may_read_their_own_setting(self):
        user = self.portal_user.with_user(self.portal_user)
        self.assertEqual(user.read(["markup_label_style"])[0]["markup_label_style"], "icon")

    def test_a_portal_user_may_change_their_own_setting(self):
        self.portal_user.with_user(self.portal_user).write({"markup_label_style": "both"})
        self.assertEqual(self.portal_user.markup_label_style, "both")

    def test_the_setting_is_self_accessible_both_ways(self):
        users = self.env["res.users"]
        self.assertIn("markup_label_style", users.SELF_READABLE_FIELDS)
        self.assertIn("markup_label_style", users.SELF_WRITEABLE_FIELDS)
