import json

from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import anchor
from ..tools.redline import html_text
from ..tools.splice import splice_html

# The only keys a caller may set on a mark: what it wants to say, never what
# the mark points at or who said it.
MARK_CLIENT_FIELDS = ("motivation", "body", "editorial_level", "ink", "gesture", "score")


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

    def _markup_check_host_read(self):
        """Whether the caller may reach this record's marks at all.

        Defaults to plain ``check_access("read")`` — the same question every
        other caller's read already answers. A host admits a wider audience
        (a share grant, a token) by overriding ONLY this method, so that
        widening is scoped to the marks transport and never leaks into
        ``read()``, ``web_read``, related-field traversal, or any other RPC
        path a plain ``check_access``/``_check_access`` override would also
        have widened.
        """
        self.check_access("read")

    def _markup_can_comment(self, field_name=None):
        """Whether the caller may add marks. Read access, unless a host says more.

        A suggestion changes nothing until someone with write accepts it, so
        commenting is deliberately cheaper than editing. Hosts with a sharing
        ladder override this to require their own comment grant. ``field_name``,
        when the caller names one, lets a host refuse a passage-specific field
        even where the record as a whole is readable.
        """
        self._markup_check_host_read()
        if field_name and not self._has_field_access(self._fields[field_name], "read"):
            raise AccessError(
                f"{self._name}.{field_name} is not readable to you, so it cannot be marked."
            )
        return True

    def markup_can_comment(self, field_name=None):
        """The same gate asked as a question, for a surface deciding what to offer."""
        self.ensure_one()
        try:
            return bool(self._markup_can_comment(field_name))
        except AccessError:
            return False

    def _markup_can_resolve(self, field_name=None):
        """Whether the caller may accept or reject a mark. Write access, unless a
        host says more. ``field_name`` lets a host refuse resolving a mark on a
        field it does not let this caller write.
        """
        self.check_access("write")
        if field_name and not self._has_field_access(self._fields[field_name], "write"):
            raise AccessError(
                f"{self._name}.{field_name} is not writable by you, so a mark on "
                "it cannot be resolved."
            )
        return True

    def markup_can_resolve(self, field_name=None):
        """The same gate asked as a question, for a surface deciding what to offer."""
        self.ensure_one()
        try:
            return bool(self._markup_can_resolve(field_name))
        except AccessError:
            return False

    # ── reading ──────────────────────────────────────────────────────────────
    def markup_marks(self, field_name=None, states=("open",)):
        """Marks on this record, for whoever may read the record itself."""
        self.ensure_one()
        self._markup_check_host_read()
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
        self._markup_can_comment(field_name)
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
            # What the caller may say is its own content, and only that. Everything
            # naming a record, an author, a state or a parent is the server's, and
            # is applied AFTER the caller's keys so a key added to the wrong dict
            # later cannot quietly become client-writable.
            **{key: values[key] for key in MARK_CLIENT_FIELDS if key in values},
            "res_model": self._name,
            "res_id": self.id,
            "field_name": field_name,
            "version_ref": self._markup_version_ref(field_name),
            # Said here rather than left to the field's default. `sudo()` bypasses
            # access checks without changing who the user is, so the default names
            # the right partner today — but authorship is the one column a reader
            # trusts, and it is written where the rule can be seen instead of
            # inherited from a default a host is free to redefine. The caller is a
            # person: a mark an agent composed is re-signed afterwards, by the one
            # path entitled to claim it.
            "author_id": self.env.user.partner_id.id,
            "author_kind": "human",
            "state": "open",
            **selector,
        }
        mark = self.env["markup.mark"].sudo().create(mark_values)
        return mark.to_dict()[0]

    def markup_drawing(self, field_name, data, mark_id=None, revision=0,
                       mode="inline", bounds=None, session_id=None):
        """Save an editable canvas under the marked host's admission boundary."""
        self.ensure_one()
        self._markup_check_field(field_name)
        self._markup_can_comment(field_name)
        if mode not in ("inline", "redline") or not isinstance(data, dict):
            raise UserError("A drawing needs canvas data and an inline or redline placement.")
        if not isinstance(data.get("layers"), list) or not isinstance(data.get("viewport"), dict):
            raise UserError("The drawing is missing its layers or viewport.")
        viewport = data["viewport"]
        if any(type(viewport.get(key)) not in (int, float) for key in ("tx", "ty", "scale")) or viewport["scale"] <= 0:
            raise UserError("The drawing has an invalid viewport.")
        for layer in data["layers"]:
            if not isinstance(layer, dict) or not isinstance(layer.get("strokes"), list):
                raise UserError("The drawing has an invalid layer.")
            if any(not isinstance(stroke, dict) for stroke in layer["strokes"]):
                raise UserError("The drawing has an invalid stroke.")
        if type(revision) is not int or revision < 0:
            raise UserError("A drawing needs a valid revision.")
        try:
            encoded = json.dumps(data, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise UserError("The drawing contains invalid data.") from error
        if len(encoded.encode()) > 2 * 1024 * 1024:
            raise UserError("This drawing exceeds the 2 MiB markup limit.")
        bounds = bounds or {}
        if not isinstance(bounds, dict) or any(type(bounds.get(key)) not in (int, float) or
               not 0 < bounds[key] < 1000000 for key in ("width", "height")):
            raise UserError("A drawing needs valid region dimensions.")
        scope = bounds.get("scope")
        if scope is not None and (not isinstance(scope, str) or len(scope) > 100):
            raise UserError("A drawing needs a valid region scope.")
        if not isinstance(session_id, str) or len(session_id) > 100:
            raise UserError("A drawing needs a valid markup session.")
        mark = self.env["markup.mark"].sudo().browse(mark_id).exists() if mark_id else None
        if mark_id:
            if not mark or (mark.res_model, mark.res_id, mark.field_name) != (self._name, self.id, field_name):
                raise UserError("That drawing is not on this region.")
            if mark.author_id != self.env.user.partner_id:
                raise AccessError("Only the drawing's author may revise it.")
            # Serialize revision checks as well as writes. Invalidating after the
            # row lock prevents a previously cached value passing a stale check.
            self.env.cr.execute("SELECT id FROM markup_mark WHERE id = %s FOR UPDATE", [mark.id])
            mark.invalidate_recordset(["ink", "state"])
            previous = mark.ink or {}
            if previous.get("schema") != "gdo.markup-canvas/1" or mark.state != "open":
                raise UserError("That drawing is no longer editable.")
            if revision != previous.get("revision"):
                raise UserError("This drawing changed elsewhere. Reload before saving.")
            if mode != previous.get("mode"):
                raise UserError("A drawing's placement cannot change while revising it.")
        else:
            if revision != 0:
                raise UserError("A new drawing starts at revision zero.")
            previous = {}
        ink = {
            "schema": "gdo.markup-canvas/1", "mode": mode,
            "revision": revision + 1, "data": data,
            "bounds": {"width": bounds["width"], "height": bounds["height"], "scope": scope},
            "session": previous.get("session") or session_id,
        }
        if mark:
            mark.write({"ink": ink})
        else:
            text = self.markup_text(field_name)
            created = self.markup_add(field_name, 0, len(text), {
                "motivation": "commenting", "body": "Canvas" if mode == "inline" else "Region redline",
                "ink": ink,
            })
            mark = self.env["markup.mark"].sudo().browse(created["id"])
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
            self._markup_can_resolve(mark.field_name)
        elif mark.author_id != self.env.user.partner_id:
            self._markup_can_resolve(mark.field_name)
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

        Text is spliced by offset. Html is spliced **in the tree**, so the words
        the mark quoted are replaced where they sit and every tag around them
        survives — see :func:`tools.splice.splice_html`. Both are the same edit
        expressed against the same readable text, which is why one method can
        own both.

        A host that keeps versions overrides this to mint one around the write,
        so the change is recorded rather than absorbed — which is the whole
        reason marks never write directly.
        """
        self.ensure_one()
        field_name = mark.field_name
        start, end = mark.start_pos, mark.end_pos
        self.write({field_name: self._markup_edited(field_name, start, end, body or "")})
        overlapped = self.env["markup.mark"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
            ("field_name", "=", field_name), ("state", "=", "open"),
            ("id", "!=", mark.id), ("start_pos", "<", end), ("end_pos", ">", start),
        ])
        overlapped.write({"state": "superseded", "superseded_by_id": mark.id})

    def _markup_edited(self, field_name, start, end, body):
        """The field's new value with ``[start, end)`` of its readable text replaced.

        Computed without writing, so a host can put it in a version, compare it,
        or show it before it commits to it.
        """
        self.ensure_one()
        self._markup_check_field(field_name)
        if self._fields[field_name].type == "html":
            try:
                return splice_html(self[field_name], start, end, body)
            except ValueError as error:
                raise UserError(str(error)) from error
        text = self.markup_text(field_name)
        return text[:start] + body + text[end:]

    # ── asking an agent ──────────────────────────────────────────────────────
    def _markup_can_ask_agent(self):
        """Whether the caller may put a passage to an agent.

        False here, and deliberately so: this module knows nothing about agents,
        and a surface that offered the control anyway would be promising a
        capability the estate might not have granted. The module that owns the
        agents answers this, once, for every host at once.
        """
        return False

    def markup_can_ask_agent(self):
        self.ensure_one()
        try:
            return bool(self._markup_can_ask_agent())
        except AccessError:
            return False

    def markup_ask_agent(self, field_name, start, end, values=None):
        """Mark a passage as a question for an agent.

        The refusal is here, in the calling path, rather than in whatever
        renders the button — a control that is merely hidden is a control that
        is still there.
        """
        self.ensure_one()
        if not self._markup_can_ask_agent():
            raise AccessError(
                "Putting a passage to an agent is not granted on this record."
            )
        values = dict(values or {})
        values["motivation"] = "questioning"
        mark = self.markup_add(field_name, start, end, values)
        self._markup_agent_asked(self.env["markup.mark"].sudo().browse(mark["id"]))
        return mark

    def _markup_agent_asked(self, mark):
        """Tell whoever answers that a passage is waiting. Nothing, by default."""
        return False

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
