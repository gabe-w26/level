"""
Job distribution engine — the rules the product promises.

  • Each open job keeps TRADES_PER_JOB live slots: trades holding an open offer
    plus trades who have quoted. When a slot frees up — an offer runs past
    OFFER_WINDOW_HOURS without a quote, or a trade passes on it — it goes to a
    new trade. So if 11 of 15 don't quote within 24 hours, 11 new trades see it.
  • A customer receives at most MAX_QUOTES quotes, first in, first served. The
    cap is enforced by one conditional UPDATE, so two trades pressing send at
    the same moment can't both become quote number six.
  • Who gets offered a job is fair rotation: trades offered the fewest jobs of
    that category and value band recently go first, then whoever has waited
    longest, then random. Reviews and ratings are never read here.
  • Tiers stack: a job is visible to its value band's tier and every tier above.

All timestamps are UTC strings computed in Python (see schema.py).
"""
import random
from datetime import datetime, timedelta

import config

TS = '%Y-%m-%d %H:%M:%S'

# Demo tools can move the clock forward so a 24-hour handover can be watched
# without waiting a day. Always zero in production.
_clock = {'offset_hours': 0.0}


def set_clock_offset(hours):
    _clock['offset_hours'] = float(hours or 0)


def utcnow():
    return (datetime.utcnow() + timedelta(hours=_clock['offset_hours'])).replace(microsecond=0)


def ts(dt):
    return dt.strftime(TS)


def parse_ts(value):
    if not value:
        return None
    return datetime.strptime(str(value)[:19], TS)


class RuleError(Exception):
    """A business rule refused the action. The message is shown to the user."""


# ── Tiers ─────────────────────────────────────────────────────────────────────

def tiers_that_see(value_band):
    rank = config.VALUE_BANDS[value_band]['rank']
    return [key for key in config.TIER_ORDER if config.TIERS[key]['rank'] >= rank]


def can_see(tier, value_band):
    return bool(tier) and config.TIERS[tier]['rank'] >= config.VALUE_BANDS[value_band]['rank']


def is_subscribed(trade, at=None):
    at = at or utcnow()
    return (trade is not None and trade['sub_status'] == 'active'
            and trade['period_end'] is not None and parse_ts(trade['period_end']) > at)


# ── Helpers ───────────────────────────────────────────────────────────────────

def notify(db, user_id, body, link=None, at=None, sms=False):
    """In-app notice, emailed by the sweep; `sms=True` also texts it (new jobs)."""
    db.execute('INSERT INTO notifications (user_id, body, link, created_at, sms) VALUES (?,?,?,?,?)',
               (user_id, body, link, ts(at or utcnow()), 1 if sms else 0))


def get_job(db, job_id):
    return db.execute(
        'SELECT j.*, c.name AS category_name, c.licence_note, a.name AS area_name, a.region '
        'FROM jobs j JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
        'WHERE j.id = ?', (job_id,)).fetchone()


def quote_top_incl_gst(q):
    """Highest price in a quote, GST-inclusive, or None for site-visit quotes."""
    top = q.get('amount_high') or q.get('amount_low')
    if not top:
        return None
    return top if q.get('gst_included') else round(top * 1.15)


def needs_act_docs(job, q):
    """Building Act: residential work at or over $30k incl. GST needs a written
    contract, the disclosure statement and the prescribed checklist."""
    top = quote_top_incl_gst(q)
    return bool(top and top >= config.CONTRACT_THRESHOLD and job['property_type'] != 'commercial')


# ── Distribution ──────────────────────────────────────────────────────────────

def _candidate_rows(db, job, at):
    """Eligible trades in fair-rotation order, with the numbers that set the order."""
    tiers = tiers_that_see(job['value_band'])
    since = ts(at - timedelta(days=config.ROTATION_LOOKBACK_DAYS))
    marks = ','.join('?' * len(tiers))
    rows = db.execute(f'''
        SELECT t.user_id, t.business_name, t.tier, t.last_offered_at,
               (SELECT COUNT(*) FROM offers o JOIN jobs j2 ON j2.id = o.job_id
                 WHERE o.trade_id = t.user_id AND o.offered_at >= ?
                   AND j2.category_id = ? AND j2.value_band = ?) AS recent
          FROM trades t
          JOIN trade_categories tc ON tc.trade_id = t.user_id AND tc.category_id = ?
          JOIN trade_areas ta ON ta.trade_id = t.user_id AND ta.area_id = ?
         WHERE t.sub_status = 'active' AND t.period_end > ?
           AND t.tier IN ({marks})
           AND t.paused = 0
           AND t.user_id <> ?
           AND NOT EXISTS (SELECT 1 FROM offers o2 WHERE o2.job_id = ? AND o2.trade_id = t.user_id)
    ''', (since, job['category_id'], job['value_band'], job['category_id'], job['area_id'],
          ts(at), *tiers, job['customer_id'], job['id'])).fetchall()
    rng = random.Random()
    return sorted(rows, key=lambda r: (r['recent'], r['last_offered_at'] or '', rng.random()))


