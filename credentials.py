"""
Tickets, cards and memberships a tradie can put up.

A licence and public liability are the two things everyone checks. They are not
the only things a tradie has: a Site Safe card, a first aid certificate, a
working-at-height ticket, Master Builders membership. None of it shows up
anywhere, and all of it is the sort of thing a homeowner would like to know.

So a tradie can upload them, we confirm we've seen each one, and each confirmed
one counts towards their score.

Rules this sticks to:

  • **An expired ticket is not a ticket.** This is the whole reason the feature
    is worth building rather than being a folder of photos. A card that ran out
    in March stops counting in March, on its own, and the tradie is told before
    it happens rather than after.
  • We confirm, we don't certify. "We've seen this" is all a badge ever claims.
    Level is not an awarding body and must never read as one.
  • The documents themselves are private. A certificate has somebody's full
    name and a registration number on it; the customer sees that a ticket was
    checked and when it runs out, never the file. Only the tradie and an admin
    can open one.
  • Nothing here affects which jobs anyone is offered. Same rule as the rest of
    the score — see trust.py.
"""
import re
from datetime import date, timedelta

# (key, what it's called, who issues it, whether it normally has an expiry)
KINDS = [
    ('site_safe',    'Site Safe passport',            'Site Safe New Zealand', True),
    ('first_aid',    'First aid certificate',         'NZ Red Cross, St John and others', True),
    ('heights',      'Working at heights',            'Site Safe, Vertical Horizonz and others', True),
    ('confined',     'Confined spaces',               'Site Safe, Vertical Horizonz and others', True),
    ('asbestos',     'Asbestos awareness or removal', 'WorkSafe-recognised trainer', True),
    ('scaffold',     'Scaffolding certificate',       'WorkSafe / Scaffolding & Rigging NZ', True),
    ('elevated',     'Elevated work platform (EWP)',  'EWPA or similar', True),
    ('hazardous',    'Hazardous substances',          'WorkSafe-recognised trainer', True),
    ('gas',          'Gas certification',             'PGDB or Gas Association', True),
    ('electrical',   'Practising licence',            'EWRB', True),
    ('lbp',          'LBP licence card',              'MBIE', True),
    ('membership',   'Trade association membership',  'Master Builders, Certified Builders, Master Plumbers…', True),
    ('insurance',    'Insurance certificate',         'Your insurer', True),
    ('other',        'Something else',                '', False),
]
BY_KEY = {k: (label, issuer, dated) for k, label, issuer, dated in KINDS}

FILE_TYPES = {'pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic'}
MAX_PER_TRADE = 12
EXPIRY_WARN_DAYS = 30          # tell them a month out, while it can still be renewed


class CredentialError(Exception):
    """Shown to the tradie as-is."""


def label_for(kind):
    return BY_KEY.get(kind, ('Something else', '', False))[0]


def _today(at=None):
    return (at.date() if hasattr(at, 'date') else at) or date.today()


def _to_date(text):
    if not text or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(text).strip()):
        return None
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def status(row, at=None):
    """Where one document stands, in the words the tradie and customer read.

    Order matters: expiry beats everything. A confirmed ticket that ran out
    last week is expired, not confirmed, and saying otherwise would be the one
    genuinely misleading thing this feature could do.
    """
    today = _today(at)
    expires = _to_date(row['expires_on'] if 'expires_on' in row.keys() else None)
    checked = row['checked_at'] if 'checked_at' in row.keys() else None
    if expires and expires < today:
        return {'key': 'expired', 'word': 'Expired', 'tone': 'danger',
                'detail': f'Ran out {expires:%d %b %Y}', 'counts': False}
    if not checked:
        return {'key': 'waiting', 'word': 'Waiting on us', 'tone': 'muted',
                'detail': 'We’ll check it and mark it seen', 'counts': False}
    if expires and expires <= today + timedelta(days=EXPIRY_WARN_DAYS):
        return {'key': 'expiring', 'word': 'Runs out soon', 'tone': 'paint',
                'detail': f'Expires {expires:%d %b %Y}', 'counts': True}
    return {'key': 'ok', 'word': 'Checked', 'tone': 'pine',
            'detail': f'Expires {expires:%d %b %Y}' if expires else 'No expiry', 'counts': True}


def for_trade(db, trade_id, at=None):
    """Every document this trade has put up, newest first, with its standing."""
    rows = [dict(r) for r in db.execute(
        'SELECT * FROM trade_documents WHERE trade_id = ? ORDER BY id DESC', (trade_id,)).fetchall()]
    for row in rows:
        row['label'] = label_for(row['kind'])
        row['status'] = status(row, at)
    return rows


