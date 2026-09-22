"""
Progress updates: the trade's promise to keep the customer in the loop.

When quoting, a trade picks how often they'll report if hired — daily (working
days), weekly, monthly, or any mix. The customer sees that promise next to the
price. Once hired, the trade posts updates (text + photos) and every promised
period is scored as kept or missed, so the customer can see at a glance whether
they're being kept informed, and future customers see the trade's record.

Scoring rules (kept deliberately simple so they're easy to explain):
  • daily   = each working day (Mon–Fri) from the start date;
  • weekly  = each Monday–Sunday week;
  • monthly = each calendar month.
A period only counts once it's over (or once it's been reported), so nobody is
marked down for today before today has finished. Periods after the job is
marked finished don't count. One update can cover several at once — e.g. a
Friday update can be both that day's and that week's.
"""
from datetime import date, datetime, timedelta, timezone

from engine import notify, ts, utcnow

try:
    from zoneinfo import ZoneInfo
    NZ = ZoneInfo('Pacific/Auckland')
except Exception:                       # no tz database on this machine
    NZ = timezone(timedelta(hours=12))

KINDS = ('daily', 'weekly', 'monthly')
LABELS = {'daily': 'Daily', 'weekly': 'Weekly', 'monthly': 'Monthly'}
PERIOD_WORD = {'daily': 'days', 'weekly': 'weeks', 'monthly': 'months'}
RECORD_MIN_PERIODS = 5       # a trade's record only shows once there's enough to judge
REMIND_FROM_HOUR = 15        # NZ time: nudge the trade from 3pm if today's update is missing
REMIND_UNTIL_HOUR = 21
MAX_PHOTOS = 6


# ── Plans ─────────────────────────────────────────────────────────────────────

def parse(plan):
    """'weekly,daily' -> ['daily', 'weekly'] (always in a fixed order, unknown values dropped)."""
    chosen = {p.strip() for p in (plan or '').split(',')}
    return [k for k in KINDS if k in chosen]


def clean(values):
    """Form checkboxes -> stored plan string ('' = no promise)."""
    return ','.join(parse(','.join(values or [])))


def describe(plan):
    kinds = parse(plan)
    if not kinds:
        return ''
    words = [LABELS[k].lower() for k in kinds]
    text = words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]
    return text[0].upper() + text[1:] + ' updates'


# ── Dates ─────────────────────────────────────────────────────────────────────

def nz_now(at=None):
    return (at or utcnow()).replace(tzinfo=timezone.utc).astimezone(NZ)


def nz_today(at=None):
    return nz_now(at).date()


def key(kind, d):
    if kind == 'daily':
        return d.isoformat()
    if kind == 'weekly':
        y, w, _ = d.isocalendar()
        return f'{y}-W{w:02d}'
    return f'{d.year}-{d.month:02d}'


def _to_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _periods(kind, start, end):
    """Every period of `kind` that overlaps [start, end], as (key, last_day) in order."""
    out, seen = [], set()
    d = start
    while d <= end:
        if kind == 'daily' and d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        k = key(kind, d)
        if k not in seen:
            seen.add(k)
            if kind == 'daily':
                last = d
            elif kind == 'weekly':
                last = d + timedelta(days=6 - d.weekday())
            else:
                nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                last = nxt - timedelta(days=1)
            out.append((k, last))
        d += timedelta(days=1)
    return out


def _is_due_window(kind, today):
    """Whether the current period's update is 'due' today (for reminders and the badge)."""
    if kind == 'daily':
        return today.weekday() < 5
    if kind == 'weekly':
        return today.weekday() >= 4                     # from Friday
    nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return (nxt - today).days <= 3                      # the last three days of the month


# ── Scoring ───────────────────────────────────────────────────────────────────

def updates_for(db, job_id):
    return db.execute('SELECT * FROM progress_updates WHERE job_id = ? ORDER BY id DESC', (job_id,)).fetchall()


def covered(updates):
    """{kind: set(period keys)} that the posted updates cover."""
    out = {k: set() for k in KINDS}
    for u in updates:
        for kind in parse(u['kinds']):
            out[kind].add(u['period_' + kind])
    return out


def score(job, updates, at=None):
    """Per promised kind: {'kept', 'expected', 'due', 'label'}. Empty when nothing was promised
    or work hasn't started."""
    kinds = parse(job['report_plan'])
    start = _to_date(job['work_started_on'])
    today = nz_today(at)
    if not kinds or not start or start > today:
        return {}
    done = _to_date(job['work_done_on'])
    end = min(done, today) if done else today
    have = covered(updates)
    result = {}
    for kind in kinds:
        expected = kept = 0
        for k, last in _periods(kind, start, end):
            over = last < today or (done is not None and last >= done)
            if over or k in have[kind]:
                expected += 1
                kept += k in have[kind]
        current = key(kind, today)
        due = (not done and start <= today and _is_due_window(kind, today) and current not in have[kind])
        result[kind] = {'kept': kept, 'expected': expected, 'due': due, 'label': LABELS[kind],
                        'word': PERIOD_WORD[kind]}
    return result


def trade_record(db, trade_id, at=None):
    """How well a trade keeps its reporting promises across hired jobs. None until there's enough."""
    jobs = db.execute("SELECT * FROM jobs WHERE hired_trade_id = ? AND status = 'hired' "
                      "AND report_plan IS NOT NULL AND report_plan <> ''", (trade_id,)).fetchall()
    kept = expected = counted = 0
    for j in jobs:
        s = score(j, updates_for(db, j['id']), at)
        if s:
            counted += 1
            kept += sum(v['kept'] for v in s.values())
            expected += sum(v['expected'] for v in s.values())
    if expected < RECORD_MIN_PERIODS:
        return None
    return {'pct': round(100 * kept / expected), 'kept': kept, 'expected': expected, 'jobs': counted}


