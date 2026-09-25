"""
The worksite: site information, site notes, and the pre-start check.

Level goes quiet at exactly the wrong moment. A homeowner accepts a quote, and
then the two people who now have to work together have nowhere to put any of
the practical stuff — where the gate is, which door, whether the dog bites,
where the water shut-off is. So it gets texted, or it doesn't get said at all
and the tradie turns up to a locked gate. This is the small slice of Docket
that belongs on a won job:

  • site information — seven plain questions either side can fill in;
  • site notes — a running log with photos, some of it the trade's own;
  • the pre-start check — what the tradie found when they arrived.

Rules this sticks to:
  1. This is a record, not advice. It never tells anyone a site is safe. It
     records what the tradesperson said they found on the day, in their words,
     and shows it to the customer unchanged.
  2. Nothing here is a substitute for the tradie's own legal duties under the
     Health and Safety at Work Act. Level is not the PCBU and does not pretend
     to be one.
  3. Only the two people on the job — the customer and the trade they hired —
     can read or write any of it. There is no public view, and nothing here
     appears on a profile or in search.
  4. A job with none of this filled in behaves exactly as it does today.
     Everything is optional, nothing is a gate, and an empty worksite renders
     as an invitation rather than a warning.

The street address is the one field neither side types. It is copied from the
job's own address column, because a site record that names the wrong house is
worse than one that names none.

All timestamps are UTC strings computed in Python (see schema.py).
"""
import json

from engine import notify, parse_ts, ts, utcnow

MAX_PHOTOS = 6
MAX_BODY = 2000
MIN_BODY = 2
MAX_FIELD = 2000
DELETE_WINDOW_MINUTES = 30      # long enough to fix a typo, short enough that the log stays honest

DISCLAIMER = ('This is a record of what the tradesperson said they found on the day. It is not a '
              'safety certificate and it does not replace anyone’s duties under the Health and '
              'Safety at Work Act 2015.')


class SiteError(Exception):
    """The worksite refused the action. The message is shown to the user."""


# ── Site information ──────────────────────────────────────────────────────────

# (key, label, hint). The labels are what a homeowner would say out loud, not
# what a site induction form would call it — this gets filled in on a phone, by
# someone who isn't in the trade, or it doesn't get filled in at all.
SITE_FIELDS = [
    ('address',     'The address',
     'Taken from the job — only you can change it, on the job itself'),
    ('access',      'Getting in',
     'Side gate code, which door, is anyone home'),
    ('parking',     'Parking and unloading',
     'Where a van fits, the driveway, whether it’s a permit street'),
    ('pets',        'Pets and people',
     'Dog in the back yard, cat that bolts, kids home after three'),
    ('hazards',     'Anything to watch out for',
     'Steep drive, low beam, dodgy step, known asbestos, the neighbour’s fence'),
    ('power_water', 'Power and water',
     'Which outlet to use, where the tap is, where the mains switch and toby are'),
    ('notes',       'Anything else',
     'Best time to come, where to leave the key, how you’d rather be contacted'),
]

SITE_KEYS = [key for key, _, _ in SITE_FIELDS]

# The customer types everything except this one. See the module docstring.
_WRITEABLE_KEYS = [k for k in SITE_KEYS if k != 'address']


def _blank_site(job_id=None):
    site = {key: '' for key in SITE_KEYS}
    site.update({'job_id': job_id, 'updated_by': None, 'updated_at': ''})
    return site


def get_site(db, job_id):
    """The site record for a job, or an empty one, so a template never branches."""
    row = db.execute('SELECT * FROM job_sites WHERE job_id = ?', (job_id,)).fetchone()
    if row is None:
        return _blank_site(job_id)
    # Normalise NULLs away for the same reason: '' renders, None prints "None".
    site = dict(row)
    for key in SITE_KEYS + ['updated_at']:
        site[key] = site.get(key) or ''
    return site


def site_done(site):
    """How many of the seven fields have something in them, for a '3 of 7' nudge."""
    if not site:
        return 0
    site = site if isinstance(site, dict) else dict(site)      # a raw row works too
    return sum(1 for key in SITE_KEYS if str(site.get(key) or '').strip())


def is_customer(job, user_id):
    return user_id is not None and job['customer_id'] == user_id