def counting(db, trade_id, at=None):
    """The ones that actually count right now: seen by us, and still in date."""
    return [d for d in for_trade(db, trade_id, at) if d['status']['counts']]


def public_list(db, trade_id, at=None):
    """What a customer is shown. Never a file, never a reference number.

    A certificate carries somebody's full name and registration number. That a
    ticket exists and when it runs out is the useful part; the document is not.
    """
    return [{'label': d['label'], 'issuer': d['issuer'], 'expires_on': d['expires_on'],
             'checked_at': d['checked_at']}
            for d in counting(db, trade_id, at)]


def add(db, trade_id, form, filename, at=None):
    """Put one up. The file is optional — some tickets are just a number."""
    from engine import ts, utcnow
    kind = (form.get('kind') or '').strip()
    if kind not in BY_KEY:
        raise CredentialError('Pick what kind of document this is.')
    n = db.execute('SELECT COUNT(*) AS n FROM trade_documents WHERE trade_id = ?', (trade_id,)).fetchone()['n']
    if n >= MAX_PER_TRADE:
        raise CredentialError(f'That’s {MAX_PER_TRADE} already — remove one you don’t need first.')
    expires = (form.get('expires_on') or '').strip() or None
    if expires and not _to_date(expires):
        raise CredentialError('Use the date picker for when it runs out.')
    if expires and _to_date(expires) < _today(at):
        raise CredentialError('That date has already passed, so it wouldn’t count. Check it and try again.')
    name = (form.get('name') or '').strip()[:120] or label_for(kind)
    db.execute('INSERT INTO trade_documents (trade_id, kind, name, issuer, reference, expires_on, '
               'filename, created_at) VALUES (?,?,?,?,?,?,?,?)',
               (trade_id, kind, name, (form.get('issuer') or '').strip()[:120] or None,
                (form.get('reference') or '').strip()[:80] or None, expires, filename,
                ts(at or utcnow())))
    db.commit()


def remove(db, doc_id, trade_id):
    """Take one down. Returns the filename so the caller can tidy up the disk."""
    row = db.execute('SELECT filename FROM trade_documents WHERE id = ? AND trade_id = ?',
                     (doc_id, trade_id)).fetchone()
    if not row:
        raise CredentialError('That’s not one of yours.')
    db.execute('DELETE FROM trade_documents WHERE id = ? AND trade_id = ?', (doc_id, trade_id))
    db.commit()
    return row['filename']


def mark_checked(db, doc_id, ok, note=None, at=None):
    """An admin confirming they have seen the document itself."""
    from engine import ts, utcnow
    db.execute('UPDATE trade_documents SET checked_at = ?, checked_note = ? WHERE id = ?',
               (ts(at or utcnow()) if ok else None, (note or '').strip()[:300] or None, doc_id))
    db.commit()


def waiting_on_us(db):
    """Everything an admin still has to look at."""
    return [dict(r) | {'label': label_for(r['kind'])} for r in db.execute(
        'SELECT d.*, t.business_name FROM trade_documents d JOIN trades t ON t.user_id = d.trade_id '
        'WHERE d.checked_at IS NULL ORDER BY d.id').fetchall()]


def warn_about_expiries(db, at=None):
    """Tell a tradie a month before a ticket runs out, and again the day it does.

    Said once per document per state, because the point is to prompt a renewal,
    not to nag someone who has already decided not to bother.
    """
    from engine import notify, ts, utcnow
    at = at or utcnow()
    today = _today(at)
    told = 0
    rows = db.execute('SELECT * FROM trade_documents WHERE expires_on IS NOT NULL '
                      'AND checked_at IS NOT NULL').fetchall()
    for row in rows:
        expires = _to_date(row['expires_on'])
        if not expires:
            continue
        soon = today + timedelta(days=EXPIRY_WARN_DAYS)
        warned = row['warned_at'] or ''
        if expires < today and warned != 'expired':
            notify(db, row['trade_id'],
                   f'Your {label_for(row["kind"]).lower()} ran out on {expires:%d %B}. It’s stopped counting '
                   'towards how you look to customers — put the new one up when you have it.',
                   '/trade/trust', at)
            db.execute("UPDATE trade_documents SET warned_at = 'expired' WHERE id = ?", (row['id'],))
            told += 1
        elif today <= expires <= soon and not warned:
            days = (expires - today).days
            notify(db, row['trade_id'],
                   f'Your {label_for(row["kind"]).lower()} runs out in {days} day{"s" if days != 1 else ""} '
                   f'({expires:%d %B}). Renew it and put the new one up, and nothing changes on your profile.',
                   '/trade/trust', at)
            db.execute("UPDATE trade_documents SET warned_at = 'soon' WHERE id = ?", (row['id'],))
            told += 1
    db.commit()
    return told