def _candidates(db, job, at):
    return [r['user_id'] for r in _candidate_rows(db, job, at)]


def live_slots(db, job_id):
    return db.execute("SELECT COUNT(*) AS n FROM offers WHERE job_id = ? AND status IN ('active','quoted')",
                      (job_id,)).fetchone()['n']


def fill_slots(db, job, at=None):
    """Top a job back up to TRADES_PER_JOB live slots. Returns how many trades were offered it."""
    at = at or utcnow()
    if job['status'] != 'open' or job['quote_count'] >= config.MAX_QUOTES:
        return 0
    if parse_ts(job['closes_at']) <= at:
        return 0
    need = config.TRADES_PER_JOB - live_slots(db, job['id'])
    if need <= 0:
        return 0
    picks = _candidates(db, job, at)[:need]
    if not picks:
        return 0
    wave = db.execute('SELECT COALESCE(MAX(wave), 0) AS w FROM offers WHERE job_id = ?',
                      (job['id'],)).fetchone()['w'] + 1
    now_s, expires = ts(at), ts(at + timedelta(hours=config.OFFER_WINDOW_HOURS))
    band = config.VALUE_BANDS[job['value_band']]['short']
    area = job['area_name'] if 'area_name' in job.keys() else ''
    for trade_id in picks:
        # OR IGNORE: if another worker offered this trade the job a moment ago,
        # skip quietly rather than abort the transaction.
        db.execute('INSERT OR IGNORE INTO offers (job_id, trade_id, wave, status, offered_at, expires_at) '
                   "VALUES (?,?,?,'active',?,?)", (job['id'], trade_id, wave, now_s, expires))
        db.execute('UPDATE trades SET last_offered_at = ? WHERE user_id = ?', (now_s, trade_id))
        notify(db, trade_id,
               f'New {band} job in {area}: “{job["title"]}”. You have {config.OFFER_WINDOW_HOURS} hours to quote.',
               f'/trade/jobs/{job["id"]}', at, sms=True)
    return len(picks)


def post_job(db, customer_id, f, at=None, hold=False):
    """Create a job and offer it out. With `hold`, it waits (status 'held') until
    the customer confirms their phone number — see release_held()."""
    at = at or utcnow()
    cur = db.execute(
        'INSERT INTO jobs (customer_id, category_id, area_id, suburb, address, title, description, '
        'value_band, timing, property_type, status, quote_count, created_at, closes_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?)',
        (customer_id, f['category_id'], f['area_id'], f.get('suburb'), f.get('address'),
         f['title'], f['description'], f['value_band'], f.get('timing'), f.get('property_type'),
         'held' if hold else 'open', ts(at), ts(at + timedelta(days=config.JOB_OPEN_DAYS))))
    job_id = cur.lastrowid
    offered = 0 if hold else fill_slots(db, get_job(db, job_id), at)
    db.commit()
    return job_id, offered


def release_held(db, customer_id, at=None):
    """Send out a customer's held jobs once their phone is confirmed. The 14-day
    clock starts now, not when they first typed the job in."""
    at = at or utcnow()
    offered = 0
    for row in db.execute("SELECT id FROM jobs WHERE customer_id = ? AND status = 'held'", (customer_id,)).fetchall():
        db.execute("UPDATE jobs SET status = 'open', created_at = ?, closes_at = ? WHERE id = ?",
                   (ts(at), ts(at + timedelta(days=config.JOB_OPEN_DAYS)), row['id']))
        offered += fill_slots(db, get_job(db, row['id']), at)
    db.commit()
    return offered


# ── Trade actions ─────────────────────────────────────────────────────────────

def get_offer(db, job_id, trade_id):
    return db.execute('SELECT * FROM offers WHERE job_id = ? AND trade_id = ?', (job_id, trade_id)).fetchone()


def mark_seen(db, job_id, trade_id, at=None):
    db.execute('UPDATE offers SET seen_at = ? WHERE job_id = ? AND trade_id = ? AND seen_at IS NULL',
               (ts(at or utcnow()), job_id, trade_id))
    db.commit()


def _clean_quote(job, q):
    kind = q.get('price_type')
    if kind not in ('fixed', 'range', 'site_visit'):
        raise RuleError('Choose how you want to price this job.')
    low, high = q.get('amount_low'), q.get('amount_high')
    if kind == 'fixed':
        if not low or low <= 0:
            raise RuleError('Enter your fixed price.')
        high = low
    elif kind == 'range':
        if not low or not high or low <= 0 or high < low:
            raise RuleError('Enter a price range where the top is at least the bottom.')
    else:
        low = high = None
    message = (q.get('message') or '').strip()
    if len(message) < 20:
        raise RuleError('Tell the customer how you’d do the job — at least a sentence or two.')
    out = dict(q, price_type=kind, amount_low=low, amount_high=high, message=message)
    if needs_act_docs(job, out) and not q.get('act_docs_promised'):
        raise RuleError('This quote is $30,000 or more for residential work, so confirm you’ll provide '
                        'a written contract, disclosure statement and the MBIE checklist.')
    return out