# ── Actions ───────────────────────────────────────────────────────────────────

class ReportError(Exception):
    pass


def start_job(db, job, quote, at=None):
    """At hire: copy the promise from the accepted quote and set the start date."""
    plan = clean(parse(quote['report_plan'] if 'report_plan' in quote.keys() else ''))
    today = nz_today(at)
    start = _to_date(quote['available_from'])
    start = start if start and start >= today else today
    db.execute('UPDATE jobs SET report_plan = ?, work_started_on = ? WHERE id = ?',
               (plan or None, start.isoformat(), job['id']))


def post_update(db, job, trade_id, body, kinds, photo_names=(), at=None):
    """A trade posts a progress update. Returns the new update id."""
    if job['hired_trade_id'] != trade_id or job['status'] != 'hired':
        raise ReportError('Only the trade hired for this job can post updates.')
    body = (body or '').strip()
    if len(body) < 15:
        raise ReportError('Write a little more — what got done, what’s next, anything the customer needs to know.')
    if len(body) > 4000:
        raise ReportError('Keep it under 4,000 characters.')
    promised = parse(job['report_plan'])
    kinds = [k for k in parse(','.join(kinds or [])) if k in promised]
    today = nz_today(at)
    now_s = ts(at or utcnow())
    db.execute('INSERT INTO progress_updates (job_id, trade_id, kinds, body, local_date, period_daily, '
               'period_weekly, period_monthly, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
               (job['id'], trade_id, ','.join(kinds), body, today.isoformat(), key('daily', today),
                key('weekly', today), key('monthly', today), now_s))
    uid = db.execute('SELECT id FROM progress_updates WHERE job_id = ? AND trade_id = ? AND created_at = ? '
                     'ORDER BY id DESC', (job['id'], trade_id, now_s)).fetchone()['id']
    for name in photo_names:
        db.execute('INSERT INTO progress_photos (update_id, filename, created_at) VALUES (?,?,?)',
                   (uid, name, now_s))
    what = describe(','.join(kinds)).replace(' updates', ' update').lower() if kinds else 'progress update'
    notify(db, job['customer_id'], f'New {what} on “{job["title"]}”.', f'/me/jobs/{job["id"]}', at)
    db.commit()
    return uid


def set_start(db, job, trade_id, value):
    if job['hired_trade_id'] != trade_id:
        raise ReportError('Only the hired trade can change the start date.')
    d = _to_date(value)
    if not d:
        raise ReportError('Pick a start date.')
    hired_on = _to_date(job['closed_at'])
    if hired_on and d < hired_on - timedelta(days=1):
        raise ReportError('The start date can’t be before you were hired.')
    if updates_for(db, job['id']) and d > nz_today():
        raise ReportError('Updates have already started, so the start date can’t move into the future.')
    db.execute('UPDATE jobs SET work_started_on = ? WHERE id = ?', (d.isoformat(), job['id']))
    notify(db, job['customer_id'], f'Start date for “{job["title"]}” set to {d:%-d %b}.', f'/me/jobs/{job["id"]}')
    db.commit()


def finish(db, job, by_user_id, at=None):
    """Either side marks the work finished; the reporting schedule stops."""
    if job['status'] != 'hired':
        raise ReportError('Only a job someone was hired for can be marked finished.')
    if job['work_done_on']:
        return
    db.execute('UPDATE jobs SET work_done_on = ? WHERE id = ?', (nz_today(at).isoformat(), job['id']))
    other = job['customer_id'] if by_user_id == job['hired_trade_id'] else job['hired_trade_id']
    link = f'/me/jobs/{job["id"]}' if other == job['customer_id'] else f'/trade/jobs/{job["id"]}'
    notify(db, other, f'“{job["title"]}” was marked as finished.', link, at)
    db.commit()


def remind(db, at=None):
    """From 3pm NZ, nudge trades whose promised update for today/this week/this month is missing.
    Once per period."""
    now = nz_now(at)
    if not REMIND_FROM_HOUR <= now.hour < REMIND_UNTIL_HOUR:
        return 0
    sent = 0
    jobs = db.execute("SELECT * FROM jobs WHERE status = 'hired' AND report_plan IS NOT NULL AND report_plan <> '' "
                      "AND work_done_on IS NULL").fetchall()
    for job in jobs:
        s = score(job, updates_for(db, job['id']), at)
        for kind, v in s.items():
            if not v['due']:
                continue
            period = key(kind, now.date())
            if db.execute('SELECT 1 FROM progress_reminders WHERE job_id = ? AND kind = ? AND period = ?',
                          (job['id'], kind, period)).fetchone():
                continue
            db.execute('INSERT INTO progress_reminders (job_id, kind, period, sent_at) VALUES (?,?,?,?)',
                       (job['id'], kind, period, ts(at or utcnow())))
            when = {'daily': 'today', 'weekly': 'this week', 'monthly': 'this month'}[kind]
            notify(db, job['hired_trade_id'],
                   f'Your {kind} update for “{job["title"]}” is due {when}. A couple of lines and a photo is plenty.',
                   f'/trade/jobs/{job["id"]}#updates', at)
            sent += 1
    db.commit()
    return sent
