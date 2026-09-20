{
    "name": "Field Diff Tracking",
    "version": "19.0.2.0.0",
    "summary": "Redlines in the chatter, and marks on the passages people argue with",
    "sequence": 0,
    "description": """
Two ways of seeing what a text says and what was done to it, sharing one engine.

**Redlines.** A model lists its prose in ``_diff_tracked`` and every change posts
a word-level redline to the chatter — what was struck, what was written in, with
unchanged runs elided.

**Marks.** A model lists its prose in ``_markup_fields`` and a passage can then
carry a highlight, a note, a question, a suggested edit or a pen stroke: one
``markup.mark`` record with different intent. Marks anchor by quote, context and
position, re-place themselves when the text moves, and are reachable only through
the record they mark. A mark never rewrites the text — accepting one does, and
that is the host's act.

One route family, ``/markup/marks|add|resolve``, serves the backend, the customer
portal and project sharing, because the question each asks of a passage is the
same one.
""",
    "category": "Technical",
    "author": "Harrison Consulting, LLC",
    "website": "https://www.harrison.consulting",
    "license": "LGPL-3",
    "depends": ["base", "mail", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": ["field_diff_tracking/static/src/scss/redline.scss"],
        "web.assets_frontend": ["field_diff_tracking/static/src/scss/redline.scss"],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
