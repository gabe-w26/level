"""
Outreach: free leads for trades who aren't on Level yet.

When a job comes in, an admin can email it to local businesses in that trade
from the prospect list (imported from the outreach spreadsheet). Each business
gets its own email with a personal sign-up link and a one-click opt-out.

Kept honest and legal (NZ Unsolicited Electronic Messages Act):
  • only addresses the business published itself, recorded with the source;
  • every email names who it's from and has a working unsubscribe;
  • opt-outs are permanent — an import never switches one back on;
  • at most MAX_LEADS emails to anyone who hasn't answered, one per real job;
  • nothing goes out until an admin presses Send, and only for an open job.
"""
import csv
import io
import os
import re
import secrets
from datetime import timedelta

import config
import integrations
import mailer
from engine import parse_ts, try_lock, ts, utcnow

MAX_LEADS = 3              # stop after this many unanswered leads
SEND_WINDOW_HOURS = 24     # a queued email older than this is dropped, not sent late

# Filling empty slots from the businesses who aren't on Level yet.
AUTO_TOPUP = os.environ.get('AUTO_TOPUP', '1') == '1'
TOPUP_MIN_GAP = 3          # don't email anyone over one or two empty slots
TOPUP_PER_SLOT = 3         # most people who get an email don't sign up today
TOPUP_MAX = 20             # never more than this off the back of one job

STATUSES = {
    'new': 'Not contacted',
    'sent': 'Lead sent',
    'replied': 'Replied',
    'signed_up': 'Signed up',
    'not_interested': 'Not interested',
    'unsubscribed': 'Unsubscribed',
}
OPEN_STATUSES = ('new', 'sent', 'replied')
STOPPED = ('not_interested', 'unsubscribed')
_FROM_SHEET = {label.lower(): key for key, label in STATUSES.items()}

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


# ── Import ────────────────────────────────────────────────────────────────────

def read_upload(filename, data):
    """Rows (dicts keyed by header) from the outreach spreadsheet or a CSV."""
    name = (filename or '').lower()
    if name.endswith('.csv'):
        text = data.decode('utf-8-sig', errors='replace')
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
    if name.endswith('.xlsx'):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb['Prospects'] if 'Prospects' in wb.sheetnames else wb.active
        it = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else '' for h in next(it, [])]
        rows = []
        for values in it:
            if not any(v not in (None, '') for v in values):
                continue
            rows.append({h: v for h, v in zip(headers, values) if h})
        return rows
    raise ValueError('Upload the outreach spreadsheet (.xlsx) or a .csv file.')


def _cell(row, *names):
    for want in names:
        for key, value in row.items():
            if key and key.strip().lower().startswith(want):
                return '' if value is None else str(value).strip()
    return ''


