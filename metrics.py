"""
The numbers behind the marketplace.

Written to answer the questions you'd actually ask: are jobs getting quotes, are
they getting hired, how fast, and is the tradie side holding up. Every figure is
counted from the database at the moment you look — nothing is cached or
estimated, and a rate is only returned when there's something to divide by.

The hire rate is the one worth watching. No competitor publishes theirs.
"""
from datetime import timedelta

import config
from engine import parse_ts, ts, utcnow

WINDOWS = (('30 days', 30), ('90 days', 90), ('All time', None))


def _since(days, at):
    return ts(at - timedelta(days=days)) if days else '0000-01-01 00:00:00'


def _rate(part, whole):
    return (part / whole) if whole else None


def _median(values):
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def jobs(db, days=30, at=None):
    """The funnel: posted → quoted → hired, and how long it took."""
    at = at or utcnow()
    since = _since(days, at)
    rows = db.execute(
        'SELECT j.id, j.status, j.quote_count, j.created_at, j.closed_at, j.close_reason, '
        '(SELECT MIN(q.created_at) FROM quotes q WHERE q.job_id = j.id) AS first_quote_at '
        'FROM jobs j WHERE j.created_at >= ? AND j.status <> ?', (since, 'held')).fetchall()
    posted = len(rows)
    quoted = [r for r in rows if r['quote_count']]
    hired = [r for r in rows if r['status'] == 'hired']
    settled = [r for r in rows if r['status'] in ('hired', 'closed', 'expired')]
    elsewhere = [r for r in settled if r['close_reason'] == 'hired_elsewhere']
    waits = [(parse_ts(r['first_quote_at']) - parse_ts(r['created_at'])).total_seconds() / 3600
             for r in quoted if r['first_quote_at']]
    return {
        'posted': posted,
        'quoted': len(quoted),
        'quoted_rate': _rate(len(quoted), posted),
        'hired': len(hired),
        'hire_rate': _rate(len(hired), len(settled)),        # of jobs that reached an end
        'settled': len(settled),
        'no_quotes': len([r for r in settled if not r['quote_count']]),
        'hired_elsewhere': len(elsewhere),
        'avg_quotes': (sum(r['quote_count'] for r in rows) / posted) if posted else None,
        'full': len([r for r in rows if r['quote_count'] >= config.MAX_QUOTES]),
        'hours_to_first_quote': _median(waits),
    }


def offers(db, days=30, at=None):
    """The tradie side of the same funnel: offered → quoted, and who let it lapse."""
    at = at or utcnow()
    since = _since(days, at)
    rows = db.execute('SELECT status, COUNT(*) AS n FROM offers WHERE offered_at >= ? GROUP BY status',
                      (since,)).fetchall()
    counts = {r['status']: r['n'] for r in rows}
    total = sum(counts.values())
    resolved = total - counts.get('active', 0)
    return {'sent': total, 'quoted': counts.get('quoted', 0), 'expired': counts.get('expired', 0),
            'declined': counts.get('declined', 0), 'closed': counts.get('closed', 0),
            'active': counts.get('active', 0),
            'quote_rate': _rate(counts.get('quoted', 0), resolved),
            'lapse_rate': _rate(counts.get('expired', 0), resolved)}


def trades(db, days=30, at=None):
    """Supply: who's signed up, who's actually taking jobs, and who left."""
    at = at or utcnow()
    since = _since(days, at)
    row = db.execute(
        'SELECT COUNT(*) AS total, '
        "SUM(CASE WHEN t.sub_status = 'active' AND t.paused = 0 THEN 1 ELSE 0 END) AS taking_jobs, "
        "SUM(CASE WHEN t.paused = 1 THEN 1 ELSE 0 END) AS paused, "
        "SUM(CASE WHEN t.sub_status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled, "
        'SUM(CASE WHEN u.closed_at IS NOT NULL THEN 1 ELSE 0 END) AS closed '
        'FROM trades t JOIN users u ON u.id = t.user_id').fetchone()
    joined = db.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'trade' AND created_at >= ?",
                        (since,)).fetchone()['n']
    quoting = db.execute('SELECT COUNT(DISTINCT trade_id) AS n FROM quotes WHERE created_at >= ?',
                         (since,)).fetchone()['n']
    checked = db.execute('SELECT COUNT(*) AS n FROM trades WHERE licence_checked_at IS NOT NULL '
                         'OR nzbn_checked_at IS NOT NULL OR insurance_checked_at IS NOT NULL').fetchone()['n']
    return {'total': row['total'] or 0, 'taking_jobs': row['taking_jobs'] or 0, 'paused': row['paused'] or 0,
            'cancelled': row['cancelled'] or 0, 'closed': row['closed'] or 0, 'joined': joined,
            'quoting': quoting, 'checked': checked,
            'quoting_rate': _rate(quoting, row['taking_jobs'] or 0)}


