"""
The trust score.

A number out of 100 shown beside a tradie's name, built from three things:

  1. what we have checked        — licence on the register, NZBN, insurance, ID;
  2. what they have done here    — updates kept, replies, jobs finished, disputes;
  3. what they have volunteered  — a photo, a police vetting result, referees.

Rules this sticks to, because a score that decides someone's income has to be
defensible to the person it is about:

  • Every input is either something the tradie gave us or something they did on
    Level. Nothing is inferred about their private life, and nothing is scraped.
    The Privacy Act's collection principles are the reason, and "we can explain
    every point to the tradie who asks" is the test.
  • The score is shown, never used to decide who is offered a job. Fair rotation
    stays blind to it — that is the whole difference between Level and the
    incumbent, whose rotation is explicitly review-weighted.
  • Nothing is guessed. A thing we have not checked scores zero and says so; it
    never scores against them.
  • It is always shown with its workings. `explain()` returns the same lines the
    tradie sees on their own page and the customer sees on the profile.
  • A new tradie is not "untrustworthy". Below MIN_JOBS the conduct section is
    marked "not enough jobs yet" and the score is reported out of what can
    actually be earned, so day one reads as unproven rather than bad.
"""
import json

# ── what each thing is worth ────────────────────────────────────────────────
# Checked facts, 50. Conduct, 30. Volunteered, 20.
CHECKED = [
    ('licence',   'Licence checked on the public register', 18),
    ('insurance', 'Public liability insurance seen',        14),
    ('nzbn',      'NZBN checked on the Companies Office',   10),
    ('business',  'Business register looked at',             8),
]
CONDUCT = [
    ('updates',   'Progress updates kept on time', 10),
    ('replies',   'Replies to customers',           8),
    ('finished',  'Jobs seen through to the end',   8),
    ('clean',     'No disputes or upheld reports',  4),
]
VOLUNTEERED = [
    ('photo',     'A photo of the person who turns up', 6),
    ('id',        'Photo ID matched to that face',      6),
    ('vetting',   'Police vetting shared',              5),
    ('referees',  'Referees who vouch for them',        3),
]

# How each thing reads inside a sentence, which is not how it reads as a heading.
# "Checked: their licence, insurance and NZBN" — not "licence checked on the
# public register, public liability insurance seen, nzbn".
SHORT = {'licence': 'their licence', 'insurance': 'insurance', 'nzbn': 'their NZBN',
         'business': 'the business register',
         'photo': 'a photo of themselves', 'id': 'photo ID', 'vetting': 'police vetting',
         'referees': 'referees we rang'}

MIN_JOBS = 3          # below this, conduct is "not enough jobs yet" rather than a low mark
GOOD = 75             # at or above this we say so in words
FAIR = 50

BANDS = [(GOOD, 'Well checked'), (FAIR, 'Part way checked'), (0, 'Just getting started')]


def _pct(kept, of):
    return None if not of else kept / of


def _points(rate, worth):
    """Points for a rate, where 100% earns the lot and 60% earns nothing.

    Nobody is perfect and a single missed reply shouldn't gut the score, but a
    tradie who answers half the time shouldn't score for it either.
    """
    if rate is None:
        return None
    return max(0, round(worth * min(1.0, (rate - 0.6) / 0.4)))


# ── the three sections ──────────────────────────────────────────────────────

def checked_facts(trade):
    """What we have verified, and when. Nothing here is the tradie's word for it."""
    have = {
        'licence': trade['licence_checked_at'] if 'licence_checked_at' in trade.keys() else None,
        'insurance': trade['insurance_checked_at'] if 'insurance_checked_at' in trade.keys() else None,
        'nzbn': trade['nzbn_checked_at'] if 'nzbn_checked_at' in trade.keys() else None,
        'business': trade['business_checked_at'] if 'business_checked_at' in trade.keys() else None,
    }
    out = []
    for key, label, worth in CHECKED:
        on = have.get(key)
        out.append({'key': key, 'label': label, 'worth': worth, 'got': worth if on else 0,
                    'when': on, 'done': bool(on)})
    return out


