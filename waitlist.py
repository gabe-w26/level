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
import secrets

import config
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
    """Is the front door closed?

    **Shut by default.** It used to be open unless a saved setting said
    otherwise, which meant shipping the waitlist did nothing until somebody
    clicked a button — and when that write silently failed three times, the site
    went on taking sign-ups it couldn't serve. A launch gate that depends on a
    successful database write to engage is a gate that fails open, which is the
    wrong way round: the safe state is the one that doesn't promise anybody
    anything.

    Precedence: a WAITLIST environment variable, then the saved setting, then
    the default. "Off" is now stored as '0' rather than as a missing row, so
    that "somebody opened it" is distinguishable from "nobody has said".
    """
    raw = integrations.get('waitlist')
    if raw in ('0', 'off', 'no', 'false'):
        return False
    if raw in ('1', 'on', 'yes', 'true'):
        return True
    # Read per call, not at import, so a test or a deploy can set it either way
    # without depending on which module got imported first.
    return os.environ.get('WAITLIST_DEFAULT', '1') != '0'


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
    invited_by = (form.get('invited_by') or '').strip().lower()[:40] or None
    existing = db.execute('SELECT id FROM waitlist WHERE lower(email) = ? AND side = ?',
                          (email, side)).fetchone()
    if existing:
        # Never blank out a referral on a repeat visit — somebody arriving a
        # second time without the link still came from whoever sent them first.
        db.execute('UPDATE waitlist SET name = ?, area_id = ?, category_id = ?, '
                   'invited_by = COALESCE(?, invited_by) WHERE id = ?',
                   (name, area_id, category_id, invited_by, existing['id']))
    else:
        db.execute('INSERT INTO waitlist (side, email, name, area_id, category_id, invited_by, '
                   'created_at) VALUES (?,?,?,?,?,?,?)',
                   (side, email, name, area_id, category_id, invited_by, now))
    db.commit()
    return {'side': side, 'area_id': area_id, 'category_id': category_id,
            'again': bool(existing), 'invited_by': invited_by,
            'nearby': nearby(db, side, area_id, category_id)}


def who_invited(db, code):
    """The business or person behind a referral code, for saying so on the page.

    Checks both kinds: a trade's invite code and a customer's share code. Returns
    a plain name or None — an unrecognised code is not an error, just nobody.
    """
    code = (code or '').strip().lower()
    if not code:
        return None
    row = db.execute('SELECT business_name AS name FROM trades t JOIN users u ON u.id = t.user_id '
                     'WHERE t.invite_code = ? AND u.closed_at IS NULL', (code,)).fetchone()
    if row:
        return row['name']
    row = db.execute('SELECT name FROM users WHERE lower(ref_code) = ? AND closed_at IS NULL',
                     (code,)).fetchone()
    return (row['name'] or '').split(' ')[0] if row else None


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
                    'short': short, 'ready': short == 0,
                    'untold': untold(db, r['id'])})
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


# ── Opening an area, and keeping the promise ──────────────────────────────────

def _site_url():
    import integrations
    return (integrations.get('site_url') or '').rstrip('/') or 'https://level.co.nz'


def _letter(row, area_name):
    """The one email these people agreed to. Short, and it does one thing."""
    first = (row['name'] or '').split(' ')[0]
    hello = f'Hi {first},' if first else 'Hi,'
    site = _site_url()
    if row['side'] == 'trade':
        return (f'{area_name} is open on {config.BRAND}', f'''{hello}

{area_name} is open. You asked to know when, so here it is.

Jobs in your trade go out to {config.TRADES_PER_JOB} local businesses at a time, by rotation, and a
job closes once {config.MAX_QUOTES} of you have quoted. There are no lead fees and nothing to buy
per job — one flat monthly price, and if you quote on {config.GUARANTEE_MIN_QUOTES} jobs in a month
and win none of them, that month is refunded.

Set your trade and area up here and you'll start getting jobs:
{site}/signup

It takes about five minutes.''')
    return (f'{config.BRAND} is open in {area_name}', f'''{hello}

{area_name} is open. You asked to know when, so here it is.

Post what needs doing and it goes to up to {config.TRADES_PER_JOB} local trades who do that work.
Up to {config.MAX_QUOTES} of them quote, you compare the quotes side by side, and you pick. It's
free, and trades only see your suburb until you decide to share more.

{site}/post

We waited until there were enough trades here to actually quote your job, which is why this took
as long as it did.''')


def ready_to_open(db):
    """Areas with somebody waiting who hasn't been told yet."""
    return [a for a in by_area(db) if untold(db, a['id'])]


def untold(db, area_id):
    return db.execute('SELECT COUNT(*) AS n FROM waitlist WHERE area_id = ? AND told_at IS NULL',
                      (area_id,)).fetchone()['n'] or 0


def open_area(db, area_id, limit=None, at=None):
    """Tell everyone waiting in one area that it's open.

    Three things this is careful about, all of them the same worry — that the
    single email somebody agreed to turns into two:

      · **Only the untold.** `told_at` is set per person, so running it twice
        emails nobody twice, and a run that dies halfway can simply be run again.
      · **Marked only when it actually went.** A send that fails leaves
        `told_at` null so the next run picks them up, rather than recording a
        promise we didn't keep.
      · **Within the day's sending limit.** Anyone past the cap keeps their null
        and waits for tomorrow, which is slower and honest, rather than being
        dropped on the floor by the provider.
    """
    import mailer
    from engine import ts, utcnow
    now = ts(at or utcnow())
    area = db.execute('SELECT name FROM areas WHERE id = ?', (area_id,)).fetchone()
    if not area:
        raise WaitlistError('No such area.')

    rows = db.execute('SELECT * FROM waitlist WHERE area_id = ? AND told_at IS NULL ORDER BY id',
                      (area_id,)).fetchall()
    if limit:
        rows = rows[:limit]

    sent = failed = 0
    for row in rows:
        token = row['unsub_token'] or secrets.token_urlsafe(24)
        if not row['unsub_token']:
            db.execute('UPDATE waitlist SET unsub_token = ? WHERE id = ?', (token, row['id']))
        subject, body = _letter(row, area['name'])
        ok = mailer.send(row['email'], row['name'] or '', subject, body,
                         unsubscribe_url=f'{_site_url()}/waitlist/stop/{token}')
        if ok:
            db.execute('UPDATE waitlist SET told_at = ? WHERE id = ?', (now, row['id']))
            sent += 1
        else:
            failed += 1
    db.commit()
    return {'area': area['name'], 'sent': sent, 'failed': failed,
            'left': untold(db, area_id)}


def forget(db, token):
    """Take somebody off the list for good. One click, no questions."""
    row = db.execute('SELECT id, email FROM waitlist WHERE unsub_token = ?', (token,)).fetchone()
    if not row:
        return None
    db.execute('DELETE FROM waitlist WHERE id = ?', (row['id'],))
    db.commit()
    return row['email']