def is_hired_trade(job, user_id):
    return user_id is not None and job['hired_trade_id'] == user_id


def can_view(job, user_id):
    """Rule 3, in one place. Every route showing any of this must ask first."""
    return is_customer(job, user_id) or is_hired_trade(job, user_id)


def _require_both(job, user_id, what):
    if not can_view(job, user_id):
        raise SiteError(f'Only the customer and the trade hired for this job can {what}.')


def _clean_field(value, label):
    value = (value or '').strip()
    if len(value) > MAX_FIELD:
        raise SiteError(f'“{label}” is too long — keep it under {MAX_FIELD:,} characters.')
    return value


def save_site(db, job, user_id, form, at=None):
    """Either side fills in (or corrects) the site information. Upserts one row per job."""
    _require_both(job, user_id, 'fill in the site details')
    values = {}
    for key, label, _ in SITE_FIELDS:
        if key == 'address':
            continue
        values[key] = _clean_field(form.get(key) if hasattr(form, 'get') else form[key], label)

    # The address is never taken from the form. A trade mistyping a street number
    # into a record the customer then trusts is the failure worth designing out.
    values['address'] = (job['address'] or '').strip()

    now_s = ts(at or utcnow())
    exists = db.execute('SELECT job_id FROM job_sites WHERE job_id = ?', (job['id'],)).fetchone()
    if exists:
        sets = ', '.join(f'{k} = ?' for k in SITE_KEYS)
        db.execute(f'UPDATE job_sites SET {sets}, updated_by = ?, updated_at = ? WHERE job_id = ?',
                   [values[k] for k in SITE_KEYS] + [user_id, now_s, job['id']])
    else:
        cols = ', '.join(SITE_KEYS)
        marks = ','.join('?' * len(SITE_KEYS))
        db.execute(f'INSERT INTO job_sites (job_id, {cols}, updated_by, updated_at) '
                   f'VALUES (?,{marks},?,?)',
                   [job['id']] + [values[k] for k in SITE_KEYS] + [user_id, now_s])
    db.commit()
    return get_site(db, job['id'])


# ── Site notes ────────────────────────────────────────────────────────────────

def add_note(db, job, author_id, body, photos=(), shared=True, at=None):
    """A note on the job's running log. Returns the new note id.

    `shared=False` is the trade's own note — their record of the day, which the
    customer never sees. A customer's note is always shared: they have the
    messages thread for anything they don't want the trade reading, and a
    private note nobody can ever see is just a trap.
    """
    _require_both(job, author_id, 'write site notes')
    body = (body or '').strip()
    if len(body) < MIN_BODY:
        raise SiteError('Write the note first.')
    if len(body) > MAX_BODY:
        raise SiteError(f'Keep the note under {MAX_BODY:,} characters.')
    photos = [p for p in (photos or ()) if p]
    if len(photos) > MAX_PHOTOS:
        raise SiteError(f'Add up to {MAX_PHOTOS} photos.')
    shared = True if is_customer(job, author_id) else bool(shared)

    now_s = ts(at or utcnow())
    db.execute('INSERT INTO site_notes (job_id, author_id, body, shared, created_at) VALUES (?,?,?,?,?)',
               (job['id'], author_id, body, 1 if shared else 0, now_s))
    note_id = db.execute('SELECT id FROM site_notes WHERE job_id = ? AND author_id = ? AND created_at = ? '
                         'ORDER BY id DESC', (job['id'], author_id, now_s)).fetchone()['id']
    for name in photos:
        db.execute('INSERT INTO site_note_photos (note_id, filename, created_at) VALUES (?,?,?)',
                   (note_id, name, now_s))

    # Only a shared note is worth a nudge — a private one is nobody else's business.
    if shared:
        other = job['customer_id'] if author_id == job['hired_trade_id'] else job['hired_trade_id']
        if other:
            link = f'/me/jobs/{job["id"]}' if other == job['customer_id'] else f'/trade/jobs/{job["id"]}'
            notify(db, other, f'New site note on “{job["title"]}”.', link + '#site', at)
    db.commit()
    return note_id


