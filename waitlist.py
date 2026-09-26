"""
Holding the door until there's something behind it.

A marketplace is worthless to the first person through the door and only gets
worth something once both sides are there. Opening early is the expensive
mistake: a homeowner posts a real job, nobody quotes because there are no
tradies yet, and that's the impression you don't get a second go at. So until a
region has both sides, the front door collects names instead of taking jobs.

Three things this is built around:

  · **It's a switch, not a deploy.** Whoever runs Level turns it off in
    Admin → Setup the day a region is worth opening, and off is the default —
    an unconfigured Level behaves exactly as it always did.

  · **Nobody already in is locked out.** Existing accounts log in, work their
    jobs and get paid the whole time. This only stands in front of the two
    doors that would create somebody new.

  · **Joining has to be worth doing.** A form that says "thanks, we'll be in
    touch" is a dead end. This one says how many people near you are already
    waiting and what's still missing — which is the honest reason to leave your
    address, and is also exactly the number that tells you when to open.
"""
import os
import re

import integrations

SIDES = {'customer': 'a homeowner', 'trade': 'a tradie'}
EMAIL = re.compile(r'^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$')

# How many of each side a region needs before it's worth opening. Not a rule the
# code enforces — it's the yardstick the admin page measures against, so "are we
# there yet" has an answer instead of being a feeling.
READY_TRADES = int(os.environ.get('WAITLIST_READY_TRADES', '8'))
READY_CUSTOMERS = int(os.environ.get('WAITLIST_READY_CUSTOMERS', '10'))


class WaitlistError(Exception):
    """Shown to whoever tried it, as-is."""


def is_on():
    """Is the front door closed? Off unless somebody turned it on."""
    return integrations.get('waitlist') == '1'


# ── Joining ───────────────────────────────────────────────────────────────────

def join(db, form, at=None):
    """Put somebody on the list, or update them if they're already on it.

    Joining twice is not an error and doesn't make a second row — people forget,
    and telling somebody off for being keen is a strange way to treat the only
    asset you have at this point.
    """
    from engine import ts, utcnow
    side = (form.get('side') or '').strip()
    if side not in SIDES:
        raise WaitlistError('Tell us whether you need a tradie or are one.')
    email = (form.get('email') or '').strip().lower()
    if not EMAIL.match(email) or len(email) > 200:
        raise WaitlistError('That email address doesn’t look right.')
    name = (form.get('name') or '').strip()[:120]
    area_id = _int(form.get('area'))
    if not area_id or not db.execute('SELECT 1 FROM areas WHERE id = ?', (area_id,)).fetchone():
        raise WaitlistError('Pick the area you’re in.')
    category_id = _int(form.get('category'))
    if category_id and not db.execute('SELECT 1 FROM categories WHERE id = ?', (category_id,)).fetchone():
        category_id = None
    if side == 'trade' and not category_id:
        raise WaitlistError('Pick the trade you do.')
    if side == 'customer':
        category_id = None          # a homeowner picking a trade would be noise

    now = ts(at or utcnow())
    existing = db.execute('SELECT id FROM waitlist WHERE lower(email) = ? AND side = ?',
                          (email, side)).fetchone()
    if existing:
        db.execute('UPDATE waitlist SET name = ?, area_id = ?, category_id = ? WHERE id = ?',
                   (name, area_id, category_id, existing['id']))
    else:
        db.execute('INSERT INTO waitlist (side, email, name, area_id, category_id, created_at) '
                   'VALUES (?,?,?,?,?,?)', (side, email, name, area_id, category_id, now))
    db.commit()
    return {'side': side, 'area_id': area_id, 'category_id': category_id,
            'again': bool(existing), 'nearby': nearby(db, side, area_id, category_id)}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── What to show somebody who just joined ─────────────────────────────────────

