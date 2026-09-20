from markupsafe import Markup

from odoo import api, models

from ..tools.redline import STYLES, redline


class DiffTrackingMixin(models.AbstractModel):
    """Post a redline to the chatter whenever a ``track_diff`` field changes.

    Declare the fields and their styles on the model::

        class Thing(models.Model):
            _inherit = ["diff.tracking.mixin"]
            _diff_tracked = {
                "notes": "text",     # prose, compared word by word
                "body": "html",      # the readable text of each block
                "source": "code",    # monospace lines
            }

    One message is posted per record per write, holding a redline for every
    tracked field that changed. The comparison runs on the stored (sanitised)
    values, and a field declaring both ``tracking`` and ``track_diff`` is
    reported once, by its redline.
    """

    _name = "diff.tracking.mixin"
    _inherit = ["mail.thread"]
    _description = "Redline Tracking"

    _diff_tracked = {}

    @api.model
    def _diff_tracked_fields(self):
        """Tracked field names mapped to their redline style."""
        tracked = {}
        for name, style in self._diff_tracked.items():
            if name not in self._fields:
                raise ValueError(f"{self._name}._diff_tracked names an unknown field {name!r}")
            if style not in STYLES:
                raise ValueError(f"{self._name}._diff_tracked[{name!r}] must be one of {STYLES}")
            tracked[name] = style
        return tracked

    def _track_get_fields(self):
        return super()._track_get_fields() - set(self._diff_tracked_fields())

    def write(self, vals):
        styles = self._diff_tracked_fields()
        names = [name for name in styles if name in vals]
        if not names or self.env.context.get("tracking_disable") or self.env.context.get("mail_notrack"):
            return super().write(vals)
        before = {record.id: {name: record[name] for name in names} for record in self}
        result = super().write(vals)
        for record in self:
            record._post_redlines(before[record.id], styles)
        return result

    def _post_redlines(self, before, styles):
        self.ensure_one()
        sections, labels = [], []
        for name, old_value in before.items():
            body = redline(old_value, self[name], styles[name])
            if body:
                label = self._fields[name]._description_string(self.env)
                labels.append(label)
                sections.append(Markup('<p class="o_redline_field"><b>%s</b></p>%s') % (label, body))
        if sections:
            self.message_post(
                body=Markup("").join(sections),
                subject=", ".join(labels),
                subtype_xmlid="mail.mt_note",
            )
