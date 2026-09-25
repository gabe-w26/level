"""
Line items on a quote.

A quote can stay what it always was — a price and a paragraph. But most tradies
already break a job down somewhere, usually on a pad in the van, and a customer
looking at "$6,400" has no idea whether that is two days' labour and materials
or six. So a quote can carry its parts: what, how many, at what rate.

What this buys, in order of how much it matters:

  • the customer sees where the money goes, which is the single most common
    thing they ring up and ask;
  • `compare.py` gets something real to compare, instead of guessing from the
    words in the inclusions box;
  • the tradie stops retyping the same eight lines, because a template holds
    them.

Rules this sticks to:
  • Items are optional, always. A quote with none behaves exactly as it did
    before this file existed, and nothing in the app requires them.
  • The total is worked out here, from the items, and it is the only number
    that goes on the quote. A tradie who types items and a different total is
    a tradie whose customer will find the discrepancy.
  • GST is handled once, at the bottom, the way an invoice does it — never per
    line. Mixing inclusive and exclusive lines is how quotes go wrong.
  • Each line is rounded to the cent, and the total is the sum of those rounded
    lines. That is the opposite of what you'd do in a spreadsheet, and it is
    right here: every line is printed as money, so the printed lines have to
    add up to the printed total. A total that is two cents off the column above
    it is the kind of thing a customer notices and a tradie can't explain.
"""
import json

GST_RATE = 0.15
MAX_ITEMS = 40
UNITS = ['hours', 'days', 'each', 'm', 'm²', 'm³', 'lineal m', 'sheets', 'litres', 'sum']


class QuoteError(Exception):
    """Shown to the tradie as-is."""


def _num(value, places=2):
    """A number from whatever the form gave us, or None. Never raises on rubbish."""
    if value is None:
        return None
    text = str(value).strip().replace(',', '').replace('$', '')
    if not text:
        return None
    try:
        return round(float(text), places)
    except ValueError:
        return None


def parse_items(form):
    """Line items out of a submitted quote form. Blank rows are dropped, not rejected.

    The form sends parallel lists — `item_description[]`, `item_qty[]` and so on
    — so a row is only real once it has a description.
    """
    descriptions = form.getlist('item_description')
    qtys, units, prices = form.getlist('item_qty'), form.getlist('item_unit'), form.getlist('item_price')
    items = []
    for i, description in enumerate(descriptions):
        text = (description or '').strip()
        if not text:
            continue
        if len(items) >= MAX_ITEMS:
            raise QuoteError(f'That’s more than {MAX_ITEMS} lines — put the small stuff together.')
        items.append({
            'description': text[:200],
            'qty': _num(qtys[i] if i < len(qtys) else None, 3),
            'unit': (units[i] if i < len(units) else '').strip()[:20] or None,
            'unit_price': _num(prices[i] if i < len(prices) else None),
        })
    return items


def line_total(item):
    """One line's money, rounded to the cent, or None when it hasn't been priced.

    Rounded here, not at the end, so the column the customer reads adds up to
    the total underneath it.

    A line with a price and no quantity counts once — "Scaffolding hire, $450"
    is a perfectly normal thing to write.
    """
    price = item.get('unit_price')
    if price is None:
        return None
    qty = item.get('qty')
    return round(price * (1 if qty is None else qty), 2)


def totals(items, gst_included=False):
    """What the lines add up to. Returns None for `total` when nothing is priced.

    `subtotal` is always the figure before GST. When the tradie has entered
    GST-inclusive prices we work backwards to it, so the breakdown reads the
    same either way.
    """
    priced = [t for t in (line_total(i) for i in items) if t is not None]
    if not priced:
        return {'subtotal': None, 'gst': None, 'total': None, 'lines': 0, 'unpriced': len(items)}
    summed = round(sum(priced), 2)
    if gst_included:
        subtotal = round(summed / (1 + GST_RATE), 2)
        gst, total = round(summed - subtotal, 2), summed
    else:
        subtotal = summed
        gst = round(subtotal * GST_RATE, 2)
        total = round(subtotal + gst, 2)
    return {'subtotal': subtotal, 'gst': gst, 'total': total,
            'lines': len(items), 'unpriced': len(items) - len(priced)}


def price_from_items(items, gst_included=False):
    """The quote's headline price, worked out from the lines.

    Returned in the same shape the quote form uses, so the caller can drop it
    straight into the quote and the rest of the app never knows the difference.
    Whole dollars, because that is what the quote stores.
    """
    t = totals(items, gst_included)
    if t['total'] is None:
        return None
    amount = round(t['total'] if gst_included else t['subtotal'])
    return {'price_type': 'fixed', 'amount_low': amount, 'amount_high': amount}


def save(db, quote_id, items, at=None):
    """Replace a quote's lines. Called inside the same commit as the quote itself."""
    from engine import ts, utcnow
    now = ts(at or utcnow())
    db.execute('DELETE FROM quote_items WHERE quote_id = ?', (quote_id,))
    for position, item in enumerate(items):
        db.execute('INSERT INTO quote_items (quote_id, position, description, qty, unit, unit_price, created_at) '
                   'VALUES (?,?,?,?,?,?,?)',
                   (quote_id, position, item['description'], item['qty'], item['unit'],
                    item['unit_price'], now))
    return len(items)


def items_for(db, quote_id):
    """One quote's lines, in order, each with its own total worked out."""
    rows = [dict(r) for r in db.execute(
        'SELECT * FROM quote_items WHERE quote_id = ? ORDER BY position, id', (quote_id,)).fetchall()]
    for row in rows:
        row['total'] = line_total(row)
    return rows


def items_for_many(db, quote_ids):
    """Lines for a page full of quotes, in one query rather than one each."""
    if not quote_ids:
        return {}
    marks = ','.join('?' * len(quote_ids))
    out = {qid: [] for qid in quote_ids}
    for r in db.execute(f'SELECT * FROM quote_items WHERE quote_id IN ({marks}) ORDER BY quote_id, position, id',
                        list(quote_ids)).fetchall():
        row = dict(r)
        row['total'] = line_total(row)
        out[row['quote_id']].append(row)
    return out


def summarise(items):
    """One line of plain words for a quote that has a breakdown.

    Used where there isn't room for the table — a list, a notification, the
    phone app's quote card.
    """
    if not items:
        return None
    words = ', '.join(i['description'].lower() for i in items[:3])
    if len(items) > 3:
        words += f' and {len(items) - 3} more'
    return f'{len(items)} line{"s" if len(items) != 1 else ""}: {words}'


# ── Templates ───────────────────────────────────────────────────────────────
# A tradie who writes the same eight lines every week should write them once.

def template_items(row):
    """The stored lines on a quote template, or an empty list. Never raises on bad JSON."""
    raw = None
    try:
        raw = row['items']
    except (KeyError, IndexError, TypeError):
        return []
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get('description')] if isinstance(items, list) else []


def dump_items(items):
    """Lines ready to store on a template."""
    return json.dumps([{'description': i['description'], 'qty': i['qty'], 'unit': i['unit'],
                        'unit_price': i['unit_price']} for i in items]) if items else None