def notes_for(db, job_id, viewer_id=None, is_customer=False):
    """The log, newest first, with each note's photos attached as `photos`.

    A customer never sees the trade's private notes — that filter is in the SQL
    rather than the template, so a new template can't leak them by accident.
    """
    sql = 'SELECT * FROM site_notes WHERE job_id = ?'
    if is_customer:
        sql += ' AND shared = 1'
    rows = db.execute(sql + ' ORDER BY id DESC', (job_id,)).fetchall()
    notes = [dict(r) for r in rows]
    if not notes:
        return notes

    marks = ','.join('?' * len(notes))
    photos = {n['id']: [] for n in notes}
    for row in db.execute(f'SELECT * FROM site_note_photos WHERE note_id IN ({marks}) ORDER BY id',
                          [n['id'] for n in notes]).fetchall():
        photo = dict(row)
        photos[photo['note_id']].append(photo)

    now = utcnow()
    for n in notes:
        n['photos'] = photos.get(n['id'], [])
        n['mine'] = viewer_id is not None and n['author_id'] == viewer_id
        n['can_delete'] = n['mine'] and _within_window(n['created_at'], now)
    return notes


def _within_window(created_at, now=None):
    written = parse_ts(created_at)
    if not written:
        return False
    return ((now or utcnow()) - written).total_seconds() <= DELETE_WINDOW_MINUTES * 60


def delete_note(db, note_id, user_id):
    """The author can take a note back within half an hour. Returns the orphaned
    photo filenames so the caller can tidy the upload folder."""
    note = db.execute('SELECT * FROM site_notes WHERE id = ?', (note_id,)).fetchone()
    if not note:
        raise SiteError('That note has already gone.')
    if note['author_id'] != user_id:
        raise SiteError('Only whoever wrote a note can delete it.')
    if not _within_window(note['created_at']):
        raise SiteError(f'Notes can only be deleted within {DELETE_WINDOW_MINUTES} minutes of writing them. '
                        'Add another note with the correction instead.')
    names = [dict(p)['filename'] for p in
             db.execute('SELECT filename FROM site_note_photos WHERE note_id = ?', (note_id,)).fetchall()]
    db.execute('DELETE FROM site_note_photos WHERE note_id = ?', (note_id,))
    db.execute('DELETE FROM site_notes WHERE id = ?', (note_id,))
    db.commit()
    return names


# ── The pre-start check ───────────────────────────────────────────────────────

ANSWERS = ('yes', 'no', 'na')
ANSWER_LABELS = {'yes': 'Yes', 'no': 'No', 'na': 'Not applicable'}

# Hazards worth naming out loud on New Zealand residential work, because these
# are the ones that turn a small job into a notifiable event.
COMMON_HAZARDS = ('asbestos (anything built or renovated before 2000)', 'lead paint',
                  'live wiring', 'working at height', 'confined space')

# (key, question, why). `why` is one clause saying what the question prevents —
# a tradie ticking boxes deserves to know why the box is there.
CHECK_ITEMS = [
    ('as_described',   'Is the site what the job described?',
     'so a change of scope is raised now, not argued about later'),
    ('access',         'Is there safe access to where the work is, and a clear way out?',
     'blocked or broken access is what most site falls have in common'),
    ('power_water',    'Have you found the power and water you need, and the mains switch and toby?',
     'so you can shut it off fast instead of looking for it in an emergency'),
    ('hazards',        'Have you identified the hazards — asbestos (pre-2000), lead paint, live wiring, '
                       'working at height, confined space?',
     'these are the ones that hurt people or need a specialist before anyone starts'),
    ('services',       'If there is any digging, have the underground services been located?',
     'hitting a gas, power or fibre line is expensive at best'),
    ('neighbours_pets', 'Are neighbours, pets and anyone else on site sorted?',
     'a gate left open or a blocked driveway ends up costing the job'),
    # Phrased as "have you sorted it", not "does it need one", so that — like
    # every other item here — "no" is the answer that needs attention. A mixed
    # polarity means a tradie answering honestly gets flagged for a good answer.
    ('consent',        'If this needs a building consent or a Record of Work, is that sorted?',
     'restricted building work and unconsented work come back on both of you'),
]

CHECK_KEYS = [key for key, _, _ in CHECK_ITEMS]

