"""
The conversation on a job: attachments, and asking to do extra work.

Two things live here because they belong to the same moment. A tradie lifts a
board, finds three rotten joists, takes a photo and says "this needs doing, it's
another $800". On the phone that's a verbal yes nobody can prove; in a chat it's
a message that gets scrolled past. Here it's a photo and a request with a price
that the customer presses yes or no to, and the answer is kept.

Extra work agreed in conversation and never written down is the single biggest
source of arguments in residential building, and this is the whole reason the
feature exists — not the chat.

Rules this sticks to:

  • **Only the two people on the job.** Files are not served from the public
    uploads folder: every one goes through a route that checks who is asking.
    A photo of somebody's back door is not public.
  • **Either side can ask.** The tradie who finds the rot, and the customer who
    says "while you're here". Same object, same yes or no.
  • **A price is a price.** A request carries an amount and whether GST is in it,
    shown the same way a quote is. "About eight hundred" is how disputes start.
  • **Answering is final, and dated.** An accepted request can't be quietly
    edited afterwards; asking again means asking again, visibly.
  • **It is not a contract.** Level records what the two of them agreed. It
    doesn't hold money, guarantee anything, or take a side if it goes wrong, and
    the page says so where somebody is about to press Accept.
"""
import os

FILE_TYPES = {
    'jpg': 'photo', 'jpeg': 'photo', 'png': 'photo', 'webp': 'photo', 'heic': 'photo',
    'pdf': 'file', 'doc': 'file', 'docx': 'file', 'xls': 'file', 'xlsx': 'file',
    'csv': 'file', 'txt': 'file',
}
MAX_FILES = 6
MAX_BYTES = 12 * 1024 * 1024        # a phone photo is 3–5 MB; a plan set can be bigger
STATUSES = {'asked': 'Waiting', 'accepted': 'Accepted', 'declined': 'Declined', 'withdrawn': 'Withdrawn'}


class ThreadError(Exception):
    """Shown to whoever tried it, as-is."""


def kind_of(filename):
    return FILE_TYPES.get((filename or '').rsplit('.', 1)[-1].lower())


def can_see(job, quote, user_id):
    """Only the customer and the trade who quoted. Nobody else, ever."""
    return bool(job and quote and user_id in (job['customer_id'], quote['trade_id']))


# ── Attachments ─────────────────────────────────────────────────────────────

def placeholder(saved):
    """What a message with files but no words says in the list of threads.

    It has to say *something* — the thread list previews the last message, and a
    blank line there looks broken. Saying "file" about an obvious photo is the
    kind of small wrongness people notice, so this counts what was actually sent.
    """
    photos = sum(1 for f in saved if f['kind'] == 'photo')
    others = len(saved) - photos
    if photos and not others:
        return '(sent a photo)' if photos == 1 else f'(sent {photos} photos)'
    if others and not photos:
        return '(sent a file)' if others == 1 else f'(sent {others} files)'
    return f'(sent {len(saved)} attachments)'


def attach(db, message_id, files, at=None):
    """Record files already saved to disk against a message."""
    from engine import ts, utcnow
    now = ts(at or utcnow())
    for saved in files:
        db.execute('INSERT INTO message_files (message_id, filename, original_name, kind, bytes, '
                   'created_at) VALUES (?,?,?,?,?,?)',
                   (message_id, saved['filename'], saved['original_name'][:160],
                    saved['kind'], saved['bytes'], now))


def files_for(db, message_ids):
    """{message id: [files]} for a whole thread in one query."""
    if not message_ids:
        return {}
    marks = ','.join('?' * len(message_ids))
    out = {mid: [] for mid in message_ids}
    for row in db.execute(f'SELECT * FROM message_files WHERE message_id IN ({marks}) ORDER BY id',
                          list(message_ids)).fetchall():
        item = dict(row)
        item['size'] = _readable(item['bytes'])
        out.setdefault(item['message_id'], []).append(item)
    return out


def _readable(n):
    if not n:
        return ''
    if n < 1024 * 1024:
        return f'{round(n / 1024)} KB'
    return f'{n / 1024 / 1024:.1f} MB'


def file_owner(db, file_id):
    """The job and quote a file belongs to, so a route can check who may see it."""
    return db.execute(
        'SELECT f.filename, f.original_name, f.kind, m.job_id, m.trade_id '
        'FROM message_files f JOIN messages m ON m.id = f.message_id WHERE f.id = ?',
        (file_id,)).fetchone()


# ── Asking to do work ───────────────────────────────────────────────────────

