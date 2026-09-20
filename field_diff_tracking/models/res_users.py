from odoo import fields, models

LABEL_STYLES = [
    ("icon", "Icons only"),
    ("both", "Icons and text"),
    ("text", "Text only"),
]


class ResUsers(models.Model):
    """How this person wants the markup surface labelled.

    One setting, read once and applied to every control — the toolbar, the
    selection menu and the margin actions — so a new control inherits it by
    construction rather than by being remembered. It is readable as well as
    writeable by the user themselves, because the people reading shared
    documents are portal users, and a portal user cannot read a field on their
    own record unless it is listed.
    """

    _inherit = "res.users"

    markup_label_style = fields.Selection(
        LABEL_STYLES, default="icon", required=True,
        help="Whether markup controls show icons, icons with text, or text. "
        "Icons always carry their name as a tooltip and to a screen reader.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["markup_label_style"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["markup_label_style"]
