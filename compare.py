"""
Comparing quotes fairly.

The cheapest quote usually isn't cheaper — it's smaller, or it's plus GST, or it
leaves out the rubbish removal. A customer looking at three prices can't see any
of that, so this works it out and says it plainly.

Rules this sticks to, because the alternative is telling people what to think:
  • never say a quote is bad, only that something isn't mentioned in it;
  • only compare things the trades actually wrote down;
  • only call a price low when there's something real to compare it against;
  • always give the customer the question to ask, not the answer.
"""
import re

GST_RATE = 0.15

# Things quotes commonly differ on. `words` are matched against what the trade
# wrote; `label` is what the customer reads.
SCOPE_ITEMS = [
    ('materials', 'Materials', ('material', 'materials', 'supply', 'supplied', 'parts')),
    ('labour', 'Labour', ('labour', 'labor')),
    ('rubbish', 'Rubbish removal', ('rubbish', 'waste', 'disposal', 'dispose', 'skip', 'tip fees', 'green waste')),
    ('making_good', 'Making good afterwards', ('making good', 'make good', 'patching', 'patch', 'reinstate',
                                               'tidy up', 'clean up', 'cleanup')),
    ('consent', 'Council consent or fees', ('consent', 'council', 'building consent', 'permit')),
    ('scaffolding', 'Scaffolding or access', ('scaffold', 'scaffolding', 'edge protection', 'access equipment')),
    ('painting', 'Painting or finishing', ('paint', 'painting', 'finishing', 'stain', 'oil')),
    ('prep', 'Preparation', ('prep', 'preparation', 'surface prep', 'sanding')),
    ('certificate', 'Certificate or sign-off', ('certificate', 'compliance', 'coc', 'producer statement',
                                                'sign off', 'sign-off', 'ps3')),
]

LOW_PRICE_GAP = 0.25        # flag a quote this far below the others
MIN_TO_COMPARE = 2          # need at least this many other priced quotes


def _text(q, *fields):
    return ' '.join((q.get(f) or '') for f in fields).lower()


def _mentions(text, words):
    return any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)


def incl_gst(q):
    """A quote's price including GST, as a single number, or None when there isn't one.

    A range uses its midpoint. This is only ever used for comparing like with like —
    the quote itself is always shown exactly as the trade wrote it.
    """
    if q.get('price_type') == 'site_visit':
        return None
    low, high = q.get('amount_low'), q.get('amount_high')
    if low is None:
        return None
    mid = (low + high) / 2 if (q.get('price_type') == 'range' and high) else float(low)
    return mid if q.get('gst_included') else mid * (1 + GST_RATE)


def scope_notes(quote, others):
    """What the other quotes cover that this one doesn't mention, and what it rules out."""
    mine_in = _text(quote, 'inclusions', 'message')
    mine_out = _text(quote, 'exclusions')
    notes = []
    for _, label, words in SCOPE_ITEMS:
        if _mentions(mine_out, words):
            notes.append({'kind': 'excluded', 'label': label,
                          'text': f'excludes {label.lower()}'})
            continue
        if _mentions(mine_in, words):
            continue
        covered = [o for o in others if _mentions(_text(o, 'inclusions', 'message'), words)
                   and not _mentions(_text(o, 'exclusions'), words)]
        if len(covered) > len(others) / 2:                                  # most of the others, not just one
            notes.append({'kind': 'missing', 'label': label,
                          'text': f'doesn’t mention {label.lower()}, which the other quote'
                                  f'{"s" if len(covered) != 1 else ""} do'
                                  f'{"" if len(covered) != 1 else "es"}'})
    return notes


def gst_note(quote, others):
    """Flag the trap of comparing a plus-GST price against an includes-GST one."""
    if quote.get('price_type') == 'site_visit' or quote.get('amount_low') is None:
        return None
    priced = [o for o in others if o.get('price_type') != 'site_visit' and o.get('amount_low') is not None]
    if not priced or quote.get('gst_included'):
        return None
    if any(o.get('gst_included') for o in priced):
        total = incl_gst(quote)
        return {'kind': 'gst', 'text': f'this price is plus GST — that’s about ${total:,.0f} including GST, '
                                       f'which is how the other quote{"s are" if len(priced) != 1 else " is"} shown'}
    return None


def price_note(quote, others, guide=None):
    """Say when a price sits well below the others. Never says the quote is bad."""
    mine = incl_gst(quote)
    if mine is None:
        return None
    theirs = [t for t in (incl_gst(o) for o in others) if t]
    if len(theirs) >= MIN_TO_COMPARE:
        theirs = sorted(theirs)
        middle = theirs[len(theirs) // 2] if len(theirs) % 2 else (theirs[len(theirs) // 2 - 1] +
                                                                   theirs[len(theirs) // 2]) / 2
        if middle and mine < middle * (1 - LOW_PRICE_GAP):
            gap = round((1 - mine / middle) * 100)
            return {'kind': 'low', 'text': f'about {gap}% below the other quotes'}
        return None
    # Not enough quotes on this job — fall back to what this trade usually costs on Level.
    if guide and guide.get('low') and mine < guide['low'] * (1 - LOW_PRICE_GAP):
        gap = round((1 - mine / guide['low']) * 100)
        return {'kind': 'low', 'text': f'about {gap}% below what this kind of job usually costs on Level'}
    return None


def notes_for(quotes, guide=None):
    """{quote id: [notes]} for a job's quotes. Quotes are dicts (quotes_for_job)."""
    out = {}
    for q in quotes:
        others = [o for o in quotes if o['id'] != q['id']]
        notes = []
        gst = gst_note(q, others)
        if gst:
            notes.append(gst)
        low = price_note(q, others, guide)
        if low:
            notes.append(low)
        notes.extend(scope_notes(q, others))
        out[q['id']] = notes
    return out