def submit_quote(db, job_id, trade_id, q, at=None):
    at = at or utcnow()
    job = get_job(db, job_id)
    offer = get_offer(db, job_id, trade_id)
    if not job or not offer:
        raise RuleError('This job wasn’t offered to you.')
    if offer['status'] == 'quoted':
        raise RuleError('You’ve already quoted on this job.')
    if offer['status'] == 'closed' or job['status'] == 'full':
        raise RuleError(f'This job already has its {config.MAX_QUOTES} quotes.')
    if offer['status'] != 'active' or parse_ts(offer['expires_at']) <= at:
        raise RuleError(f'Your {config.OFFER_WINDOW_HOURS} hours to quote have passed, so this job went to another trade.')
    plan = _report_plan(q)
    q = _clean_quote(job, q)

    # The cap: only succeeds while the job is open and under MAX_QUOTES.
    cur = db.execute("UPDATE jobs SET quote_count = quote_count + 1 "
                     "WHERE id = ? AND status = 'open' AND quote_count < ?", (job_id, config.MAX_QUOTES))
    if cur.rowcount != 1:
        db.rollback()
        raise RuleError(f'Someone got there first — this job already has its {config.MAX_QUOTES} quotes.')

    now_s = ts(at)
    db.execute('INSERT INTO quotes (job_id, trade_id, price_type, amount_low, amount_high, gst_included, '
               'message, inclusions, exclusions, warranty, available_from, duration, act_docs_promised, '
               "report_plan, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'sent',?)",
               (job_id, trade_id, q['price_type'], q['amount_low'], q['amount_high'],
                1 if q.get('gst_included') else 0, q['message'], q.get('inclusions'), q.get('exclusions'),
                q.get('warranty'), q.get('available_from'), q.get('duration'),
                1 if q.get('act_docs_promised') else 0, plan, now_s))
    db.execute("UPDATE offers SET status = 'quoted', resolved_at = ? WHERE id = ?", (now_s, offer['id']))

    count = db.execute('SELECT quote_count FROM jobs WHERE id = ?', (job_id,)).fetchone()['quote_count']
    link = f'/me/jobs/{job_id}'
    if count >= config.MAX_QUOTES:
        db.execute("UPDATE jobs SET status = 'full' WHERE id = ?", (job_id,))
        waiting = db.execute("SELECT trade_id FROM offers WHERE job_id = ? AND status = 'active'", (job_id,)).fetchall()
        db.execute("UPDATE offers SET status = 'closed', resolved_at = ? WHERE job_id = ? AND status = 'active'",
                   (now_s, job_id))
        for w in waiting:
            notify(db, w['trade_id'], f'“{job["title"]}” filled up with {config.MAX_QUOTES} quotes before you quoted.',
                   f'/trade/jobs/{job_id}', at)
        notify(db, job['customer_id'], f'You have all {config.MAX_QUOTES} quotes for “{job["title"]}”. Compare them side by side.', link, at)
    else:
        notify(db, job['customer_id'], f'New quote {count} of {config.MAX_QUOTES} for “{job["title"]}”.', link, at)
    db.commit()
    import referrals
    referrals.earn_for_first_quote(db, trade_id, at)
    return count


def _report_plan(q):
    """The progress updates promised on a quote: None if the field wasn't sent, else '' or e.g. 'daily,weekly'."""
    if 'report_plan' not in q:
        return None
    import reporting
    value = q['report_plan']
    return reporting.clean(value if isinstance(value, (list, tuple)) else str(value or '').split(','))


def revise_quote(db, job, trade_id, q, at=None):
    """A trade tidies up their quote before the customer has responded. The
    arrival time doesn't change, so it keeps its place in the first six."""
    at = at or utcnow()
    row = db.execute('SELECT * FROM quotes WHERE job_id = ? AND trade_id = ?', (job['id'], trade_id)).fetchone()
    if not row:
        raise RuleError('You haven’t quoted on this job.')
    if row['status'] != 'sent':
        raise RuleError('The customer has already responded to this quote, so it can’t be changed.')
    plan = _report_plan(q)
    q = _clean_quote(job, q)
    if plan is not None:
        db.execute('UPDATE quotes SET report_plan = ? WHERE id = ?', (plan, row['id']))
    db.execute('UPDATE quotes SET price_type = ?, amount_low = ?, amount_high = ?, gst_included = ?, message = ?, '
               'inclusions = ?, exclusions = ?, warranty = ?, available_from = ?, duration = ?, '
               'act_docs_promised = ? WHERE id = ?',
               (q['price_type'], q['amount_low'], q['amount_high'], 1 if q.get('gst_included') else 0, q['message'],
                q.get('inclusions'), q.get('exclusions'), q.get('warranty'), q.get('available_from'),
                q.get('duration'), 1 if q.get('act_docs_promised') else 0, row['id']))
    who = db.execute('SELECT business_name FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    notify(db, job['customer_id'], f'{who["business_name"]} updated their quote for “{job["title"]}”.',
           f'/me/jobs/{job["id"]}', at)
    db.commit()


