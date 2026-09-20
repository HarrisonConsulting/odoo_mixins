from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import anchor
from ..tools.redline import html_text


class MarkupMixin(models.AbstractModel):
    """Let a record's text be marked: highlighted, noted, questioned, edited.

    Declare which fields may carry marks::

        class Thing(models.Model):
            _inherit = ["markup.mixin"]
            _markup_fields = ("body", "notes")

    **Offsets are taken against** :meth:`markup_text`, never against a field's
    raw value. For an Html field that is the readable text of its blocks, which
    is what a reader sees and what a selector must agree with on both sides of
    the wire.

    Marks are reachable only through this mixin. ``markup.mark`` is closed
    to everyone but the system, and every method here checks access on the host
    record first, so a mark can never carry the text of a record its reader may
    not open. Creating one needs comment-grade access, resolving one needs
    write.
    """

    _name = "markup.mixin"
    _description = "Markable Record"

    _markup_fields = ()

    mark_count = fields.Integer(
        compute="_compute_mark_count",
        help="Open marks on this record's text.",
    )

    def _compute_mark_count(self):
        counts = {}
        if self.ids:
            grouped = self.env["markup.mark"].sudo()._read_group(
                [("res_model", "=", self._name), ("res_id", "in", self.ids),
                 ("state", "=", "open")],
                groupby=["res_id"], aggregates=["__count"],
            )
            counts = {res_id: count for res_id, count in grouped}
        for record in self:
            record.mark_count = counts.get(record.id, 0)

    # ── the text marks point at ──────────────────────────────────────────────
    def markup_text(self, field_name):
        """The text a selector is measured against.

        Html fields resolve to the readable text of their blocks, so a mark made
        on what the reader saw lands on the same characters the server counts.
        """
        self.ensure_one()
        self._markup_check_field(field_name)
        value = self[field_name]
        if self._fields[field_name].type == "html":
            return html_text(value)
        return str(value or "")

    def _markup_check_field(self, field_name):
        if field_name not in self._markup_fields:
            raise UserError(
                f"{self._name}.{field_name} does not carry marks. "
                f"Markable fields: {', '.join(self._markup_fields) or 'none'}."
            )

    def _markup_version_ref(self, field_name):
        """Identity of the version marks are being taken against, if any."""
        return False

    def _markup_can_comment(self):
        """Whether the caller may add marks. Read access, unless a host says more.

        A suggestion changes nothing until someone with write accepts it, so
        commenting is deliberately cheaper than editing. Hosts with a sharing
        ladder override this to require their own comment grant.
        """
        self.check_access("read")
        return True

    def markup_can_comment(self):
        """The same gate asked as a question, for a surface deciding what to offer."""
        self.ensure_one()
        try:
            return bool(self._markup_can_comment())
        except AccessError:
            return False

    # ── reading ──────────────────────────────────────────────────────────────
    def markup_marks(self, field_name=None, states=("open",)):
        """Marks on this record, for whoever may read the record itself."""
        self.ensure_one()
        self.check_access("read")
        domain = [("res_model", "=", self._name), ("res_id", "=", self.id)]
        if field_name:
            self._markup_check_field(field_name)
            domain.append(("field_name", "=", field_name))
        if states:
            domain.append(("state", "in", list(states)))
        return self.env["markup.mark"].sudo().search(domain).to_dict()

    # ── writing ──────────────────────────────────────────────────────────────
    def markup_add(self, field_name, start, end, values=None):
        """Mark a passage, reconciling what the caller saw with what the text says.

        Offsets go stale the moment anyone else writes. A caller that sends the
        quote it was looking at gets the mark placed on **those words**, wherever
        they are now; one whose words are gone is refused rather than silently
        landed on whatever occupies the offsets today.
        """
        self.ensure_one()
        self._markup_check_field(field_name)
        self._markup_can_comment()
        text = self.markup_text(field_name)
        values = dict(values or {})
        seen = {key: values.pop(key, None) for key in ("quote", "prefix", "suffix")}
        selector = anchor.selector(text, start, end)
        if seen["quote"] is not None and seen["quote"] != selector["quote"]:
            placed = anchor.resolve(
                text, seen["quote"], seen["prefix"] or "", seen["suffix"] or "", start
            )
            if not placed:
                raise UserError(
                    "The text moved under this mark and the passage it quoted is gone. "
                    "Reload and mark it again."
                )
            selector = anchor.selector(text, placed[0], placed[1])
        mark_values = {
            "res_model": self._name,
            "res_id": self.id,
            "field_name": field_name,
            "version_ref": self._markup_version_ref(field_name),
            **selector,
            **values,
        }
        mark = self.env["markup.mark"].sudo().create(mark_values)
        return mark.to_dict()[0]

    def markup_resolve(self, mark_id, state, body=None):
        """Accept or reject a mark. Accepting an edit applies it; rejecting never does."""
        self.ensure_one()
        if state not in ("accepted", "rejected"):
            raise UserError("A mark is resolved as accepted or rejected.")
        mark = self.env["markup.mark"].sudo().browse(mark_id)
        if not mark.exists() or mark.res_model != self._name or mark.res_id != self.id:
            raise UserError("That mark is not on this record.")
        if state == "accepted":
            self.check_access("write")
        elif mark.author_id != self.env.user.partner_id:
            self.check_access("write")
        if mark.state != "open":
            raise UserError(f"That mark is already {mark.state}.")

        if state == "accepted" and mark.motivation == "editing":
            self._markup_apply(mark, body if body is not None else mark.body)
        mark.write({
            "state": state,
            "body": body if body is not None else mark.body,
            "resolved_by_id": self.env.user.partner_id.id,
            "resolved_on": fields.Datetime.now(),
        })
        return mark.to_dict()[0]

    def _markup_apply(self, mark, body):
        """Write an accepted edit into the text, and supersede what it overtook.

        The base implementation splices the field. A host that keeps versions
        overrides this to mint one instead, so the change is recorded rather
        than absorbed — which is the whole reason marks never write directly.
        """
        self.ensure_one()
        field_name = mark.field_name
        if self._fields[field_name].type == "html":
            raise UserError(
                "Accepting an edit on an Html field needs a host that can rewrite "
                "its blocks; splicing the readable text would discard the markup."
            )
        text = self.markup_text(field_name)
        start, end = mark.start_pos, mark.end_pos
        self.write({field_name: text[:start] + (body or "") + text[end:]})
        overlapped = self.env["markup.mark"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
            ("field_name", "=", field_name), ("state", "=", "open"),
            ("id", "!=", mark.id), ("start_pos", "<", end), ("end_pos", ">", start),
        ])
        overlapped.write({"state": "superseded", "superseded_by_id": mark.id})

    # ── keeping marks on the passage they were made on ───────────────────────
    def markup_reanchor(self, field_names=None):
        """Re-place every open or orphaned mark against the text as it stands.

        One search answers for the whole recordset, because the common case by
        far is a write that touches many records and marks none of them — a mass
        edit of tasks must not pay a query per record per field to learn there
        was nothing to move.
        """
        if isinstance(field_names, str):
            field_names = [field_names]
        names = list(field_names or self._markup_fields)
        if not self.ids or not names:
            return
        marks = self.env["markup.mark"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "in", self.ids),
            ("field_name", "in", names), ("state", "in", ("open", "orphaned")),
        ])
        grouped = defaultdict(lambda: self.env["markup.mark"].sudo())
        for mark in marks:
            grouped[(mark.res_id, mark.field_name)] |= mark
        for (res_id, name), group in grouped.items():
            group._reanchor(self.browse(res_id).markup_text(name))

    def write(self, vals):
        """Text that moves takes its marks with it."""
        touched = [name for name in self._markup_fields if name in vals]
        result = super().write(vals)
        if touched and not self.env.context.get("markup_skip_reanchor"):
            self.with_context(markup_skip_reanchor=True).markup_reanchor(touched)
        return result