def conduct(db, trade_id, stats=None):
    """How they have behaved on Level. Counted from the database, never estimated."""
    s = stats if stats is not None else record(db, trade_id)
    jobs = s['finished'] + s['underway']
    enough = jobs >= MIN_JOBS
    out = []
    for key, label, worth in CONDUCT:
        rate, note = None, None
        if not enough:
            note = 'not enough jobs yet'
        elif key == 'updates':
            rate = s['update_rate']
            note = None if rate is not None else 'no updates promised yet'
        elif key == 'replies':
            rate = s['reply_rate']
        elif key == 'finished':
            rate = _pct(s['finished'], jobs)
        elif key == 'clean':
            rate = 1.0 if not s['disputes'] else 0.0
        got = _points(rate, worth)
        out.append({'key': key, 'label': label, 'worth': worth, 'got': got or 0,
                    'rate': rate, 'note': note, 'counts': not (note or rate is None)})
    return out


def volunteered(trade, referees=0):
    """Things the tradie chose to add. Each one is opt-in and can be taken away again."""
    keys = trade.keys()

    def col(name):
        return trade[name] if name in keys else None

    have = {
        'photo': col('photo_checked_at') or col('photo'),
        'id': col('id_checked_at'),
        'vetting': col('vetting_at') if (col('vetting_status') or '') == 'seen' else None,
        'referees': referees >= 2 or None,
    }
    out = []
    for key, label, worth in VOLUNTEERED:
        on = have.get(key)
        out.append({'key': key, 'label': label, 'worth': worth, 'got': worth if on else 0,
                    'done': bool(on)})
    return out


# ── the numbers behind the conduct section ──────────────────────────────────

def record(db, trade_id, at=None):
    """Everything the conduct section needs, counted from the tables."""
    import reporting

    row = db.execute("""SELECT
            SUM(CASE WHEN work_done_on IS NOT NULL THEN 1 ELSE 0 END) AS finished,
            SUM(CASE WHEN work_done_on IS NULL THEN 1 ELSE 0 END)     AS underway
        FROM jobs WHERE hired_trade_id = ? AND status = 'hired'""", (trade_id,)).fetchone()
    finished, underway = (row['finished'] or 0), (row['underway'] or 0)

    # The reporting module already works out what was promised and what arrived
    # in time; there is no point keeping a second opinion about it here.
    kept = reporting.trade_record(db, trade_id, at)
    update_rate = (kept['pct'] / 100) if kept else None

    # A thread counts as answered once the trade has said something in it.
    msg = db.execute("""SELECT COUNT(*) AS asked, SUM(answered) AS answered FROM (
                          SELECT MAX(CASE WHEN sender_id = ? THEN 1 ELSE 0 END) AS answered
                          FROM messages WHERE trade_id = ? GROUP BY job_id
                        ) t""", (trade_id, trade_id)).fetchone()
    reply_rate = _pct(msg['answered'] or 0, msg['asked'] or 0)

    # The only dispute signal we hold about a trade: a customer flagging their quote.
    disputes = db.execute('SELECT COUNT(*) AS n FROM quotes WHERE trade_id = ? AND flagged = 1',
                          (trade_id,)).fetchone()['n'] or 0

    return {'finished': finished, 'underway': underway, 'update_rate': update_rate,
            'reply_rate': reply_rate, 'disputes': disputes, 'update_detail': kept}


def referee_count(db, trade_id):
    return db.execute('SELECT COUNT(*) AS n FROM trade_referees WHERE trade_id = ? AND checked_at IS NOT NULL',
                      (trade_id,)).fetchone()['n'] or 0


# ── putting it together ─────────────────────────────────────────────────────