def decline_offer(db, job_id, trade_id, at=None):
    """A trade passes on a job. Their slot goes to another trade straight away."""
    at = at or utcnow()
    offer = get_offer(db, job_id, trade_id)
    if not offer or offer['status'] != 'active':
        raise RuleError('This job isn’t waiting on you.')
    db.execute("UPDATE offers SET status = 'declined', resolved_at = ? WHERE id = ?", (ts(at), offer['id']))
    fill_slots(db, get_job(db, job_id), at)
    db.commit()


def report_job(db, job_id, trade_id, reason, note, at=None):
    db.execute('INSERT OR IGNORE INTO job_reports (job_id, trade_id, reason, note, status, created_at) '
               "VALUES (?,?,?,?,'open',?)", (job_id, trade_id, reason, note, ts(at or utcnow())))
    db.commit()


def set_pause(db, trade_id, paused, until=None, at=None):
    at = at or utcnow()
    now_s = ts(at)
    if paused:
        db.execute('UPDATE trades SET paused = 1, paused_until = ? WHERE user_id = ?', (until, trade_id))
        # Pausing forfeits the lowered quote requirement for this month's guarantee.
        db.execute('UPDATE payments SET paused_during = 1 WHERE trade_id = ? AND period_start <= ? AND period_end > ?',
                   (trade_id, now_s, now_s))
    else:
        db.execute('UPDATE trades SET paused = 0, paused_until = NULL, streak_reset_at = ? WHERE user_id = ?',
                   (now_s, trade_id))