def customers(db, days=30, at=None):
    at = at or utcnow()
    since = _since(days, at)
    joined = db.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'customer' AND created_at >= ?",
                        (since,)).fetchone()['n']
    posters = db.execute('SELECT customer_id, COUNT(*) AS n FROM jobs GROUP BY customer_id').fetchall()
    repeat = len([p for p in posters if p['n'] > 1])
    reviews = db.execute('SELECT COUNT(*) AS n FROM reviews WHERE created_at >= ?', (since,)).fetchone()['n']
    hired = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'hired' AND closed_at >= ?",
                       (since,)).fetchone()['n']
    return {'joined': joined, 'posted_ever': len(posters), 'repeat': repeat,
            'repeat_rate': _rate(repeat, len(posters)), 'reviews': reviews,
            'review_rate': _rate(reviews, hired)}


def outreach(db, at=None):
    """Did the free-lead emails actually bring anyone in?"""
    sent = db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE status = 'sent'").fetchone()['n']
    clicked = db.execute('SELECT COUNT(*) AS n FROM prospect_sends WHERE clicked_at IS NOT NULL').fetchone()['n']
    businesses = db.execute('SELECT COUNT(*) AS n FROM prospects WHERE leads_sent > 0').fetchone()['n']
    joined = db.execute("SELECT COUNT(*) AS n FROM prospects WHERE status = 'signed_up'").fetchone()['n']
    opted_out = db.execute("SELECT COUNT(*) AS n FROM prospects WHERE status = 'unsubscribed' "
                           "OR status = 'not_interested'").fetchone()['n']
    return {'sent': sent, 'clicked': clicked, 'click_rate': _rate(clicked, sent), 'businesses': businesses,
            'joined': joined, 'join_rate': _rate(joined, businesses), 'opted_out': opted_out,
            'opt_out_rate': _rate(opted_out, businesses)}


def reporting(db, at=None):
    """How well trades are keeping their progress-update promises, across every hired job."""
    import reporting as rep
    at = at or utcnow()
    jobs_ = db.execute("SELECT * FROM jobs WHERE status = 'hired' AND report_plan IS NOT NULL "
                       "AND report_plan <> ''").fetchall()
    kept = expected = counted = 0
    for job in jobs_:
        score = rep.score(job, rep.updates_for(db, job['id']), at)
        if score:
            counted += 1
            kept += sum(v['kept'] for v in score.values())
            expected += sum(v['expected'] for v in score.values())
    promised = db.execute("SELECT COUNT(*) AS n FROM quotes WHERE report_plan IS NOT NULL "
                          "AND report_plan <> ''").fetchone()['n']
    total_quotes = db.execute('SELECT COUNT(*) AS n FROM quotes').fetchone()['n']
    return {'jobs': counted, 'kept': kept, 'expected': expected, 'on_time': _rate(kept, expected),
            'quotes_promising': promised, 'promise_rate': _rate(promised, total_quotes)}


def everything(db, days=30, at=None):
    at = at or utcnow()
    return {'days': days, 'jobs': jobs(db, days, at), 'offers': offers(db, days, at),
            'trades': trades(db, days, at), 'customers': customers(db, days, at),
            'outreach': outreach(db, at), 'reporting': reporting(db, at)}