def explain(db, trade):
    """The score and every line behind it — the same thing the tradie and the customer see."""
    tid = trade['user_id']
    stats = record(db, tid)
    sections = [
        {'key': 'checked', 'title': 'What we’ve checked', 'rows': checked_facts(trade),
         'note': 'Verified by us against the public registers and the documents supplied.'},
        {'key': 'conduct', 'title': 'How they work', 'rows': conduct(db, tid, stats),
         'note': 'Counted from what has actually happened on Level.'},
        {'key': 'added', 'title': 'What they’ve added themselves', 'rows': volunteered(trade, referee_count(db, tid)),
         'note': 'Optional. Each one is the tradie’s choice, and they can remove it.'},
    ]
    got = sum(r['got'] for s in sections for r in s['rows'])
    # Conduct a new tradie can't have earned yet is left out of the total, so the
    # score reads as unproven rather than bad.
    possible = sum(r['worth'] for s in sections for r in s['rows']
                   if s['key'] != 'conduct' or r.get('counts') or r.get('got'))
    score = round(got / possible * 100) if possible else 0
    for floor, word in BANDS:
        if score >= floor:
            band = word
            break
    return {'score': score, 'band': band, 'sections': sections, 'stats': stats,
            'proven': stats['finished'] + stats['underway'] >= MIN_JOBS,
            'next': _next_step(sections)}


def _next_step(sections):
    """The single most valuable thing this tradie could do next. Shown only to them."""
    todo = [r for s in sections if s['key'] != 'conduct' for r in s['rows'] if not r['got']]
    if not todo:
        return None
    best = max(todo, key=lambda r: r['worth'])
    return {'label': best['label'], 'worth': best['worth'], 'key': best['key']}


def score_for(db, trade):
    return explain(db, trade)['score']


def refresh(db, at=None, limit=None):
    """Recompute cached scores. Called from the sweep, so no page load waits on it."""
    from engine import ts, utcnow
    now = ts(at or utcnow())
    rows = db.execute('SELECT * FROM trades' + (f' LIMIT {int(limit)}' if limit else '')).fetchall()
    changed = 0
    for trade in rows:
        score = score_for(db, trade)
        if trade['trust_score'] != score:
            changed += 1
        db.execute('UPDATE trades SET trust_score = ?, trust_at = ? WHERE user_id = ?',
                   (score, now, trade['user_id']))
    db.commit()
    return changed


def band_for(score):
    if score is None:
        return 'Not scored yet'
    for floor, word in BANDS:
        if score >= floor:
            return word
    return BANDS[-1][1]


# ── what a customer is told ─────────────────────────────────────────────────

def summary(db, trade):
    """Two or three short lines for a customer, naming only what was actually done."""
    e = explain(db, trade)
    lines = []
    done = [SHORT.get(r['key'], r['label'].lower()) for r in e['sections'][0]['rows'] if r['done']]
    if done:
        lines.append('We’ve checked ' + _and(done) + '.')
    if e['proven']:
        s = e['stats']
        bits = [f"{s['finished']} job{'s' if s['finished'] != 1 else ''} finished through Level"]
        if s['update_rate'] is not None:
            bits.append(f"{round(s['update_rate'] * 100)}% of promised updates on time")
        lines.append('; '.join(bits) + '.')
    else:
        lines.append('New to Level — not enough finished jobs to judge how they work yet.')
    added = [SHORT.get(r['key'], r['label'].lower()) for r in e['sections'][2]['rows'] if r['done']]
    if added:
        lines.append('They’ve also given us ' + _and(added) + '.')
    return {'score': e['score'], 'band': e['band'], 'lines': lines}


def _and(words):
    """"a, b and c" — an Oxford comma is not how anyone writes this out loud."""
    if len(words) == 1:
        return words[0]
    return ', '.join(words[:-1]) + ' and ' + words[-1]


def as_json(db, trade):
    """For the phone app."""
    e = explain(db, trade)
    return {'score': e['score'], 'band': e['band'], 'proven': e['proven'],
            'sections': [{'title': s['title'], 'note': s['note'],
                          'rows': [{'label': r['label'], 'got': r['got'], 'worth': r['worth'],
                                    'note': r.get('note')} for r in s['rows']]}
                         for s in e['sections']]}
