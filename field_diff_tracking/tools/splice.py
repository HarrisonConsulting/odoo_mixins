"""Writing an accepted edit back into HTML without discarding the markup.

A mark's offsets are measured against :func:`redline.html_text` — the readable
text of an HTML value, whitespace collapsed, one block per line. Splicing that
readable text and storing the result would be a correct edit of the wrong
thing: every tag in the field would be gone.

So the splice happens in the tree. The same walk that produces the readable
text is run again, this time recording which ``.text`` or ``.tail`` string each
character came out of, and the replacement is written into those strings. Bold
stays bold, the list stays a list, and the paragraph the edit did not touch is
byte-identical afterwards.

Blocks the edit empties are left standing. An empty block contributes no line
to the readable text — :func:`redline._flush` drops it — so leaving it keeps
the tree and the text agreeing, which is the invariant every mark depends on.
"""
from lxml import etree, html as lxml_html
from markupsafe import Markup, escape

from .redline import _BLOCK_TAGS

# Elements that carry meaning with no content of their own, so emptying the text
# around them must not take them with it.
_VOID_TAGS = frozenset("br img hr input area base col embed source track wbr".split())

# Mirrors ``str.isspace``, which is what ``" ".join(text.split())`` splits on.
_SPACE = frozenset("\t\n\v\f\r \x1c\x1d\x1e\x1f\x85\xa0"
                   "          "
                   "      　")


class _Slot:
    """One string in the tree that characters can come from and go back into."""

    __slots__ = ("element", "attribute")

    def __init__(self, element, attribute):
        self.element = element
        self.attribute = attribute

    def read(self):
        return getattr(self.element, self.attribute) or ""

    def write(self, value):
        setattr(self.element, self.attribute, value or None)


def splice_html(value, start, end, replacement):
    """Replace ``[start, end)`` of an HTML value's readable text.

    :param value: the stored HTML
    :param start: offset into :func:`redline.html_text` of ``value``
    :param end: offset just past the last character replaced
    :param replacement: plain text written in; empty for a deletion
    :return: ``Markup`` of the rewritten HTML
    :raises ValueError: when the range does not resolve to characters in the
        tree, which means the caller's offsets do not belong to this value
    """
    text = str(value or "")
    if not text.strip():
        return Markup(escape_text(replacement))
    try:
        root = lxml_html.fragment_fromstring(text, create_parent="div")
    except etree.ParserError:
        raise ValueError("That value is not HTML this splice can rewrite.")

    provenance = _map(root)
    start = max(0, min(int(start), len(provenance)))
    end = max(start, min(int(end), len(provenance)))

    # The characters actually written into the tree, ignoring the block
    # separators, which belong to no string and cannot be edited.
    covered = [entry for entry in provenance[start:end] if entry is not None]
    if not covered and not (replacement or ""):
        return Markup(text)

    if covered:
        _cut(covered)
        anchor = covered[0]
    else:
        anchor = _insertion_point(provenance, start)
        if anchor is None:
            raise ValueError(
                "That range names no character of this value, so there is "
                "nothing to replace and nowhere to write."
            )
    if replacement:
        current = anchor["slot"].read()
        offset = anchor["offset"]
        anchor["slot"].write(current[:offset] + replacement + current[offset:])

    _prune(root)
    return Markup("".join(
        [root.text or ""]
        + [lxml_html.tostring(child, encoding="unicode") for child in root]
    ))


def escape_text(value):
    """Plain text as an HTML paragraph, for a value that had no tree to keep."""
    if not (value or "").strip():
        return ""
    return str(Markup("<p>%s</p>") % escape(value))


def _cut(covered):
    """Remove every covered character from the strings that hold them.

    The first covered character is also the earliest the cut touches in its own
    string — a slot appears once in a block and its characters are read in
    order — so nothing is removed ahead of it and its offset still counts the
    same characters afterwards. That is what lets the replacement be written at
    that offset without re-deriving it.
    """
    by_slot = {}
    for entry in covered:
        by_slot.setdefault(id(entry["slot"]), (entry["slot"], set()))[1].add(entry["offset"])
    for slot, offsets in by_slot.values():
        current = slot.read()
        slot.write("".join(ch for index, ch in enumerate(current) if index not in offsets))


def _insertion_point(provenance, start):
    """Where an insertion with no covered characters is written."""
    for index in range(start, len(provenance)):
        if provenance[index] is not None:
            entry = provenance[index]
            return {"slot": entry["slot"], "offset": entry["offset"]}
    for index in range(start - 1, -1, -1):
        if provenance[index] is not None:
            entry = provenance[index]
            return {"slot": entry["slot"], "offset": entry["offset"] + 1}
    return None


def _prune(root):
    """Drop inline elements the edit emptied, keeping blocks and void tags."""
    for element in list(root.iter()):
        if element is root or not isinstance(element.tag, str):
            continue
        if element.tag in _BLOCK_TAGS or element.tag in _VOID_TAGS:
            continue
        if len(element) or (element.text or "").strip():
            continue
        parent = element.getparent()
        tail = element.tail or ""
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(element)


def _map(root):
    """Per-character provenance for the readable text, mirroring ``html_text``.

    ``None`` marks the newline between two blocks: it is produced by the join,
    not by any string in the tree, so it can be crossed by a range but never
    written to.
    """
    blocks = []
    buffer = []
    _walk(root, blocks, buffer, is_root=True)
    _flush(blocks, buffer)
    provenance = []
    for index, block in enumerate(blocks):
        if index:
            provenance.append(None)
        provenance.extend(block)
    return provenance


def _walk(element, blocks, buffer, is_root=False):
    tag = element.tag if isinstance(element.tag, str) else None
    # The value is wrapped in a div before it is walked, so the container opens
    # and closes a block whatever it is.
    block = is_root or tag in _BLOCK_TAGS
    if block or tag == "br":
        _flush(blocks, buffer)
    if tag and element.text:
        buffer.append(_Slot(element, "text"))
    for child in element:
        _walk(child, blocks, buffer)
        if child.tail:
            buffer.append(_Slot(child, "tail"))
    if block:
        _flush(blocks, buffer)


def _flush(blocks, buffer):
    """``" ".join("".join(strings).split())``, keeping provenance per character.

    A collapsed run of whitespace is attributed to its first character, so the
    space a reader selected is a real character in a real string.
    """
    line = []
    pending = None
    started = False
    for slot in buffer:
        value = slot.read()
        for offset, char in enumerate(value):
            if char in _SPACE:
                if started and pending is None:
                    pending = {"slot": slot, "offset": offset}
                continue
            if pending is not None:
                line.append(pending)
                pending = None
            line.append({"slot": slot, "offset": offset})
            started = True
    buffer.clear()
    if line:
        blocks.append(line)
