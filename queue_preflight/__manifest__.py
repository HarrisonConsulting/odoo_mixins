{
    "name": "Queue Preflight",
    "version": "19.0.1.0.0",
    "summary": "Preflight health checks before queue job fanout",
    "description": """Provides queue.preflight.mixin — an abstract model
that standardizes service health validation before dispatching batch
queue jobs. Prevents N individual failures when a service is unhealthy
by checking once and bailing early with a single log entry.""",
    "category": "Technical",
    "author": "Harrison Consulting, LLC",
    "website": "https://www.harrison.consulting",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": False,
}
