"""Anchoring: finding a quoted passage again after the text around it moved.

A mark points at a passage the way a person would: the exact words, a little of
what came before and after, and where it sat. Each of those spoils differently
— an edit elsewhere moves the position, an edit alongside spoils the context, a
deletion takes the quote itself — so resolution spends the cheapest evidence
first and stops when one answer is unambiguous.

It returns ``None`` rather than a guess. A mark that cannot be placed is worth
more as an orphan a reader can see than as an annotation pointing at the wrong
sentence.
"""
import difflib

# Characters of context kept either side of a quote. Enough to separate repeated
# phrases in prose, short enough to survive being edited next to.
CONTEXT = 32

# A fuzzy match must reproduce this much of the quote to count.
FUZZY_FLOOR = 0.75

# Above this many character pairs the fuzzy pass is skipped: it is the only
# quadratic step here, and a mark that needs it on a novel-sized field is a mark
# better shown as an orphan than paid for.
FUZZY_BUDGET = 8_000_000


def selector(text, start, end, context=CONTEXT):
    """The selector for a range of ``text``: what it says, and what surrounds it."""
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    return {
        "quote": text[start:end],
        "prefix": text[max(0, start - context):start],
        "suffix": text[end:end + context],
        "start_pos": start,
        "end_pos": end,
    }


def resolve(text, quote, prefix="", suffix="", start_pos=0, end_pos=None, floor=FUZZY_FLOOR):
    """Place a selector in ``text``.

    Takes exactly what :func:`selector` emits, so a stored selector can be handed
    over whole. ``end_pos`` is part of that shape; the quote's own length is what
    places its end, so it is recorded rather than read.

    :return: ``(start, end, how)`` where ``how`` names the evidence that settled
        it — ``position``, ``exact``, ``unique``, ``context``, ``nearest`` or
        ``fuzzy`` — or ``None`` when the passage is gone.
    """
    text = text or ""
    quote = quote or ""
    if not quote:
        return _resolve_insertion(text, prefix, suffix, start_pos)

    start_pos = max(0, min(start_pos or 0, len(text)))
    if text[start_pos:start_pos + len(quote)] == quote:
        return (start_pos, start_pos + len(quote), "position")

    hits = _occurrences(text, quote)
    if len(hits) == 1:
        return (hits[0], hits[0] + len(quote), "exact" if hits[0] == start_pos else "unique")
    if hits:
        return _disambiguate(text, quote, prefix, suffix, start_pos, hits)
    return _fuzzy(text, quote, start_pos, floor)


def _resolve_insertion(text, prefix, suffix, start_pos):
    """An insertion point has no quote: it is the seam between two neighbours."""
    if prefix:
        hits = _occurrences(text, prefix)
        if len(hits) == 1:
            return (hits[0] + len(prefix), hits[0] + len(prefix), "context")
        if hits:
            best = min(hits, key=lambda i: abs(i + len(prefix) - (start_pos or 0)))
            return (best + len(prefix), best + len(prefix), "nearest")
    if suffix:
        hits = _occurrences(text, suffix)
        if len(hits) == 1:
            return (hits[0], hits[0], "context")
    if start_pos is not None and 0 <= start_pos <= len(text):
        return (start_pos, start_pos, "position")
    return None


def _occurrences(text, needle, limit=200):
    found, index = [], text.find(needle)
    while index != -1 and len(found) < limit:
        found.append(index)
        index = text.find(needle, index + 1)
    return found


def _disambiguate(text, quote, prefix, suffix, start_pos, hits):
    """Repeated phrases are told apart by what surrounds them, then by distance."""
    scored = []
    for index in hits:
        before = text[max(0, index - len(prefix)):index] if prefix else ""
        after = text[index + len(quote):index + len(quote) + len(suffix)] if suffix else ""
        score = _common_suffix(prefix, before) + _common_prefix(suffix, after)
        scored.append((score, -abs(index - start_pos), index))
    scored.sort(reverse=True)
    best, runner = scored[0], scored[1]
    how = "context" if best[0] and best[0] > runner[0] else "nearest"
    return (best[2], best[2] + len(quote), how)


def _common_prefix(a, b):
    length = 0
    for left, right in zip(a, b):
        if left != right:
            break
        length += 1
    return length


def _common_suffix(a, b):
    return _common_prefix(a[::-1], b[::-1])


def _fuzzy(text, quote, start_pos, floor):
    """The quote was edited, not deleted: find how much of it survived, and where.

    A word inserted into the middle splits the quote into runs without losing
    any of it, so survival is measured across ALL the runs, not the longest one.
    The mark then spans what the passage became, which is usually not the length
    it was.
    """
    if not text or len(text) * len(quote) > FUZZY_BUDGET:
        return None
    matcher = difflib.SequenceMatcher(None, text, quote, autojunk=False)
    runs = [run for run in matcher.get_matching_blocks() if run.size]
    if not runs or sum(run.size for run in runs) / len(quote) < floor:
        return None
    start = max(0, runs[0].a - runs[0].b)
    end = min(len(text), runs[-1].a + runs[-1].size + (len(quote) - runs[-1].b - runs[-1].size))
    # Runs scattered the width of a document are coincidence, not a passage.
    if end - start > 3 * len(quote) + CONTEXT:
        end = min(len(text), start + len(quote))
    return (start, max(end, start + 1), "fuzzy")