def _maybe_auto_pause(db, trade_id, at):
    """Pause leads for a trade who let AUTO_PAUSE_AFTER offers in a row expire,
    so customers aren't left waiting on slots nobody is using."""
    trade = db.execute('SELECT paused, streak_reset_at FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    if not trade or trade['paused']:
        return False
    rows = db.execute("SELECT status FROM offers WHERE trade_id = ? AND offered_at >= ? "
                      "AND status IN ('expired','quoted','declined') ORDER BY offered_at DESC LIMIT ?",
                      (trade_id, trade['streak_reset_at'] or '0000', config.AUTO_PAUSE_AFTER)).fetchall()
    if len(rows) == config.AUTO_PAUSE_AFTER and all(r['status'] == 'expired' for r in rows):
        set_pause(db, trade_id, True, None, at)
        notify(db, trade_id, f'We’ve paused new jobs because your last {config.AUTO_PAUSE_AFTER} expired without a quote. '
                             'Turn jobs back on whenever you’re ready.', '/trade#availability', at)
        return True
    return False


# ── Customer actions ──────────────────────────────────────────────────────────

def _quote_for(db, job, quote_id):
    q = db.execute('SELECT * FROM quotes WHERE id = ? AND job_id = ?', (quote_id, job['id'])).fetchone()
    if not q:
        raise RuleError('That quote isn’t on this job.')
    return q


def share_contact(db, job, quote_id, at=None):
    at = at or utcnow()
    q = _quote_for(db, job, quote_id)
    if q['status'] == 'sent':
        db.execute("UPDATE quotes SET status = 'shortlisted', responded_at = ? WHERE id = ?", (ts(at), q['id']))
        notify(db, q['trade_id'], f'The customer shared their contact details for “{job["title"]}”. Get in touch.',
               f'/trade/jobs/{job["id"]}', at)
    db.commit()


def decline_quote(db, job, quote_id, at=None):
    at = at or utcnow()
    q = _quote_for(db, job, quote_id)
    if q['status'] in ('sent', 'shortlisted'):
        db.execute("UPDATE quotes SET status = 'declined', responded_at = ? WHERE id = ?", (ts(at), q['id']))
        notify(db, q['trade_id'], f'The customer passed on your quote for “{job["title"]}”.', f'/trade/jobs/{job["id"]}', at)
    db.commit()


def _close_offers_and_quotes(db, job, keep_quote_id, at):
    now_s = ts(at)
    db.execute("UPDATE offers SET status = 'closed', resolved_at = ? WHERE job_id = ? AND status = 'active'",
               (now_s, job['id']))
    losers = db.execute("SELECT id, trade_id FROM quotes WHERE job_id = ? AND status IN ('sent','shortlisted') AND id <> ?",
                        (job['id'], keep_quote_id or 0)).fetchall()
    for q in losers:
        db.execute("UPDATE quotes SET status = 'declined', responded_at = ? WHERE id = ?", (now_s, q['id']))
        notify(db, q['trade_id'], f'“{job["title"]}” has closed — the customer went another way this time.',
               f'/trade/jobs/{job["id"]}', at)


def accept_quote(db, job, quote_id, act_ack=False, at=None):
    at = at or utcnow()
    if job['status'] not in ('open', 'full', 'expired'):
        raise RuleError('This job is already closed.')
    q = _quote_for(db, job, quote_id)
    if q['status'] not in ('sent', 'shortlisted'):
        raise RuleError('That quote can’t be accepted any more.')
    if needs_act_docs(job, dict(q)) and not act_ack:
        raise RuleError('Tick the box to confirm you’ll get a written contract, disclosure statement and '
                        'checklist before work starts — the law requires them for jobs this size.')
    now_s = ts(at)
    db.execute("UPDATE quotes SET status = 'accepted', responded_at = ?, act_docs_ack_at = ? WHERE id = ?",
               (now_s, now_s if act_ack else None, q['id']))
    db.execute("UPDATE jobs SET status = 'hired', hired_trade_id = ?, closed_at = ?, close_reason = 'hired' WHERE id = ?",
               (q['trade_id'], now_s, job['id']))
    _close_offers_and_quotes(db, job, q['id'], at)
    import reporting
    reporting.start_job(db, job, q, at)
    notify(db, q['trade_id'], f'You won “{job["title"]}”. The customer’s contact details are on the job.',
           f'/trade/jobs/{job["id"]}', at)
    db.commit()


def close_job(db, job, outcome, at=None):
    """Customer closes a job. `outcome` is a quote id (hired that trade), 'elsewhere' or 'not_going_ahead'."""
    at = at or utcnow()
    if job['status'] not in ('open', 'full', 'expired'):
        raise RuleError('This job is already closed.')
    if str(outcome).isdigit():
        return accept_quote(db, job, int(outcome), act_ack=True, at=at)
    reason = 'hired_elsewhere' if outcome == 'elsewhere' else 'not_going_ahead'
    db.execute("UPDATE jobs SET status = 'closed', closed_at = ?, close_reason = ? WHERE id = ?",
               (ts(at), reason, job['id']))
    _close_offers_and_quotes(db, job, None, at)
    db.commit()


def flag_quote(db, job, quote_id, reason):
    q = _quote_for(db, job, quote_id)
    db.execute('UPDATE quotes SET flagged = 1, flag_reason = ? WHERE id = ?', (reason, q['id']))
    db.commit()


# ── Guarantee ─────────────────────────────────────────────────────────────────

def period_stats(db, trade_id, start, end):
    one = lambda sql: db.execute(sql, (trade_id, start, end)).fetchone()['n']
    return {
        'offers': one('SELECT COUNT(*) AS n FROM offers WHERE trade_id = ? AND offered_at >= ? AND offered_at < ?'),
        'quotes': one('SELECT COUNT(*) AS n FROM quotes WHERE trade_id = ? AND flagged = 0 '
                      'AND created_at >= ? AND created_at < ?'),
        'won':    one("SELECT COUNT(*) AS n FROM quotes WHERE trade_id = ? AND status = 'accepted' "
                      'AND responded_at >= ? AND responded_at < ?'),
    }


def required_quotes(offers_received, paused_during):
    if paused_during:
        return config.GUARANTEE_MIN_QUOTES
    return min(config.GUARANTEE_MIN_QUOTES, offers_received)


def current_payment(db, trade_id, at=None):
    now_s = ts(at or utcnow())
    return db.execute('SELECT * FROM payments WHERE trade_id = ? AND period_start <= ? AND period_end > ? '
                      'ORDER BY id DESC LIMIT 1', (trade_id, now_s, now_s)).fetchone()


def guarantee_progress(db, trade_id, at=None):
    at = at or utcnow()
    p = current_payment(db, trade_id, at)
    if not p:
        return None
    s = period_stats(db, trade_id, p['period_start'], p['period_end'])
    # During the month the target is the full five; it only drops at month end
    # if fewer jobs than that were offered.
    target = config.GUARANTEE_MIN_QUOTES
    return dict(s, payment=p, target=target, paused_during=bool(p['paused_during']),
                qualified=s['won'] == 0 and s['quotes'] >= target)


def evaluate_guarantees(db, at=None):
    """Settle the guarantee for every billing month that has ended."""
    at = at or utcnow()
    now_s = ts(at)
    due = db.execute("SELECT * FROM payments WHERE claim_checked = 0 AND period_end <= ? "
                     "AND status IN ('paid','demo')", (now_s,)).fetchall()
    approved = []
    for p in due:
        s = period_stats(db, p['trade_id'], p['period_start'], p['period_end'])
        need = required_quotes(s['offers'], p['paused_during'])
        eligible = s['won'] == 0 and s['quotes'] >= need
        status = 'not_eligible'
        if eligible:
            status = 'approved' if config.GUARANTEE_AUTO_APPROVE else 'pending'
        cur = db.execute(
            'INSERT OR IGNORE INTO guarantee_claims (trade_id, payment_id, period_start, period_end, amount_cents, '
            'offers_received, quotes_sent, jobs_won, quotes_required, status, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (p['trade_id'], p['id'], p['period_start'], p['period_end'], p['amount_cents'],
             s['offers'], s['quotes'], s['won'], need, status, now_s))
        db.execute('UPDATE payments SET claim_checked = 1 WHERE id = ?', (p['id'],))
        if eligible:
            notify(db, p['trade_id'], f'No job won last month, so your ${p["amount_cents"] / 100:.2f} is being refunded.',
                   '/trade/plan', at)
            if status == 'approved' and cur.rowcount == 1:
                approved.append(cur.lastrowid)
    db.commit()
    if approved:
        import billing
        for claim_id in approved:
            billing.refund_claim(db, claim_id, at)
    return len(due)