def ask(db, job, trade_id, asked_by, form, message_id=None, at=None):
    """Raise a piece of extra work with a price, for the other side to answer."""
    from engine import ts, utcnow
    title = (form.get('title') or '').strip()
    if len(title) < 4:
        raise ThreadError('Say what the work is, in a few words.')
    detail = (form.get('detail') or '').strip()
    if len(detail) < 10:
        raise ThreadError('Explain what needs doing and why — the other person has to decide on it.')
    amount = _money(form.get('amount'))
    if amount is None or amount <= 0:
        raise ThreadError('Put a price on it. "About eight hundred" is how arguments start.')
    if amount > 1_000_000:
        raise ThreadError('That price looks wrong — check it.')
    db.execute('INSERT INTO work_requests (job_id, trade_id, asked_by, title, detail, amount, '
               "gst_included, status, message_id, created_at) VALUES (?,?,?,?,?,?,?,'asked',?,?)",
               (job['id'], trade_id, asked_by, title[:140], detail[:2000], amount,
                0 if form.get('gst') == 'excl' else 1, message_id, ts(at or utcnow())))
    # Telling the other side belongs here rather than in the route. It was in the
    # website's route alone, and the phone's route was written without it — so a
    # request raised on site reached nobody. A request nobody is told about is
    # just a note to yourself, which is the thing this feature exists to replace.
    _tell(db, _other_side(job, trade_id, asked_by), job, trade_id,
          f'There’s extra work to agree on “{job["title"]}” — have a look and say yes or no.')
    db.commit()


def _other_side(job, trade_id, user_id):
    return trade_id if user_id == job['customer_id'] else job['customer_id']


def _tell(db, user_id, job, trade_id, body):
    from engine import notify
    notify(db, user_id, body, f'/thread/{job["id"]}/{trade_id}')


def _money(value):
    text = str(value or '').strip().replace(',', '').replace('$', '')
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def answer(db, request_id, user_id, decision, job, trade_id, note=None, at=None):
    """Accept, decline or withdraw — as the other person on *this* job.

    `job` and `trade_id` are the thread the caller has already been authorised
    for, and they are required rather than optional on purpose. Looking a request
    up by id alone meant the job and trade in the URL were decoration: anybody
    who could reach a thread of their own could answer a request belonging to two
    strangers, and `work_requests.id` is a sequential integer, so the whole table
    was walkable. This record is the evidence of what two people agreed. A third
    party writing it is the worst thing that could happen to it.

    So the row is fetched scoped to the thread, and the answerer has to be the
    *other side of that thread* — not merely somebody who isn't the asker.
    """
    from engine import ts, utcnow
    row = db.execute('SELECT * FROM work_requests WHERE id = ? AND job_id = ? AND trade_id = ?',
                     (request_id, job['id'], trade_id)).fetchone()
    if not row:
        raise ThreadError('That request has gone.')
    if row['status'] != 'asked':
        raise ThreadError(f'That was already {STATUSES[row["status"]].lower()}.')
    if decision == 'withdrawn':
        if user_id != row['asked_by']:
            raise ThreadError('Only the person who asked can take it back.')
    elif decision in ('accepted', 'declined'):
        # The other side of this job, by name — which also rules out anybody who
        # isn't on it at all, where "not the asker" did not.
        if user_id != _other_side(job, trade_id, row['asked_by']):
            raise ThreadError('The other person has to answer this one.')
    else:
        raise ThreadError('Accept it or decline it.')
    db.execute('UPDATE work_requests SET status = ?, answered_at = ?, answered_note = ? WHERE id = ?',
               (decision, ts(at or utcnow()), (note or '').strip()[:500] or None, request_id))
    word = {'accepted': 'agreed to', 'declined': 'said no to', 'withdrawn': 'withdrawn'}[decision]
    # Whoever asked hears the answer — including when they took it back
    # themselves, so the record of who did what stays on both sides.
    _tell(db, row['asked_by'], job, trade_id, f'“{row["title"]}” on “{job["title"]}” was {word}.')
    db.commit()
    return row


def requests_for(db, job_id, trade_id):
    """Every request on this job between these two, newest first."""
    rows = [dict(r) for r in db.execute(
        'SELECT * FROM work_requests WHERE job_id = ? AND trade_id = ? ORDER BY id DESC',
        (job_id, trade_id)).fetchall()]
    for row in rows:
        row['word'] = STATUSES.get(row['status'], row['status'])
        row['price'] = _price(row)
    return rows


def _price(row):
    amount = row['amount'] or 0
    return f'${amount:,.0f} {"incl. GST" if row["gst_included"] else "plus GST"}'


def extra_agreed(db, job_id, trade_id):
    """What's been said yes to, and what it comes to.

    Shown beside the original quote so nobody is surprised by the final bill —
    which is the whole point of writing any of it down.
    """
    rows = db.execute("SELECT amount, gst_included FROM work_requests WHERE job_id = ? AND trade_id = ? "
                      "AND status = 'accepted'", (job_id, trade_id)).fetchall()
    if not rows:
        return None
    incl = sum((r['amount'] or 0) if r['gst_included'] else round((r['amount'] or 0) * 1.15) for r in rows)
    return {'count': len(rows), 'total_incl_gst': incl}