def import_rows(db, rows):
    """Add or update prospects. Returns (added, updated, skipped)."""
    cats = {}
    for c in db.execute('SELECT id, slug, name FROM categories'):
        cats[c['slug'].lower()] = c['id']
        cats[c['name'].lower()] = c['id']
    areas = db.execute('SELECT id, slug, name FROM areas').fetchall()
    now = ts(utcnow())
    added = updated = skipped = 0
    for row in rows:
        business = _cell(row, 'business')
        cat_id = cats.get(_cell(row, 'trade').lower())
        if not business or not cat_id:
            skipped += 1
            continue
        found = EMAIL_RE.search(_cell(row, 'email'))
        email = found.group(0).lower() if found else None
        area_ids = [a['id'] for a in areas if str(row.get(a['name']) or '').strip().upper() == 'Y']
        listed = _cell(row, 'areas')
        if listed:
            wanted = {s.strip().lower() for s in listed.split(',')}
            area_ids += [a['id'] for a in areas if a['slug'] in wanted and a['id'] not in area_ids]
        dnc = _cell(row, 'do not contact').upper() in ('Y', 'YES', 'TRUE', '1')
        status = _FROM_SHEET.get(_cell(row, 'status').lower(), 'new')
        fields = {
            'category_id': cat_id, 'business_name': business[:200],
            'first_name': _cell(row, 'first name')[:60] or None, 'email': email,
            'phone': _cell(row, 'phone')[:40] or None, 'website': _cell(row, 'website')[:300] or None,
            'based_in': _cell(row, 'based in')[:80] or None, 'source_url': _cell(row, 'source')[:500] or None,
            'notes': _cell(row, 'what they do', 'notes')[:500] or None,
        }
        existing = None
        if email:
            existing = db.execute('SELECT * FROM prospects WHERE email = ?', (email,)).fetchone()
        if not existing:
            existing = db.execute('SELECT * FROM prospects WHERE category_id = ? AND LOWER(business_name) = ?',
                                  (cat_id, business.lower())).fetchone()
        if existing:
            pid = existing['id']
            cols = ', '.join(f'{k} = ?' for k in fields)
            db.execute(f'UPDATE prospects SET {cols}, updated_at = ? WHERE id = ?',
                       (*fields.values(), now, pid))
            # Opt-outs only ever tighten: a stale sheet can't switch one back on.
            if dnc and not existing['do_not_contact']:
                db.execute('UPDATE prospects SET do_not_contact = 1 WHERE id = ?', (pid,))
            if status in STOPPED and existing['status'] not in STOPPED:
                db.execute('UPDATE prospects SET status = ? WHERE id = ?', (status, pid))
            updated += 1
        else:
            token = secrets.token_urlsafe(12)
            db.execute(
                'INSERT INTO prospects (category_id, business_name, first_name, email, phone, website, based_in, '
                'source_url, notes, do_not_contact, status, token, created_at, updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (*fields.values(), 1 if dnc else 0, status, token, now, now))
            pid = db.execute('SELECT id FROM prospects WHERE token = ?', (token,)).fetchone()['id']
            added += 1
        if area_ids:
            db.execute('DELETE FROM prospect_areas WHERE prospect_id = ?', (pid,))
            for aid in set(area_ids):
                db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (pid, aid))
    db.commit()
    return added, updated, skipped


# ── Matching and sending ──────────────────────────────────────────────────────

def matches(db, job):
    """Businesses we could send this job to, least-contacted first."""
    return db.execute(
        "SELECT p.* FROM prospects p "
        "JOIN prospect_areas pa ON pa.prospect_id = p.id AND pa.area_id = ? "
        "WHERE p.category_id = ? AND p.email IS NOT NULL AND p.email <> '' "
        "AND p.do_not_contact = 0 AND p.status IN ('new','sent','replied') AND p.leads_sent < ? "
        "AND NOT EXISTS (SELECT 1 FROM prospect_sends s WHERE s.prospect_id = p.id AND s.job_id = ?) "
        "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.email = p.email) "
        "ORDER BY p.leads_sent, p.last_sent_at, p.business_name",
        (job['area_id'], job['category_id'], MAX_LEADS, job['id'])).fetchall()


def default_summary(description, limit=180):
    """First sentence or so of the job, for the email. The admin edits it before sending."""
    text = re.sub(r'\s+', ' ', description or '').strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    return cut[:stop + 1] if stop > 60 else cut.rsplit(' ', 1)[0] + '…'


def links(prospect, job_id=None):
    site = integrations.site_url()
    join = f'{site}/o/{prospect["token"]}' + (f'?j={job_id}' if job_id else '')
    return join, f'{site}/o/{prospect["token"]}/stop'


def compose(prospect, job, summary, sender_name):
    """Subject and body for one business. `job` needs category_name and area_name."""
    join, stop = links(prospect, job['id'])
    first = (prospect['first_name'] or '').strip()
    size = config.VALUE_BANDS.get(job['value_band'], {}).get('label', '')
    place = job['suburb'] or job['area_name']
    site = re.sub(r'^https?://(www\.)?', '', (prospect['website'] or '').rstrip('/'))
    subject = f'Free {job["category_name"].lower()} lead in {place}: {job["title"]}'
    body = (
        f'Hi {first or "there"},\n\n'
        f'A homeowner in {place} has just posted a job on {config.BRAND} that looks like your kind of work:\n\n'
        f'{job["title"]}\n{summary}\nSize: {size}\n\n'
        f'{config.BRAND} is new in Wellington and we’re running a free pilot — no card, no tokens, no lead fees. '
        f'Each job goes to up to {config.TRADES_PER_JOB} local trades and closes at {config.MAX_QUOTES} quotes, '
        f'first in first served.\n\n'
        f'Sign up free (takes 2 minutes) and if there’s still a spot on this job it comes straight to you. '
        f'New trades go to the front of the queue for the next one too:\n{join}\n\n'
        f'I got your email from {site or "your website"}. If you’d rather not hear from us, use the link below '
        f'or just reply “unsubscribe”, and we won’t email you again.\n\n'
        f'Cheers,\n{sender_name}\n{config.BRAND} — {integrations.site_url()}\nWellington, New Zealand'
    )
    return subject, body, stop