# ── Sweep ─────────────────────────────────────────────────────────────────────

def _renewal_reminders(db, at):
    soon = ts(at + timedelta(days=7))
    rows = db.execute(
        "SELECT p.id, p.trade_id, p.period_end, t.tier FROM payments p JOIN trades t ON t.user_id = p.trade_id "
        "WHERE p.reminder_sent = 0 AND p.period_end > ? AND p.period_end <= ? "
        "AND t.sub_status = 'active' AND t.cancel_at_period_end = 0", (ts(at), soon)).fetchall()
    for r in rows:
        tier = config.TIERS.get(r['tier']) or {}
        notify(db, r['trade_id'], f'Your {tier.get("name", "")} plan renews on {r["period_end"][:10]} for '
                                  f'${tier.get("price", 0)} {config.PRICE_GST_NOTE}. Change or cancel any time before then.',
               '/trade/plan', at)
        db.execute('UPDATE payments SET reminder_sent = 1 WHERE id = ?', (r['id'],))


def sweep(db, at=None):
    """Run every time-based rule. Safe to run as often as you like."""
    at = at or utcnow()
    now_s = ts(at)
    report = dict(jobs_expired=0, offers_expired=0, slots_filled=0, auto_paused=0, renewed=0, months_settled=0)

    for job in db.execute("SELECT id, customer_id, title FROM jobs WHERE status = 'open' AND closes_at <= ?",
                          (now_s,)).fetchall():
        db.execute("UPDATE jobs SET status = 'expired', closed_at = ?, close_reason = 'time' WHERE id = ?",
                   (now_s, job['id']))
        db.execute("UPDATE offers SET status = 'closed', resolved_at = ? WHERE job_id = ? AND status = 'active'",
                   (now_s, job['id']))
        notify(db, job['customer_id'], f'“{job["title"]}” closed after {config.JOB_OPEN_DAYS} days. '
                                       'Post it again if you still need someone.', f'/me/jobs/{job["id"]}', at)
        report['jobs_expired'] += 1

    stale = db.execute("SELECT id, trade_id FROM offers WHERE status = 'active' AND expires_at <= ?",
                       (now_s,)).fetchall()
    for o in stale:
        db.execute("UPDATE offers SET status = 'expired', resolved_at = ? WHERE id = ?", (now_s, o['id']))
    report['offers_expired'] = len(stale)
    for trade_id in {o['trade_id'] for o in stale}:
        report['auto_paused'] += 1 if _maybe_auto_pause(db, trade_id, at) else 0

    for t in db.execute('SELECT user_id FROM trades WHERE paused = 1 AND paused_until IS NOT NULL AND paused_until <= ?',
                        (now_s,)).fetchall():
        set_pause(db, t['user_id'], False, at=at)

    import billing
    report['renewed'] = billing.roll_demo_periods(db, at)

    short = db.execute("SELECT j.id FROM jobs j WHERE j.status = 'open' AND j.closes_at > ? AND "
                       "(SELECT COUNT(*) FROM offers o WHERE o.job_id = j.id AND o.status IN ('active','quoted')) < ?",
                       (now_s, config.TRADES_PER_JOB)).fetchall()
    for row in short:
        report['slots_filled'] += fill_slots(db, get_job(db, row['id']), at)

    _renewal_reminders(db, at)
    _nudge_quiet_customers(db, at)
    _ask_who_they_hired(db, at)
    db.commit()
    report['months_settled'] = evaluate_guarantees(db, at)
    return report


def try_lock(db, name, seconds, at=None):
    """Cross-process lock so only one worker runs the sweep at a time."""
    at = at or utcnow()
    until = ts(at + timedelta(seconds=seconds))
    if db.execute('UPDATE locks SET expires_at = ? WHERE name = ? AND expires_at <= ?',
                  (until, name, ts(at))).rowcount == 1:
        db.commit()
        return True
    got = db.execute('INSERT OR IGNORE INTO locks (name, holder, expires_at) VALUES (?,?,?)',
                     (name, '', until)).rowcount == 1
    db.commit()
    return got


# ── Read models for dashboards ────────────────────────────────────────────────

def fairness_snapshot(db, trade_id, tier, at=None):
    """How many jobs this trade was offered in 30 days vs trades on the same plan
    who share a trade category and an area with them."""
    at = at or utcnow()
    since = ts(at - timedelta(days=30))
    mine = db.execute('SELECT COUNT(*) AS n FROM offers WHERE trade_id = ? AND offered_at >= ?',
                      (trade_id, since)).fetchone()['n']
    peers = db.execute(
        'SELECT DISTINCT t.user_id FROM trade_categories tc1 '
        'JOIN trade_categories tc2 ON tc2.category_id = tc1.category_id '
        'JOIN trade_areas ta1 ON ta1.trade_id = tc1.trade_id '
        'JOIN trade_areas ta2 ON ta2.trade_id = tc2.trade_id AND ta2.area_id = ta1.area_id '
        "JOIN trades t ON t.user_id = tc2.trade_id AND t.sub_status = 'active' AND t.tier = ? "
        'WHERE tc1.trade_id = ? AND tc2.trade_id <> ?', (tier, trade_id, trade_id)).fetchall()
    ids = [p['user_id'] for p in peers]
    avg = None
    if ids:
        marks = ','.join('?' * len(ids))
        total = db.execute(f'SELECT COUNT(*) AS n FROM offers WHERE trade_id IN ({marks}) AND offered_at >= ?',
                           (*ids, since)).fetchone()['n']
        avg = total / len(ids)
    return {'mine': mine, 'peer_avg': avg, 'peers': len(ids)}


