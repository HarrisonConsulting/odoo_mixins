"""Redlines: what changed between two versions of a text, shown the way an
editor marks a page.

Struck text sits in ``<del>``, written-in text in ``<ins>``, with enough
unchanged text around each change to read it in place. The comparison runs in
two passes: first by unit (a line of code, a line of prose, a block of HTML),
then by word inside the units that changed, so one corrected word reads as one
correction rather than as a replaced line. A line break that was removed is
shown as a struck pilcrow, the proofreader's mark for "run on".

The output uses only ``div``, ``p``, ``pre``, ``ins``, ``del`` and ``class``,
all of which survive Odoo's HTML sanitiser, so it can be posted to chatter,
stored in an Html field or rendered on the portal unchanged.
"""
import difflib
import re

from lxml import etree, html as lxml_html
from markupsafe import Markup, escape

STYLES = ("code", "text", "html")

# Above this many token pairs a changed block is shown whole (struck, then
# written in) instead of word by word, keeping the comparison linear-ish on
# pathological inputs such as a single-line HTML blob.
WORD_DIFF_BUDGET = 4_000_000

_TOKEN_RE = re.compile(r"\n|[^\S\n]+|\w+|[^\w\s]", re.UNICODE)

_BLOCK_TAGS = frozenset(
    "address article aside blockquote dd div dl dt figcaption figure footer "
    "h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table tbody td "
    "tfoot th thead tr ul".split()
)


def redline(old, new, style="text", context=1):
    """Return the redline from ``old`` to ``new`` as ``Markup``.

    :param old: the earlier value (str, Markup, False or None)
    :param new: the later value
    :param style: ``code`` (monospace, line units), ``text`` (prose, line
        units) or ``html`` (the readable text of each block element)
    :param context: unchanged units kept on each side of a change
    :return: ``Markup('')`` when the readable content did not change
    """
    if style not in STYLES:
        raise ValueError(f"redline style must be one of {STYLES}, not {style!r}")
    split = _html_blocks if style == "html" else _lines
    old_units, new_units = split(old), split(new)
    if old_units == new_units:
        return Markup("")
    rows = _rows(old_units, new_units, context)
    if style == "code":
        body = Markup("\n").join(rows)
        return Markup('<pre class="o_redline o_redline_code">%s</pre>') % body
    body = Markup("").join(Markup("<p>%s</p>") % (row or Markup("&#160;")) for row in rows)
    return Markup('<div class="o_redline o_redline_%s">%s</div>') % (style, body)


# ── Units ───────────────────────────────────────────────────────────────────

def _lines(value):
    return str(value or "").splitlines()


def html_text(value):
    """The readable text of an HTML value, one block per line.

    This is what a mark's offsets are measured against, so the text the reader
    sees and the text the server counts are the same characters. Structure is
    not decorated here: a heading contributes its words, not its level.
    """
    return "\n".join(_html_blocks(value, decorate=False))


def _html_blocks(value, decorate=True):
    """The readable text of each block of an HTML value, in document order.

    With ``decorate``, headings keep their level as a ``#`` prefix and list
    items a bullet, so a change of structure reads as a change of text. Without
    it, only the words are returned.
    """
    if not value or not str(value).strip():
        return []
    try:
        root = lxml_html.fragment_fromstring(str(value), create_parent="div")
    except etree.ParserError:
        return _lines(value)
    blocks, buffer = [], []
    _walk(root, blocks, buffer, decorate=decorate)
    _flush(blocks, buffer, "")
    return blocks


def _walk(element, blocks, buffer, prefix="", decorate=True):
    tag = element.tag if isinstance(element.tag, str) else None
    if tag in _BLOCK_TAGS or tag == "br":
        _flush(blocks, buffer, prefix)
        prefix = _prefix(tag) if decorate else ""
    if tag and element.text:
        buffer.append(element.text)
    for child in element:
        _walk(child, blocks, buffer, prefix, decorate)
        if child.tail:
            buffer.append(child.tail)
    if tag in _BLOCK_TAGS:
        _flush(blocks, buffer, prefix)


def _prefix(tag):
    if tag and len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return "#" * int(tag[1]) + " "
    return "• " if tag == "li" else ""


def _flush(blocks, buffer, prefix):
    text = " ".join("".join(buffer).split())
    buffer.clear()
    if text:
        blocks.append(prefix + text)


# ── Comparison ──────────────────────────────────────────────────────────────

def _rows(old_units, new_units, context):
    """Rows of inline markup, one per displayed unit, with elided runs."""
    matcher = difflib.SequenceMatcher(None, old_units, new_units, autojunk=False)
    opcodes = matcher.get_opcodes()
    rows = []
    for index, (op, i1, i2, j1, j2) in enumerate(opcodes):
        if op == "equal":
            rows.extend(_context_rows(
                old_units[i1:i2], context,
                keep_head=index > 0, keep_tail=index < len(opcodes) - 1,
            ))
        elif op == "delete":
            rows.extend(_wrap("del", unit) for unit in old_units[i1:i2])
        elif op == "insert":
            rows.extend(_wrap("ins", unit) for unit in new_units[j1:j2])
        else:
            rows.extend(_word_rows("\n".join(old_units[i1:i2]), "\n".join(new_units[j1:j2])))
    return rows


def _context_rows(units, context, keep_head, keep_tail):
    head = units[:context] if keep_head else []
    tail = units[-context:] if keep_tail and context else []
    if len(head) + len(tail) >= len(units):
        return [escape(unit) for unit in units]
    skipped = len(units) - len(head) - len(tail)
    marker = Markup('<span class="o_redline_skip">⋯ %s unchanged</span>') % skipped
    return [escape(unit) for unit in head] + [marker] + [escape(unit) for unit in tail]


def _word_rows(old, new):
    """Rows for one changed block, compared word by word."""
    old_tokens, new_tokens = _TOKEN_RE.findall(old), _TOKEN_RE.findall(new)
    if len(old_tokens) * len(new_tokens) > WORD_DIFF_BUDGET:
        return [_wrap("del", line) for line in old.split("\n")] + [
            _wrap("ins", line) for line in new.split("\n")
        ]
    pieces = []
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            pieces.extend((None, token) for token in old_tokens[i1:i2])
            continue
        if op in ("delete", "replace"):
            pieces.extend(("del", token) for token in old_tokens[i1:i2])
        if op in ("insert", "replace"):
            pieces.extend(("ins", token) for token in new_tokens[j1:j2])
    return _pieces_to_rows(pieces)


def _pieces_to_rows(pieces):
    rows, row, run_kind, run = [], [], None, []

    def close_run():
        if run:
            text = "".join(run)
            row.append(_wrap(run_kind, text) if run_kind else escape(text))
            run.clear()

    for kind, token in pieces:
        if token == "\n" and kind != "del":
            close_run()
            rows.append(Markup("").join(row))
            row = []
            continue
        if token == "\n":
            token = "¶"
        if kind != run_kind:
            close_run()
            run_kind = kind
        run.append(token)
    close_run()
    rows.append(Markup("").join(row))
    return rows


def _wrap(tag, text):
    return Markup("<%s>%s</%s>") % (Markup(tag), text, Markup(tag))
