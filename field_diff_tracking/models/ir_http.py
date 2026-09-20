from odoo import api, models
from odoo.http import request


class IrHttp(models.AbstractModel):
    """Carry the reader's label preference into the session, front and back.

    The ``user`` service is backed by ``res.users.settings`` and the request
    context carries only language and timezone, so neither reaches a field on
    ``res.users``. The session does, on both sides, which is how ``portal``
    carries ``tour_enabled``.
    """

    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        if request and request.session.uid:
            result["markup_label_style"] = self.env.user.markup_label_style
        return result

    @api.model
    def get_frontend_session_info(self):
        result = super().get_frontend_session_info()
        if request and request.session.uid:
            result["markup_label_style"] = self.env.user.markup_label_style
        return result