def _nudge_quiet_customers(db, at):
    """Tradies' biggest complaint is customers who never reply. Three days after
    a quote arrives unanswered, give the customer one friendly push."""
    cutoff = ts(at - timedelta(days=3))
    rows = db.execute(
        'SELECT j.id, j.customer_id, j.title, '
        "  (SELECT COUNT(*) FROM quotes q WHERE q.job_id = j.id AND q.status = 'sent' AND q.created_at <= ?) AS waiting "
        "FROM jobs j WHERE j.status IN ('open', 'full') AND j.nudged_at IS NULL", (cutoff,)).fetchall()
    for r in rows:
        if r['waiting']:
            who = 'tradie is' if r['waiting'] == 1 else 'tradies are'
            notify(db, r['customer_id'],
                   f'{r["waiting"]} {who} waiting to hear back about “{r["title"]}”. Share your details, accept a '
                   'quote, or close the job — it lets them know where they stand.', f'/me/jobs/{r["id"]}', at)
            db.execute('UPDATE jobs SET nudged_at = ? WHERE id = ?', (ts(at), r['id']))


def _ask_who_they_hired(db, at):
    """Once a job winds down without an accepted quote, ask who got the work —
    the trade who did it should get the credit (and it keeps refunds honest)."""
    week_ago = ts(at - timedelta(days=7))
    rows = db.execute(
        'SELECT id, customer_id, title FROM jobs WHERE followup_at IS NULL AND quote_count > 0 '
        "AND (status = 'expired' OR (status = 'full' AND created_at <= ?))", (week_ago,)).fetchall()
    for r in rows:
        notify(db, r['customer_id'], f'Did you end up hiring someone for “{r["title"]}”? Let us know — it takes '
                                     'ten seconds and helps the tradies who quoted.', f'/me/jobs/{r["id"]}', at)
        db.execute('UPDATE jobs SET followup_at = ? WHERE id = ?', (ts(at), r['id']))


def customer_record(db, customer_id):
    """What trades see about a customer before quoting — their track record of replying."""
    r = db.execute(
        "SELECT COUNT(*) AS quotes, "
        "SUM(CASE WHEN q.status <> 'sent' THEN 1 ELSE 0 END) AS answered "
        'FROM quotes q JOIN jobs j ON j.id = q.job_id WHERE j.customer_id = ?', (customer_id,)).fetchone()
    jobs = db.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN status = 'hired' THEN 1 ELSE 0 END) AS hired "
                      'FROM jobs WHERE customer_id = ?', (customer_id,)).fetchone()
    user = db.execute('SELECT created_at, phone_verified_at FROM users WHERE id = ?', (customer_id,)).fetchone()
    return {'quotes': r['quotes'] or 0, 'answered': r['answered'] or 0,
            'jobs': jobs['n'] or 0, 'hired': jobs['hired'] or 0,
            'since': user['created_at'][:7] if user else None,
            'phone_verified': bool(user and user['phone_verified_at'])}


def price_text(q):
    if q['price_type'] == 'site_visit':
        return 'Site visit first'
    if q['price_type'] == 'range':
        return f'${q["amount_low"]:,} – ${q["amount_high"]:,}'
    return f'${q["amount_low"]:,}'


def _timeline(job, offers, quotes):
    """Everything that has happened to a job, in order, in plain English."""
    events = [dict(when=job['created_at'], text='Job posted',
                   detail=f'{config.VALUE_BANDS[job["value_band"]]["label"]} · {job["category_name"]} · {job["area_name"]}')]

    def listed(names):
        return ', '.join(names[:4]) + (f' and {len(names) - 4} more' if len(names) > 4 else '')

    waves = {}
    for o in offers:
        waves.setdefault((o['offered_at'], o['wave']), []).append(o['business_name'])
    for (when, wave), names in waves.items():
        events.append(dict(when=when, detail=listed(names),
                           text=f'Wave {wave}: offered to {len(names)} trade{"s" if len(names) != 1 else ""}'))

    names_by_trade = {o['trade_id']: o['business_name'] for o in offers}
    for i, q in enumerate(quotes, 1):
        who = names_by_trade.get(q['trade_id'], 'A trade')
        events.append(dict(when=q['created_at'], text=f'Quote {i} of {config.MAX_QUOTES}: {who}',
                           detail=price_text(q)))
        if q['responded_at'] and q['status'] == 'accepted':
            events.append(dict(when=q['responded_at'], text=f'Customer accepted {who}',
                               detail='The job closed to everyone else'))
        elif q['responded_at'] and q['status'] == 'shortlisted':
            events.append(dict(when=q['responded_at'], text=f'Customer shared contact details with {who}',
                               detail='The trade can now call them'))

    words = {'expired': ('ran out of time', 'their slots went back into the queue'),
             'declined': ('passed on the job', 'the slot went to another trade straight away'),
             'closed': ('lost the slot', 'the job had filled up or closed')}
    resolved = {}
    for o in offers:
        if o['resolved_at'] and o['status'] in words:
            resolved.setdefault((o['resolved_at'], o['status']), []).append(o['business_name'])
    for (when, status), names in resolved.items():
        verb, why = words[status]
        events.append(dict(when=when, text=f'{len(names)} {verb}', detail=f'{listed(names)} — {why}'))

    if job['closed_at'] and job['close_reason'] != 'hired':
        why = {'time': f'Nobody was hired within {config.JOB_OPEN_DAYS} days',
               'hired_elsewhere': 'The customer hired someone off Level',
               'not_going_ahead': 'The customer isn’t going ahead'}
        events.append(dict(when=job['closed_at'], text='Job closed', detail=why.get(job['close_reason'], '')))

    return sorted(events, key=lambda e: e['when'])


