from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..tools import anchor

MOTIVATIONS = [
    ("highlighting", "Highlight"),
    ("commenting", "Note"),
    ("questioning", "Question"),
    ("editing", "Suggested edit"),
    ("assessing", "Assessment"),
    ("replying", "Reply"),
]


class MarkupMark(models.Model):
    """One mark on a passage of a record's text.

    A highlight, a margin note, a question, a suggested edit and a pen stroke
    are this record with different intent, so the margin, the redline and the
    history are three readings of one table rather than three features.

    A mark never changes the text it points at. An editing mark carries the
    replacement it proposes; applying it is the host record's act, and mints
    whatever version that host keeps.

    Marks are reachable only through the record they mark — ``markup.mixin``
    checks access on the host and reads them ``sudo``, so a mark cannot leak the
    text of a record its reader may not open.
    """

    _name = "markup.mark"
    _description = "Markup Mark"
    _order = "res_model, res_id, start_pos, id"

    _check_positions_ordered = models.Constraint(
        "CHECK (start_pos >= 0 AND end_pos >= start_pos)",
        "A mark ends where it starts or after it, never before.",
    )

    # ── what it marks ────────────────────────────────────────────────────────
    res_model = fields.Char(
        string="Model", required=True, index=True,
        help="Model of the record this mark is on.",
    )
    res_id = fields.Many2oneReference(
        string="Record", model_field="res_model", required=True, index=True,
        help="Record this mark is on.",
    )
    field_name = fields.Char(
        required=True,
        help="Field of that record whose text this mark points at.",
    )
    version_ref = fields.Char(
        help="Identity of the version the selector was taken against, where the "
        "host keeps versions. A mark belongs to a version, so re-anchoring onto "
        "a later one is deliberate rather than silent.",
    )

    # ── how it points (W3C Web Annotation selectors) ─────────────────────────
    quote = fields.Text(
        help="The exact words marked. Empty for an insertion point, which is a "
        "seam between neighbours rather than a passage.",
    )
    prefix = fields.Char(help="Text immediately before the quote, for telling repeats apart.")
    suffix = fields.Char(help="Text immediately after the quote, for telling repeats apart.")
    start_pos = fields.Integer(help="Character offset of the quote when the mark was made.")
    end_pos = fields.Integer(help="Character offset just past the quote when the mark was made.")
    anchored_by = fields.Char(
        readonly=True,
        help="Evidence that placed this mark the last time it was resolved: "
        "position, exact, unique, context, nearest or fuzzy.",
    )

    # ── what it says ─────────────────────────────────────────────────────────
    motivation = fields.Selection(
        MOTIVATIONS, required=True, default="commenting", index=True,
        help="Why the mark was made. 'Suggested edit' is what makes a mark a proposal.",
    )
    edit_kind = fields.Selection(
        [("insert", "Insert"), ("delete", "Delete"), ("replace", "Replace")],
        compute="_compute_edit_kind", store=True,
        help="Shape of a suggested edit, derived from what it replaces and with what.",
    )
    body = fields.Text(
        help="The note, the question, or the replacement text a suggested edit proposes.",
    )
    editorial_level = fields.Selection(
        [("developmental", "Developmental"), ("line", "Line"),
         ("copy", "Copy"), ("proof", "Proof")],
        help="Depth of the note, so a writer can take the structural ones first.",
    )

    # ── how it was made ──────────────────────────────────────────────────────
    ink = fields.Json(
        help="The pen stroke that made this mark, kept as evidence. Recognition is "
        "a guess, so the stroke stays correctable and a hand-written note is never "
        "lost to it.",
    )
    gesture = fields.Char(
        help="What the stroke was recognised as: strike, scribble, underline, "
        "caret, circle or margin.",
    )

    # ── who made it ──────────────────────────────────────────────────────────
    author_id = fields.Many2one(
        "res.partner", required=True, index=True,
        default=lambda self: self.env.user.partner_id,
        help="Who made this mark. An agent writes under its own partner, so every "
        "contribution is attributable.",
    )
    author_kind = fields.Selection(
        [("human", "Human"), ("agent", "Agent")], default="human", required=True,
        help="Whether a person or an agent made it, so a reader can filter by hand.",
    )

    # ── where it stands ──────────────────────────────────────────────────────
    state = fields.Selection(
        [("open", "Open"), ("accepted", "Accepted"), ("rejected", "Rejected"),
         ("superseded", "Superseded"), ("orphaned", "Orphaned")],
        default="open", required=True, index=True,
        help="Orphaned means the passage it marked is gone: the mark is kept and "
        "listed rather than deleted, because a lost note is worse than a stale one.",
    )
    resolved_by_id = fields.Many2one(
        "res.partner", readonly=True, help="Who accepted or rejected this mark.",
    )
    resolved_on = fields.Datetime(readonly=True, help="When it was accepted or rejected.")
    superseded_by_id = fields.Many2one(
        "markup.mark", ondelete="set null",
        help="The mark whose acceptance overtook this one.",
    )

    parent_id = fields.Many2one(
        "markup.mark", string="In reply to", ondelete="cascade", index=True,
        help="The mark this one answers, making a margin thread.",
    )
    child_ids = fields.One2many("markup.mark", "parent_id", string="Replies")

    @api.depends("motivation", "quote", "body")
    def _compute_edit_kind(self):
        for mark in self:
            if mark.motivation != "editing":
                mark.edit_kind = False
            elif not (mark.quote or ""):
                mark.edit_kind = "insert"
            elif not (mark.body or ""):
                mark.edit_kind = "delete"
            else:
                mark.edit_kind = "replace"

    @api.constrains("motivation", "quote", "body")
    def _check_editing_has_substance(self):
        for mark in self:
            if mark.motivation == "editing" and not (mark.quote or mark.body):
                raise ValidationError(
                    "A suggested edit must replace something or write something in."
                )

    @api.depends("motivation", "quote", "body")
    def _compute_display_name(self):
        for mark in self:
            label = dict(MOTIVATIONS).get(mark.motivation, mark.motivation)
            passage = (mark.quote or mark.body or "").strip().replace("\n", " ")
            mark.display_name = f"{label}: {passage[:40]}…" if len(passage) > 40 else f"{label}: {passage}"

    # ── placement ────────────────────────────────────────────────────────────
    def _reanchor(self, text):
        """Place this mark in ``text`` again, or orphan it.

        Called after the text moves. A mark that resolves keeps its identity and
        gains new offsets; one that cannot is marked orphaned rather than guessed
        at, and stays visible to whoever has to decide what became of it.
        """
        for mark in self:
            if mark.state not in ("open", "orphaned"):
                continue
            placed = anchor.resolve(
                text, mark.quote, mark.prefix or "", mark.suffix or "", mark.start_pos
            )
            if not placed:
                mark.write({"state": "orphaned", "anchored_by": False})
                continue
            start, end, how = placed
            values = {"start_pos": start, "end_pos": end, "anchored_by": how}
            if mark.state == "orphaned":
                values["state"] = "open"
            if mark.quote and how != "fuzzy":
                # The quote stays as authored — refreshing it from an edited
                # passage would launder someone else's change into the mark.
                # Only the surrounding context is re-taken, and only when the
                # quote itself was found intact.
                refreshed = anchor.selector(text, start, end)
                values["prefix"] = refreshed["prefix"]
                values["suffix"] = refreshed["suffix"]
            mark.write(values)

    def to_dict(self):
        """The transport shape the markup surface reads, in browser or terminal."""
        return [{
            "id": mark.id,
            "field": mark.field_name,
            "motivation": mark.motivation,
            "edit_kind": mark.edit_kind,
            "quote": mark.quote or "",
            "prefix": mark.prefix or "",
            "suffix": mark.suffix or "",
            "start": mark.start_pos,
            "end": mark.end_pos,
            "body": mark.body or "",
            "level": mark.editorial_level or "",
            "gesture": mark.gesture or "",
            "ink": mark.ink or None,
            "state": mark.state,
            "anchored_by": mark.anchored_by or "",
            "author": mark.author_id.display_name,
            "author_kind": mark.author_kind,
            "version_ref": mark.version_ref or "",
            "parent_id": mark.parent_id.id or None,
        } for mark in self]