def reply_address(sender):
    """Where replies (including "unsubscribe") land: the mailbox we send from, which someone reads."""
    return integrations.get('smtp_user') or sender['email']


def queue(db, job, prospect_ids, summary, sender):
    """Queue one email per chosen business. Only businesses that still match go."""
    allowed = {p['id'] for p in matches(db, job)}
    now = ts(utcnow())
    sender_name = (sender['name'] or 'The team').split(' ')[0]
    n = 0
    for pid in prospect_ids:
        if pid not in allowed:
            continue
        db.execute('INSERT INTO prospect_sends (prospect_id, job_id, summary, sender_id, sender_name, reply_to, '
                   "status, created_at) VALUES (?,?,?,?,?,?,'queued',?)",
                   (pid, job['id'], summary, sender['id'], sender_name, reply_address(sender), now))
        n += 1
    db.commit()
    return n


def shortfall(db, job):
    """How many of this job's slots we couldn't fill from trades already on Level."""
    from engine import live_slots
    return max(0, config.TRADES_PER_JOB - live_slots(db, job['id']))


def maybe_topup(db, job, at=None):
    """Not enough local trades for this job? Ask the ones who aren't on Level yet.

    This is the honest version of a cold email: there is a real job, in their
    trade, in their area, right now, and the slot is genuinely empty. Every rule
    that governs a hand-sent lead governs this one too — the three-email cap, the
    opt-out list, one email per business per job — because it goes through the
    same `matches()` and `queue()`.

    Once per job. A job that stays short doesn't get a second round: if the first
    email didn't move anyone, a reminder is just spam.
    """
    at = at or utcnow()
    if not AUTO_TOPUP or job['status'] != 'open' or job['topup_at']:
        return 0
    gap = shortfall(db, job)
    if gap < TOPUP_MIN_GAP:
        return 0
    sender = db.execute("SELECT id, name, email FROM users WHERE role = 'admin' AND closed_at IS NULL "
                        'ORDER BY id').fetchone()
    if not sender:
        return 0
    # More than the gap, because most people who get an email don't sign up today.
    want = min(gap * TOPUP_PER_SLOT, TOPUP_MAX)
    picks = [p['id'] for p in matches(db, job)[:want]]
    now = ts(at)
    db.execute('UPDATE jobs SET topup_at = ?, topup_sent = ? WHERE id = ?', (now, len(picks), job['id']))
    db.commit()
    if not picks:
        return 0
    return queue(db, job, picks, default_summary(job['description']), sender)


def flush(db, limit=20):
    """Send queued outreach emails. Runs with the other background jobs, and
    straight after an admin presses Send. The lock stops both sending the same email."""
    if not mailer.enabled() or not try_lock(db, 'outreach', 300):
        return 0
    try:
        return _flush(db, limit)
    finally:
        db.execute("UPDATE locks SET expires_at = ? WHERE name = 'outreach'", (ts(utcnow()),))
        db.commit()