def explain(db, job, at=None):
    """Everything behind one job: who it could go to, who has it now, what
    happens next, and what has already happened. Read-only — changes nothing."""
    at = at or utcnow()
    now_s = ts(at)
    band = config.VALUE_BANDS[job['value_band']]
    tiers = tiers_that_see(job['value_band'])
    marks = ','.join('?' * len(tiers))
    joined = ('FROM trade_categories tc JOIN trade_areas ta ON ta.trade_id = tc.trade_id '
              'JOIN trades t ON t.user_id = tc.trade_id WHERE tc.category_id = ? AND ta.area_id = ?')
    args = (job['category_id'], job['area_id'])
    paid = f"{joined} AND t.sub_status = 'active' AND t.period_end > ? AND t.tier IN ({marks})"

    def count(sql, params=()):
        return db.execute(sql, params).fetchone()['n']

    funnel = [
        ('Trades signed up', count('SELECT COUNT(*) AS n FROM trades'), 'Everyone with a trade account'),
        (f'Do {job["category_name"].lower()} work',
         count('SELECT COUNT(*) AS n FROM trade_categories WHERE category_id = ?', (job['category_id'],)),
         'Chose this trade on their profile'),
        (f'Cover {job["area_name"]}', count(f'SELECT COUNT(*) AS n {joined}', args), 'And work in this area'),
        (f'Plan covers {band["short"]} jobs', count(f'SELECT COUNT(*) AS n {paid}', (*args, now_s, *tiers)),
         'Paying for a plan that includes this job size'),
        ('Taking new jobs now', count(f'SELECT COUNT(*) AS n {paid} AND t.paused = 0', (*args, now_s, *tiers)),
         'Not paused'),
    ]

    offers = db.execute('SELECT o.*, t.business_name FROM offers o JOIN trades t ON t.user_id = o.trade_id '
                        'WHERE o.job_id = ? ORDER BY o.offered_at, o.id', (job['id'],)).fetchall()
    quotes = db.execute('SELECT * FROM quotes WHERE job_id = ? ORDER BY created_at, id', (job['id'],)).fetchall()
    priced = {q['trade_id']: price_text(q) for q in quotes}
    slots = [dict(o, price=priced.get(o['trade_id'])) for o in offers]
    queue = _candidate_rows(db, job, at)

    upcoming = []
    if job['status'] == 'open':
        soonest = db.execute("SELECT MIN(expires_at) AS e FROM offers WHERE job_id = ? AND status = 'active'",
                             (job['id'],)).fetchone()['e']
        if soonest:
            k = count("SELECT COUNT(*) AS n FROM offers WHERE job_id = ? AND status = 'active' AND expires_at = ?",
                      (job['id'], soonest))
            taking = min(k, len(queue))
            upcoming.append(dict(when=soonest, text=f'{k} offer{"s" if k != 1 else ""} run out of time',
                                 detail=(f'{taking} trade{"s" if taking != 1 else ""} from the queue take those slots'
                                         if taking else 'Nobody is left in the queue, so those slots stay empty')))
        upcoming.append(dict(when=job['closes_at'], text='Job closes for good',
                             detail=f'{config.JOB_OPEN_DAYS} days after posting'))

    return dict(funnel=funnel, slots=slots, queue=queue, upcoming=upcoming,
                timeline=_timeline(job, offers, quotes), tiers=tiers,
                live=sum(1 for s in slots if s['status'] in ('active', 'quoted')),
                lookback=ts(at - timedelta(days=config.ROTATION_LOOKBACK_DAYS)))


def trade_rating(db, trade_id):
    r = db.execute('SELECT COUNT(*) AS n, AVG(rating) AS avg, AVG(workmanship) AS w, AVG(communication) AS c, '
                   'AVG(timeliness) AS t, AVG(value_for_money) AS v FROM reviews WHERE trade_id = ?',
                   (trade_id,)).fetchone()
    return {k: r[k] for k in ('n', 'avg', 'w', 'c', 't', 'v')}