# What a 'no' means, written the way the customer should read it — never a raw key.
FLAG_TEXT = {
    'as_described':   'the site isn’t quite what the job described — worth talking through before work starts',
    'access':         'getting to the work isn’t straightforward or safe yet',
    'power_water':    'power, water or the shut-offs haven’t been found on site',
    'hazards':        'hazards haven’t been ruled out — asbestos, lead paint, live wiring, height or confined space',
    'services':       'underground services haven’t been located and there’s digging to do',
    'neighbours_pets': 'neighbours, pets or others on site still need sorting',
    'consent':        'a building consent or Record of Work may still be needed',
}


def _check_item(key):
    for k, question, why in CHECK_ITEMS:
        if k == key:
            return {'key': k, 'question': question, 'why': why}
    return None


def clean_answers(answers):
    """Form values -> {key: 'yes'|'no'|'na'} in CHECK_ITEMS order. Unanswered keys are left out."""
    answers = answers or {}
    out = {}
    for key in CHECK_KEYS:
        value = (answers.get(key) or '').strip().lower() if hasattr(answers, 'get') else ''
        if value in ANSWERS:
            out[key] = value
    return out


def save_check(db, job, trade_id, answers, hazards, notes, at=None):
    """The hired trade records the pre-start check. Returns the new check id.

    Nothing here decides anything. It writes down what the tradesperson said
    they found, and every 'no' is shown to the customer as it was given.
    """
    if not is_hired_trade(job, trade_id):
        raise SiteError('Only the trade hired for this job can record the pre-start check.')
    if job['status'] != 'hired':
        raise SiteError('The pre-start check opens once you’ve been hired for the job.')

    clean = clean_answers(answers)
    missing = [k for k in CHECK_KEYS if k not in clean]
    if missing:
        first = _check_item(missing[0])
        more = f' (and {len(missing) - 1} more)' if len(missing) > 1 else ''
        raise SiteError(f'Still to answer: “{first["question"]}”{more}. '
                        'Answer every one — “not applicable” is a real answer.')

    hazards = _clean_field(hazards, 'Hazards')
    notes = _clean_field(notes, 'Notes')
    now_s = ts(at or utcnow())
    db.execute('INSERT INTO site_checks (job_id, trade_id, answers, hazards, notes, created_at) '
               'VALUES (?,?,?,?,?,?)',
               (job['id'], trade_id, json.dumps(clean), hazards or None, notes or None, now_s))
    check_id = db.execute('SELECT id FROM site_checks WHERE job_id = ? AND trade_id = ? AND created_at = ? '
                          'ORDER BY id DESC', (job['id'], trade_id, now_s)).fetchone()['id']
    notify(db, job['customer_id'],
           f'A pre-start site check was recorded on “{job["title"]}”.',
           f'/me/jobs/{job["id"]}#site', at)
    db.commit()
    return check_id


def _parse_answers(value):
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in CHECK_KEYS and v in ANSWERS}
    try:
        loaded = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: v for k, v in loaded.items() if k in CHECK_KEYS and v in ANSWERS}


def checks_for(db, job_id):
    """Every check on the job, newest first, ready to render.

    Each check gains `answers` (parsed back to a dict), `items` (the questions
    in order, with the answer and its label resolved) and `flags`.
    """
    rows = db.execute('SELECT * FROM site_checks WHERE job_id = ? ORDER BY id DESC', (job_id,)).fetchall()
    out = []
    for row in rows:
        check = dict(row)
        check['answers'] = _parse_answers(check.get('answers'))
        check['items'] = [
            {'key': key, 'question': question, 'why': why,
             'answer': check['answers'].get(key, ''),
             'label': ANSWER_LABELS.get(check['answers'].get(key, ''), 'Not answered')}
            for key, question, why in CHECK_ITEMS
        ]
        check['flags'] = flags(check)
        out.append(check)
    return out


def flags(check):
    """The items answered 'no', as short plain sentences the customer can read.

    An empty list is not a clean bill of health — it only means nothing was
    marked 'no'. See rule 1 in the module docstring.
    """
    if check is None:
        return []
    try:
        raw = check['answers']              # a check row, or one from checks_for
    except (KeyError, IndexError, TypeError):
        raw = check                         # or the answers dict on its own
    answers = _parse_answers(raw)
    return [FLAG_TEXT.get(key, _check_item(key)['question'].rstrip('?').lower())
            for key in CHECK_KEYS if answers.get(key) == 'no']
