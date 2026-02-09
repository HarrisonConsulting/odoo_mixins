import logging
import os

from odoo import api, models

_logger = logging.getLogger(__name__)


class EnvConfigMixin(models.AbstractModel):
    """Mixin providing ENV-first configuration lookups.

    Lookup order: environment variable -> ir.config_parameter -> default.

    ENV var name resolution:
    1. Check _env_config_map for explicit override (e.g. standard service vars)
    2. Fall back to auto-conversion: 'some.key' -> 'SOME_KEY'

    Usage:
        class MyConfig(models.TransientModel):
            _name = 'my.config'
            _inherit = ['env.config.mixin']

            _env_config_map = {
                'my.api_key': 'MY_SERVICE_API_KEY',
            }

            def do_something(self):
                key = self._get_config('my.api_key')
    """
    _name = 'env.config.mixin'
    _description = 'Environment Variable Configuration Mixin'

    # Override in consuming models to map ir.config_parameter keys
    # to custom ENV var names (for standard service variables).
    # Example: {'openai.api_key': 'OPENAI_API_KEY'}
    _env_config_map = {}

    @api.model
    def _env_key(self, key):
        """Resolve the ENV var name for a config key."""
        return self._env_config_map.get(key) or key.upper().replace('.', '_')

    @api.model
    def _get_config(self, key, default=None):
        """Get config value: ENV var -> ir.config_parameter -> default."""
        env_key = self._env_key(key)
        env_value = os.environ.get(env_key)
        if env_value:
            return env_value

        param_value = self.env['ir.config_parameter'].sudo().get_param(key)
        if param_value:
            return param_value

        return default

    @api.model
    def _get_config_bool(self, key, default=False):
        """Get boolean config value."""
        value = self._get_config(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).lower() in ('true', '1', 'yes')

    @api.model
    def _get_config_int(self, key, default=0):
        """Get integer config value."""
        value = self._get_config(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @api.model
    def _get_config_float(self, key, default=0.0):
        """Get float config value."""
        value = self._get_config(key)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @api.model
    def _is_config_set(self, key):
        """Check if a config key has any value (ENV or DB)."""
        env_key = self._env_key(key)
        return bool(
            os.environ.get(env_key)
            or self.env['ir.config_parameter'].sudo().get_param(key)
        )

    @api.model
    def _set_config(self, key, value):
        """Set config value in ir.config_parameter (ENV cannot be set from Odoo)."""
        self.env['ir.config_parameter'].sudo().set_param(key, value)
