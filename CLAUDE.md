# CLAUDE.md - odoo_mixins

**Repository**: /workspace/HarrisonConsulting/odoo_mixins/
**Product Family**: @/mnt/gdo/docs/product/odoo/platform/CLAUDE.md
**Catalog**: @/mnt/gdo/docs/product/odoo/PRODUCT-CATALOG.md

---

## Context

Shared utility mixins for Odoo modules. Provides reusable patterns for field diff
tracking, queue job preflight checks, and environment variable configuration that
are consumed as dependencies across the entire module portfolio.

## Module Inventory

| Module | Version | License | Purpose |
|--------|---------|---------|---------|
| `field_diff_tracking` | 19.0.1.0.0 | LGPL-3 | Track diffs for text, char, and HTML fields |
| `queue_preflight` | 19.0.1.0.0 | LGPL-3 | Preflight health checks before queue job fanout |
| `env_config` | 19.0.1.0.0 | OPL-1 | ENV var + ir.config_parameter config mixin |

## Architecture

Each module is an independent mixin with no inter-dependencies. They are designed to
be inherited by other modules:

- `field_diff_tracking` — adds diff computation to any model's text fields
- `queue_preflight` — gate check before `queue_job` fanout to prevent cascading failures
- `env_config` — unified mixin for reading config from environment variables with
  `ir.config_parameter` fallback

## Consumers

These modules are widely depended on across the portfolio:
- `env_config` — used by `openai_base`, `modelnexus`, `alpaca`, and others
- `queue_preflight` — used by any module with `queue_job` batch operations
- `field_diff_tracking` — used by modules needing audit trails on text fields

## Module Registration

When creating a new Odoo module in this repository:
1. Add the module to @/mnt/gdo/docs/product/odoo/PRODUCT-CATALOG.md
2. Run `python /mnt/gdo/scripts/validate-catalog.py` to verify registration
3. If complex (>3 models, custom controllers, or JS components):
   create a CLAUDE.md following @/mnt/gdo/docs/product/odoo/.templates/MODULE-CLAUDE.md.template
