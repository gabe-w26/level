"""
What the public register says about a business, and about the people behind it.

The NZBN register is free, public and authoritative, and it answers a question
no review ever will: has this person done this before, and how did it end? A
builder on their fourth company, three of which were liquidated, is not
something you learn from five stars and a nice van.

Three rules this sticks to, and they are all the same rule — that a public
register is evidence, not a verdict:

  · **Facts, with where they came from and when.** Never a score, never a
    judgement. "Three companies, two removed, per the Companies Office,
    checked 29 Sep" is something a person can act on and argue with. "Risk: 7"
    is not.
  · **Nothing negative is published automatically.** What the register says
    about somebody's past companies goes to an admin to read, not onto a public
    profile. Publishing a judgement about a named, identifiable business is a
    thing you can be sued for, and the Privacy Act gives them a right to see and
    correct it. Only the plain confirmation — this NZBN exists, is registered,
    and matches this name — is safe to surface on its own.
  · **It never blocks anything.** No key, register down, name won't match: the
    site behaves exactly as it did before, which is with a human doing the
    checking.

Getting a key: register at api.business.govt.nz, which is free. The endpoint and
auth header are settings rather than constants because that API has moved before
and will again — see BASE and AUTH_HEADER below.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

BASE = os.environ.get('NZBN_API_BASE', 'https://api.business.govt.nz/services/v5/nzbn')
AUTH_HEADER = os.environ.get('NZBN_AUTH_HEADER', 'Ocp-Apim-Subscription-Key')
TIMEOUT = 8

# Register statuses that mean the company is no longer trading. Kept as a set
# rather than a cleverer rule because the register's wording is what it is.
ENDED = {'removed', 'liquidation', 'in liquidation', 'receivership', 'struck off',
         'dissolved', 'deregistered', 'voluntary administration'}


def key():
    import integrations
    return (integrations.get('nzbn_key') or '').strip()


def configured():
    return bool(key())


def clean(nzbn):
    """Digits only. People paste them with spaces, dashes and an NZBN: prefix."""
    return re.sub(r'\D', '', nzbn or '')[:13]


# ── Talking to the register ──────────────────────────────────────────────────

class RegisterDown(Exception):
    """We could not ask. Emphatically not the same as the register saying no.

    Recording "no such NZBN" when the truth is "the API timed out" writes a
    permanent note that a real business is bogus, on the strength of somebody
    else's bad afternoon. So the two are different exits, and only one of them
    gets written down.
    """


def _get(path):
    """One call. Returns parsed JSON, None for "no such thing", raises if we
    couldn't ask at all."""
    if not configured():
        raise RegisterDown('no key')
    req = urllib.request.Request(f'{BASE}{path}', headers={
        AUTH_HEADER: key(), 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise RegisterDown(f'status {resp.status}')
            return json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                      # asked, and there is no such entity
        raise RegisterDown(str(e))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            json.JSONDecodeError) as e:
        raise RegisterDown(str(e))


def entity(nzbn):
    """The register's record for one NZBN, flattened to what we actually use."""
    raw = _get(f'/entities/{clean(nzbn)}')
    return _flatten(raw) if raw else None


def _flatten(raw):
    """Pull the few fields we care about out of a big, changeable payload.

    Written defensively on purpose: the register returns a lot, the shape has
    changed before, and a missing key should mean "we don't know" rather than a
    500 on an admin page.
    """
    def first(value):
        if isinstance(value, list):
            return value[0] if value else None
        return value

    name = raw.get('entityName') or raw.get('tradingNames') or ''
    if isinstance(name, list):
        name = (first(name) or {}).get('name', '') if name and isinstance(name[0], dict) else ''
    status = (raw.get('entityStatusDescription') or raw.get('entityStatusCode') or '')
    return {
        'nzbn': raw.get('nzbn') or '',
        'name': (name or '').strip(),
        'status': str(status).strip(),
        'type': (raw.get('entityTypeDescription') or '').strip(),
        'registered_on': (raw.get('registrationDate') or '')[:10],
        'ended': _has_ended(status),
        'directors': _directors(raw),
    }


def _has_ended(status):
    s = str(status or '').strip().lower()
    return any(word in s for word in ENDED)


def _directors(raw):
    out = []
    for role in raw.get('roles') or []:
        if (role.get('roleType') or '').lower() not in ('director', 'directors'):
            continue
        person = role.get('rolePerson') or {}
        name = ' '.join(x for x in [person.get('firstName'), person.get('middleNames'),
                                    person.get('lastName')] if x).strip()
        name = name or (person.get('fullName') or '').strip()
        if name:
            out.append({'name': name, 'from': (role.get('roleStartDate') or '')[:10]})
    return out


def search(name):
    """Entities matching a name. Used to find a director's other companies.

    A search that can't be run returns nothing rather than bringing the whole
    check down — the entity itself is the part that matters, and a missing
    history is reported as a history we don't have.
    """
    try:
        raw = _get('/entities?search-term=' + urllib.parse.quote(name or ''))
    except RegisterDown:
        return []
    if not raw:
        return []
    items = raw.get('items') if isinstance(raw, dict) else raw
    return [_flatten(i) for i in (items or []) if isinstance(i, dict)]


# ── What we make of it ───────────────────────────────────────────────────────

def history(nzbn):
    """This business, plus every other company its directors have run.

    The number that matters is at the bottom: how many of those ended badly.
    It is reported, never judged — "two of four removed" is a fact an admin can
    read and a tradie can explain. Anything stronger is our opinion, and our
    opinion is not something the register said.
    """
    this = entity(nzbn)
    if not this:
        return None
    others, seen = [], {clean(nzbn)}
    for d in this['directors']:
        for company in search(d['name']):
            if clean(company['nzbn']) in seen or not company['nzbn']:
                continue
            seen.add(clean(company['nzbn']))
            others.append(dict(company, director=d['name']))
    return {
        'entity': this,
        'others': others,
        'ended': [c for c in others if c['ended']],
        'ended_count': sum(1 for c in others if c['ended']),
        'total': len(others) + 1,
    }


def name_matches(claimed, registered):
    """Is the business name near enough to the register's?

    Deliberately forgiving about the noise — Ltd, Limited, The, punctuation,
    case — and deliberately strict about everything else. A near-match is
    reported as a near-match for a person to look at, never silently accepted.
    """
    def tidy(s):
        s = (s or '').lower()
        s = re.sub(r'\b(limited|ltd|the|nz|new zealand|co|company)\b', ' ', s)
        return re.sub(r'[^a-z0-9]+', '', s)
    a, b = tidy(claimed), tidy(registered)
    if not a or not b:
        return False
    return a == b or a in b or b in a


# ── Recording what it said, and when ─────────────────────────────────────────

def run(db, trade, by=None, at=None):
    """Check one business against the register and keep a dated snapshot.

    Returns the snapshot, or None when there's nothing to check or nobody to
    ask. It updates the trade's own NZBN fields only on the plain, checkable
    facts — the number exists, is registered, and matches the name they gave.
    Everything about the people behind it is recorded for an admin to read and
    changes nothing on its own.
    """
    from engine import ts, utcnow
    now = ts(at or utcnow())
    nzbn = clean(trade['nzbn'] if 'nzbn' in trade.keys() else '')
    if not nzbn or not configured():
        return None

    try:
        found = history(nzbn)
    except RegisterDown:
        return None            # couldn't ask; nothing is recorded, nothing is implied
    if not found:
        db.execute('INSERT INTO company_checks (trade_id, nzbn, found, checked_at, checked_by) '
                   'VALUES (?,?,0,?,?)', (trade['user_id'], nzbn, now, by))
        # Gone from the register entirely — same reasoning as a struck-off one.
        _drop_the_badge(db, trade['user_id'], 'not on the register', now)
        db.commit()
        return {'found': False, 'nzbn': nzbn, 'checked_at': now}

    e = found['entity']
    matched = name_matches(trade['business_name'], e['name'])
    db.execute(
        'INSERT INTO company_checks (trade_id, nzbn, found, name_matched, registered_name, status, '
        'registered_on, other_companies, ended_companies, detail, checked_at, checked_by) '
        'VALUES (?,?,1,?,?,?,?,?,?,?,?,?)',
        (trade['user_id'], nzbn, 1 if matched else 0, e['name'], e['status'], e['registered_on'],
         len(found['others']), found['ended_count'],
         json.dumps({'others': found['others'], 'directors': e['directors']}), now, by))

    # The only thing that moves on its own: the register agrees this number is
    # registered to this name. A judgement about the people behind it is for a
    # person to make, so it waits for one.
    if matched and not e['ended']:
        db.execute('UPDATE trades SET nzbn_status = ?, nzbn_registered_on = ?, nzbn_checked_at = ? '
                   'WHERE user_id = ?', (e['status'], e['registered_on'], now, trade['user_id']))
    elif e['ended']:
        # And the one thing it takes away. "NZBN checked on the Companies
        # Office" is a badge customers see and a claim we make on our own
        # behalf — leaving it up for a company the register now calls struck
        # off turns a true statement into a false one, quietly, with nobody
        # having done anything. The admin still has the whole history below it.
        _drop_the_badge(db, trade['user_id'], e['status'], now)
    db.commit()
    return {'found': True, 'nzbn': nzbn, 'name_matched': matched, 'entity': e,
            'others': found['others'], 'ended_count': found['ended_count'], 'checked_at': now}


def _drop_the_badge(db, trade_id, status, now):
    """Take the public "NZBN checked" badge down, and record why."""
    row = db.execute('SELECT nzbn_checked_at FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    if not row or not row['nzbn_checked_at']:
        return False
    db.execute('UPDATE trades SET nzbn_checked_at = NULL, nzbn_status = ? WHERE user_id = ?',
               (status, trade_id))
    return True


def latest(db, trade_id):
    """The most recent check for one business, with its detail unpacked."""
    row = db.execute('SELECT * FROM company_checks WHERE trade_id = ? ORDER BY id DESC LIMIT 1',
                     (trade_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out.update(json.loads(out.get('detail') or '{}'))
    except (ValueError, TypeError):
        out['others'] = []
    return out


def worth_a_look(check):
    """Should an admin read this one properly?

    Not a score and not a refusal — a reason to look, in words. Everything here
    is a thing the register said; whether it matters is somebody's judgement,
    and this does not pretend to make it.
    """
    if not check or not check.get('found'):
        return []
    reasons = []
    if not check.get('name_matched'):
        reasons.append('The registered name doesn’t match the business name they gave.')
    if _has_ended(check.get('status')):
        reasons.append(f'The register lists this company as {check["status"]}.')
    ended = check.get('ended_companies') or 0
    if ended:
        total = (check.get('other_companies') or 0) + 1
        reasons.append(f'{ended} of {total} companies connected to the directors are removed, '
                       'in liquidation or struck off.')
    return reasons


# ── Keeping it current ───────────────────────────────────────────────────────

RECHECK_DAYS = 90
PER_SWEEP = 3          # gentle on a free government API, and there is no hurry


def due(db, at=None, limit=PER_SWEEP):
    """Businesses with an NZBN that nobody has asked about lately.

    Never-checked first, then the stalest. The re-check matters more than it
    looks: a company can go into liquidation while it is happily quoting here,
    and the register is the only place that shows up.
    """
    from engine import ts, utcnow
    cutoff = ts((at or utcnow()) - timedelta(days=RECHECK_DAYS))
    return db.execute(
        'SELECT t.* FROM trades t JOIN users u ON u.id = t.user_id '
        "WHERE COALESCE(t.nzbn, '') <> '' AND u.closed_at IS NULL "
        '  AND NOT EXISTS (SELECT 1 FROM company_checks c WHERE c.trade_id = t.user_id '
        '                  AND c.checked_at > ?) '
        'ORDER BY (SELECT MAX(checked_at) FROM company_checks c2 WHERE c2.trade_id = t.user_id) '
        '  IS NOT NULL, '
        '         (SELECT MAX(checked_at) FROM company_checks c3 WHERE c3.trade_id = t.user_id) '
        'LIMIT ?', (cutoff, limit)).fetchall()


def sweep(db, at=None, limit=PER_SWEEP):
    """Check a few businesses, and say something when the answer got worse.

    Only a change is worth an admin's attention. A company that was registered
    last time and is in liquidation now is the thing this exists to catch —
    reporting "still registered" every ninety days would bury it.
    """
    from engine import notify
    done = changed = 0
    for trade in due(db, at=at, limit=limit):
        before = latest(db, trade['user_id'])
        out = run(db, trade, at=at)
        if not out:
            continue                          # couldn't ask; try again next time
        done += 1
        if not _got_worse(before, out):
            continue
        changed += 1
        what = out['entity']['status'] if out.get('found') else 'no longer on the register'
        for a in db.execute("SELECT id FROM users WHERE role = 'admin' AND closed_at IS NULL"):
            notify(db, a['id'],
                   f'The Companies Office now lists {trade["business_name"]} as “{what}”. '
                   'It was fine when we last looked.',
                   f'/admin/trades/{trade["user_id"]}', at=at)
    db.commit()
    return {'checked': done, 'changed': changed}


def _got_worse(before, now):
    """Did this go from fine to not fine? Only that is worth interrupting for."""
    if not before or not before.get('found'):
        return False                          # nothing to compare against
    was_fine = not _has_ended(before.get('status'))
    if not now.get('found'):
        return was_fine                       # it was there, and now it isn't
    return was_fine and _has_ended(now['entity']['status'])


def needing_a_look(db):
    """Every business whose latest register answer gives a reason to read it.

    So this scales past pressing a button on one page at a time. Sorted worst
    first, where "worst" means the most reasons — still not a score, just the
    order a person would work through them in.
    """
    rows = db.execute(
        'SELECT c.* , t.business_name FROM company_checks c '
        'JOIN trades t ON t.user_id = c.trade_id '
        'JOIN users u ON u.id = c.trade_id AND u.closed_at IS NULL '
        'WHERE c.id IN (SELECT MAX(id) FROM company_checks GROUP BY trade_id)').fetchall()
    out = []
    for row in rows:
        reasons = worth_a_look(dict(row))
        if reasons:
            out.append({'trade_id': row['trade_id'], 'name': row['business_name'],
                        'checked_at': row['checked_at'], 'reasons': reasons})
    out.sort(key=lambda r: (-len(r['reasons']), r['name']))
    return out
