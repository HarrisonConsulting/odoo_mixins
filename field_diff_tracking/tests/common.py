"""A host for marks that this module's tests can own.

This module ships the mixin and no record to hang it on, and borrowing a host
from a module further down the tree would test that module's install rather
than this one. So ``res.partner`` is made markable for the duration of a class.
"""
from odoo import models
from odoo.orm.model_classes import add_to_registry
from odoo.tests import TransactionCase


class MarkablePartnerCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_partner_markable()

    @classmethod
    def _make_partner_markable(cls):
        """Give ``res.partner`` marks for this class, and take them back after.

        Precedent: /mnt/19/odoo/odoo/addons/test_orm/tests/test_fields.py:4631
        — a model definition added to the live registry, then set up
        incrementally. The mixin contributes one non-stored computed field, so
        nothing here reaches the schema.
        """
        registry = cls.env.registry
        Partner = registry["res.partner"]
        # Class cleanups run last-registered-first: the base classes are put
        # back, and only then is the registry rebuilt from them. The model has
        # to be NAMED on the way out — that is what clears ``_setup_done__``,
        # and a setup that skips the model leaves its ``__bases__`` disagreeing
        # with what was just restored.
        cls.addClassCleanup(registry._setup_models__, cls.env.cr, ["res.partner"])
        cls.addClassCleanup(setattr, Partner, "_base_classes__", Partner._base_classes__)

        class MarkablePartner(models.Model):
            _module = None
            _name = "res.partner"
            _inherit = ["res.partner", "markup.mixin"]
            _markup_fields = ("comment",)

        add_to_registry(registry, MarkablePartner)
        registry._setup_models__(cls.env.cr, ["res.partner"])
