"""Transport for marks, shared by every surface that shows markable text.

One route family serves the backend, the customer portal and project sharing,
because the question each asks is the same one: *what is marked on this field,
and may I mark it too*.

The model is named by the caller, which is safe only because the set it can
name is closed twice over: the model must inherit ``markup.mixin``, and the
mixin then checks access on the record itself before it reads or writes
anything. A model outside that set is a 404 — the same existence masking the
portal uses everywhere else, so probing the route tells a caller nothing.
"""
from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

MARKUP_MIXIN = "markup.mixin"


def _markable(model_name, res_id):
    """The record, if it is one that carries marks and the caller may see it.

    Membership in the markable set is decided by the registry, not by a name the
    caller supplied. Precedent: addons/mail/models/mail_message.py:1453.
    """
    pool = request.env.registry
    if model_name not in pool or not issubclass(pool[model_name], pool[MARKUP_MIXIN]):
        return None
    record = request.env[model_name].browse(int(res_id))
    try:
        record.check_access("read")
    except (AccessError, MissingError, ValueError):
        return None
    return record if record.exists() else None


class MarkupTransport(http.Controller):

    @http.route("/markup/marks", type="jsonrpc", auth="user")
    def marks(self, model, res_id, field_name=None, states=("open",)):
        """Every mark on a field, for whoever may read the record."""
        record = _markable(model, res_id)
        if record is None:
            return {"error": "not_found"}
        try:
            return {
                "marks": record.markup_marks(field_name, tuple(states or ())),
                "text": record.markup_text(field_name) if field_name else None,
                "can_comment": record.markup_can_comment(),
                "can_resolve": record.has_access("write"),
                "can_ask_agent": record.markup_can_ask_agent(),
            }
        except (AccessError, UserError) as error:
            return {"error": "refused", "message": str(error)}

    @http.route("/markup/add", type="jsonrpc", auth="user")
    def add(self, model, res_id, field_name, start, end, values=None):
        """Mark a passage. The caller sends what it saw; the server places it."""
        record = _markable(model, res_id)
        if record is None:
            return {"error": "not_found"}
        try:
            return {"mark": record.markup_add(field_name, int(start), int(end), values or {})}
        except (AccessError, UserError) as error:
            return {"error": "refused", "message": str(error)}

    @http.route("/markup/ask", type="jsonrpc", auth="user")
    def ask(self, model, res_id, field_name, start, end, values=None):
        """Put a passage to an agent, where the estate grants that."""
        record = _markable(model, res_id)
        if record is None:
            return {"error": "not_found"}
        try:
            return {"mark": record.markup_ask_agent(field_name, int(start), int(end), values or {})}
        except (AccessError, UserError) as error:
            return {"error": "refused", "message": str(error)}

    @http.route("/markup/resolve", type="jsonrpc", auth="user")
    def resolve(self, model, res_id, mark_id, state, body=None):
        """Accept or reject a mark, which is the host record's act, never the mark's."""
        record = _markable(model, res_id)
        if record is None:
            return {"error": "not_found"}
        try:
            return {"mark": record.markup_resolve(int(mark_id), state, body)}
        except (AccessError, UserError) as error:
            return {"error": "refused", "message": str(error)}
