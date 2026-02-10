import logging

from odoo import models

_logger = logging.getLogger(__name__)


class QueuePreflightMixin(models.AbstractModel):
    _name = 'queue.preflight.mixin'
    _description = 'Queue Job Preflight Check Mixin'

    # --- Hook methods (override in consuming models) ---

    def _preflight_check_enabled(self):
        """Is the feature/service enabled? Return (ok, reason)."""
        return True, ""

    def _preflight_check_credentials(self):
        """Do required credentials exist? Return (ok, reason)."""
        return True, ""

    def _preflight_refresh_credentials(self):
        """Attempt credential refresh (e.g. OAuth token rotation).
        No-op for static tokens/API keys — only override for OAuth flows."""
        return True, ""

    def _preflight_check_health(self):
        """Lightweight service health ping. Return (ok, reason)."""
        return True, ""

    def _preflight_on_skip(self, service_name, stage, reason):
        """Called when a preflight check fails. Override for audit records or UserError."""
        _logger.warning(
            "Preflight [%s] failed at '%s': %s — skipping job fanout",
            service_name, stage, reason,
        )

    # --- Orchestrator ---

    def _run_preflight(self, service_name='', stages=None):
        """Run preflight checks in order. Returns True if all pass.

        Args:
            service_name: Human label for logging (e.g. 'GitHub', 'Fireflies').
            stages: Optional list of (name, callable) tuples to override
                    the default check pipeline.
        """
        if stages is None:
            stages = [
                ('enabled', self._preflight_check_enabled),
                ('credentials', self._preflight_check_credentials),
                ('refresh', self._preflight_refresh_credentials),
                ('health', self._preflight_check_health),
            ]
        for stage_name, check_fn in stages:
            try:
                ok, reason = check_fn()
                if not ok:
                    self._preflight_on_skip(service_name, stage_name, reason)
                    return False
            except Exception as e:
                self._preflight_on_skip(service_name, stage_name, str(e))
                return False
        return True