def _flush(db, limit):
    rows = db.execute(
        "SELECT s.id AS send_id, s.summary, s.sender_name, s.reply_to, s.created_at AS queued_at, "
        "p.id AS prospect_id, p.token, p.first_name, p.business_name, p.email, p.website, p.status AS p_status, "
        "p.do_not_contact, j.id, j.title, j.suburb, j.value_band, j.status AS job_status, "
        "c.name AS category_name, a.name AS area_name "
        "FROM prospect_sends s JOIN prospects p ON p.id = s.prospect_id JOIN jobs j ON j.id = s.job_id "
        "JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id "
        "WHERE s.status = 'queued' ORDER BY s.id LIMIT ?", (limit,)).fetchall()
    now = utcnow()
    sent = 0
    for r in rows:
        stale = parse_ts(r['queued_at']) < now - timedelta(hours=SEND_WINDOW_HOURS)
        if (stale or r['job_status'] not in ('open', 'full') or r['do_not_contact']
                or r['p_status'] in STOPPED or r['p_status'] == 'signed_up'):
            db.execute("UPDATE prospect_sends SET status = 'skipped' WHERE id = ?", (r['send_id'],))
            continue
        subject, body, stop = compose(r, r, r['summary'], r['sender_name'])
        reply_to = r['reply_to'] or integrations.get('smtp_user') or None
        ok = mailer.send(r['email'], None, subject, body, unsubscribe_url=stop, reply_to=reply_to)
        stamp = ts(utcnow())
        if ok:
            sent += 1
            db.execute("UPDATE prospect_sends SET status = 'sent', sent_at = ? WHERE id = ?", (stamp, r['send_id']))
            db.execute("UPDATE prospects SET leads_sent = leads_sent + 1, last_sent_at = ?, "
                       "status = CASE WHEN status = 'new' THEN 'sent' ELSE status END WHERE id = ?",
                       (stamp, r['prospect_id']))
        else:
            db.execute("UPDATE prospect_sends SET status = 'failed' WHERE id = ?", (r['send_id'],))
        db.commit()
    db.commit()
    return sent


# ── What the business does with the email ─────────────────────────────────────

def by_token(db, token):
    return db.execute('SELECT * FROM prospects WHERE token = ?', (token or '',)).fetchone()


def clicked(db, prospect, job_id=None):
    sql = 'UPDATE prospect_sends SET clicked_at = ? WHERE prospect_id = ? AND clicked_at IS NULL'
    args = [ts(utcnow()), prospect['id']]
    if job_id:
        sql += ' AND job_id = ?'
        args.append(job_id)
    db.execute(sql, args)
    if prospect['status'] == 'sent':
        db.execute("UPDATE prospects SET status = 'replied' WHERE id = ?", (prospect['id'],))
    db.commit()


def unsubscribe(db, prospect):
    db.execute("UPDATE prospects SET status = 'unsubscribed', unsubscribed_at = ?, updated_at = ? WHERE id = ?",
               (ts(utcnow()), ts(utcnow()), prospect['id']))
    db.execute("UPDATE prospect_sends SET status = 'skipped' WHERE prospect_id = ? AND status = 'queued'",
               (prospect['id'],))
    db.commit()


def signed_up(db, prospect, user_id):
    db.execute("UPDATE prospects SET status = 'signed_up', user_id = ?, updated_at = ? WHERE id = ?",
               (user_id, ts(utcnow()), prospect['id']))
    db.commit()


def setup_hint(db, prospect):
    """The trade and areas we listed them under, to pre-tick on the setup page."""
    areas = [r['area_id'] for r in db.execute('SELECT area_id FROM prospect_areas WHERE prospect_id = ?',
                                              (prospect['id'],))]
    return {'categories': [prospect['category_id']], 'areas': areas}


# ── Numbers for the admin page ────────────────────────────────────────────────

def stats(db):
    counts = {k: 0 for k in STATUSES}
    for r in db.execute('SELECT status, COUNT(*) AS n FROM prospects GROUP BY status'):
        counts[r['status']] = r['n']
    total = sum(counts.values())
    emailable = db.execute(
        "SELECT COUNT(*) AS n FROM prospects WHERE email IS NOT NULL AND email <> '' AND do_not_contact = 0 "
        "AND status IN ('new','sent','replied') AND leads_sent < ?", (MAX_LEADS,)).fetchone()['n']
    sent = db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE status = 'sent'").fetchone()['n']
    queued = db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE status = 'queued'").fetchone()['n']
    clicks = db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE clicked_at IS NOT NULL").fetchone()['n']
    contacted = total - counts['new']
    return {'counts': counts, 'total': total, 'emailable': emailable, 'sent': sent, 'queued': queued,
            'clicks': clicks, 'contacted': contacted,
            'rate': (counts['signed_up'] / contacted) if contacted else None}