def nearby(db, side, area_id, category_id=None):
    """How full their corner of the map is, from their side of it.

    A tradie cares how many homeowners are waiting near them; a homeowner cares
    how many tradies. Each is told the number that answers the question they
    actually have, plus how many of their own kind are ahead of them, because
    pretending they're the only one is the sort of small lie that gets found out.
    """
    area = db.execute('SELECT name, region FROM areas WHERE id = ?', (area_id,)).fetchone()
    same = db.execute('SELECT COUNT(*) AS n FROM waitlist WHERE side = ? AND area_id = ?',
                      (side, area_id)).fetchone()['n'] or 0
    other_side = 'trade' if side == 'customer' else 'customer'
    other = db.execute('SELECT COUNT(*) AS n FROM waitlist WHERE side = ? AND area_id = ?',
                       (other_side, area_id)).fetchone()['n'] or 0
    # Trades already signed up and paying count towards a region being ready —
    # they're the supply, whether they came through the waitlist or before it.
    live_trades = db.execute(
        'SELECT COUNT(DISTINCT t.user_id) AS n FROM trades t JOIN trade_areas ta ON ta.trade_id = t.user_id '
        "WHERE ta.area_id = ? AND t.sub_status = 'active'", (area_id,)).fetchone()['n'] or 0
    trades = (same if side == 'trade' else other) + live_trades
    customers = same if side == 'customer' else other
    return {
        'area': area['name'] if area else '',
        'region': area['region'] if area else '',
        'same_side': same,
        'trades': trades,
        'customers': customers,
        'ready': trades >= READY_TRADES and customers >= READY_CUSTOMERS,
        'needs_trades': max(0, READY_TRADES - trades),
        'needs_customers': max(0, READY_CUSTOMERS - customers),
    }


# ── What the admin needs to decide when to open ───────────────────────────────

def by_area(db):
    """Every area anybody is waiting in, nearest to ready first.

    Sorted by how close it is rather than by how many names it has, because the
    question isn't "where are people" — it's "where could we open on Monday".
    """
    rows = db.execute(
        'SELECT a.id, a.name, a.region, '
        "  SUM(CASE WHEN w.side = 'trade' THEN 1 ELSE 0 END) AS trades, "
        "  SUM(CASE WHEN w.side = 'customer' THEN 1 ELSE 0 END) AS customers, "
        '  MAX(w.created_at) AS latest '
        'FROM waitlist w JOIN areas a ON a.id = w.area_id '
        'GROUP BY a.id, a.name, a.region').fetchall()
    out = []
    for r in rows:
        live = db.execute(
            'SELECT COUNT(DISTINCT t.user_id) AS n FROM trades t '
            'JOIN trade_areas ta ON ta.trade_id = t.user_id '
            "WHERE ta.area_id = ? AND t.sub_status = 'active'", (r['id'],)).fetchone()['n'] or 0
        trades = (r['trades'] or 0) + live
        customers = r['customers'] or 0
        short = max(0, READY_TRADES - trades) + max(0, READY_CUSTOMERS - customers)
        out.append({'id': r['id'], 'name': r['name'], 'region': r['region'],
                    'waiting_trades': r['trades'] or 0, 'live_trades': live,
                    'trades': trades, 'customers': customers, 'latest': r['latest'],
                    'short': short, 'ready': short == 0})
    out.sort(key=lambda a: (a['short'], -(a['trades'] + a['customers'])))
    return out


def trades_wanted(db, area_id=None):
    """Which trades people are waiting for, so outreach knows who to go and find."""
    sql = ('SELECT c.name, COUNT(*) AS n FROM waitlist w JOIN categories c ON c.id = w.category_id '
           "WHERE w.side = 'trade'")
    args = []
    if area_id:
        sql += ' AND w.area_id = ?'
        args.append(area_id)
    sql += ' GROUP BY c.name ORDER BY n DESC, c.name'
    return [dict(r) for r in db.execute(sql, args).fetchall()]


def totals(db):
    rows = db.execute('SELECT side, COUNT(*) AS n FROM waitlist GROUP BY side').fetchall()
    counts = {r['side']: r['n'] for r in rows}
    return {'customers': counts.get('customer', 0), 'trades': counts.get('trade', 0),
            'total': sum(counts.values())}
