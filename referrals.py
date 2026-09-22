"""
Referrals — three kinds:

  • Trade → trade: a trade's invite link (/join/<code>). When the new trade
    quotes on their first job, the referrer earns a free month (capped).
  • Customer → trade: a homeowner recommends a tradie they know. They get a
    link to send themselves, or we email the tradie once on their behalf.
  • Customer → customer: a share link (/r/<code>) so friends can post jobs.
    Tracked, not rewarded — posting is free anyway.

Free months are banked while the pilot is free, and used once billing is on:
as a Stripe customer credit in live mode, or a $0 month in demo billing.
"""
import secrets
import threading

import config
import integrations
import mailer
from engine import notify, ts, utcnow

REWARD_MONTHS = 1
MAX_MONTHS = int(getattr(config, 'REFERRAL_MAX_MONTHS', 12))   # per referrer, all time
MAX_RECOMMENDATIONS_PER_DAY = 10


# ── Codes ─────────────────────────────────────────────────────────────────────

def ref_code(db, user):
    """A customer's share code, made on first use."""
    if user['ref_code']:
        return user['ref_code']
    while True:
        code = secrets.token_urlsafe(5).lower().replace('_', '').replace('-', '')[:7]
        if len(code) >= 6 and not db.execute('SELECT 1 FROM users WHERE ref_code = ?', (code,)).fetchone():
            break
    db.execute('UPDATE users SET ref_code = ? WHERE id = ?', (code, user['id']))
    db.commit()
    return code


def referrer_for_code(db, code):
    return db.execute("SELECT id, name FROM users WHERE ref_code = ? AND role = 'customer' AND closed_at IS NULL",
                      ((code or '').lower(),)).fetchone()


def customer_joined(db, new_user_id, referrer_id):
    """A friend signed up through a customer's share link."""
    db.execute('UPDATE users SET referred_by = ? WHERE id = ? AND referred_by IS NULL', (referrer_id, new_user_id))
    notify(db, referrer_id, 'A friend you shared Level with just posted a job. Thanks for spreading the word.', '/me')
    db.commit()


# ── Trade → trade rewards ─────────────────────────────────────────────────────

def earn_for_first_quote(db, trade_id, at=None):
    """Called after a trade sends a quote. If it's their first and a trade invited them,
    the inviter earns a free month."""
    user = db.execute('SELECT id, referred_by FROM users WHERE id = ?', (trade_id,)).fetchone()
    if not user or not user['referred_by']:
        return False
    referrer = db.execute("SELECT u.id, t.business_name FROM users u JOIN trades t ON t.user_id = u.id "
                          "WHERE u.id = ? AND u.role = 'trade' AND u.closed_at IS NULL",
                          (user['referred_by'],)).fetchone()
    if not referrer:
        return False
    if db.execute('SELECT COUNT(*) AS n FROM quotes WHERE trade_id = ?', (trade_id,)).fetchone()['n'] != 1:
        return False
    if db.execute('SELECT 1 FROM referral_rewards WHERE referred_id = ?', (trade_id,)).fetchone():
        return False
    earned = db.execute('SELECT COALESCE(SUM(months), 0) AS n FROM referral_rewards WHERE referrer_id = ?',
                        (referrer['id'],)).fetchone()['n']
    if earned >= MAX_MONTHS:
        return False
    newbie = db.execute('SELECT business_name FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    db.execute('INSERT INTO referral_rewards (referrer_id, referred_id, months, earned_at) VALUES (?,?,?,?)',
               (referrer['id'], trade_id, REWARD_MONTHS, ts(at or utcnow())))
    when = 'We’ll use it once the free pilot ends.' if not config.CHARGING else 'It comes off your next bill.'
    notify(db, referrer['id'], f'{newbie["business_name"] if newbie else "Your invite"} sent their first quote — '
                               f'you’ve earned a free month. {when}', '/trade/referrals', at)
    db.commit()
    apply_credits(db, referrer['id'])
    return True


def banked(db, trade_id):
    rows = db.execute('SELECT r.*, t.business_name FROM referral_rewards r LEFT JOIN trades t ON t.user_id = r.referred_id '
                      'WHERE r.referrer_id = ? ORDER BY r.id DESC', (trade_id,)).fetchall()
    waiting = sum(r['months'] for r in rows if not r['applied_at'])
    used = sum(r['months'] for r in rows if r['applied_at'])
    return {'rows': rows, 'waiting': waiting, 'used': used, 'earned': waiting + used, 'cap': MAX_MONTHS}


def use_demo_month(db, trade_id, at=None):
    """Demo billing (not the free pilot): spend one banked month on a renewal. True if one was used."""
    r = db.execute('SELECT id FROM referral_rewards WHERE referrer_id = ? AND applied_at IS NULL ORDER BY id',
                   (trade_id,)).fetchone()
    if not r:
        return False
    db.execute("UPDATE referral_rewards SET applied_at = ?, applied_as = 'demo' WHERE id = ?", (ts(at or utcnow()), r['id']))
    return True


def apply_credits(db, trade_id):
    """Live billing: turn banked months into Stripe customer credit (it comes off the next invoices)."""
    if not config.CHARGING:
        return 0
    t = db.execute('SELECT * FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    if not t or not t['stripe_customer_id'] or not t['tier']:
        return 0
    import stripe
    import billing
    applied = 0
    for r in db.execute('SELECT * FROM referral_rewards WHERE referrer_id = ? AND applied_at IS NULL',
                        (trade_id,)).fetchall():
        try:
            txn = stripe.Customer.create_balance_transaction(
                t['stripe_customer_id'], amount=-billing.price_cents(t['tier']) * r['months'], currency='nzd',
                description=f'{config.BRAND} referral: {r["months"]} free month(s)')
        except Exception as e:                          # try again next time
            print(f'[referrals] Stripe credit failed for trade {trade_id}: {e}', flush=True)
            break
        db.execute("UPDATE referral_rewards SET applied_at = ?, applied_as = ? WHERE id = ?",
                   (ts(utcnow()), f'stripe:{txn["id"]}', r['id']))
        applied += 1
    db.commit()
    return applied


# ── Customer → trade recommendations ──────────────────────────────────────────

class RecommendError(Exception):
    pass


def recommend(db, customer, f, at=None):
    """Save a homeowner's recommendation. Returns (prospect, existing_trade_user_or_None)."""
    import re
    name = (f.get('name') or '').strip()[:80]
    business = (f.get('business_name') or '').strip()[:200] or name
    email = (f.get('email') or '').strip().lower()[:200]
    phone = (f.get('phone') or '').strip()[:40]
    cat = db.execute('SELECT id, name FROM categories WHERE id = ?', (f.get('category_id') or 0,)).fetchone()
    area = db.execute('SELECT id FROM areas WHERE id = ?', (f.get('area_id') or 0,)).fetchone()
    if len(business) < 2:
        raise RecommendError('Enter their name or business name.')
    if not cat:
        raise RecommendError('Pick their trade.')
    if not area:
        raise RecommendError('Pick the area they work in.')
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise RecommendError('That email doesn’t look right.')
    if not email and len(re.sub(r'\D', '', phone)) < 8:
        raise RecommendError('Add their email or mobile so they can be invited.')
    since = ts(utcnow().replace(hour=0, minute=0, second=0, microsecond=0))
    made = db.execute("SELECT COUNT(*) AS n FROM prospects WHERE recommended_by = ? AND created_at >= ?",
                      (customer['id'], since)).fetchone()['n']
    if made >= MAX_RECOMMENDATIONS_PER_DAY:
        raise RecommendError('That’s plenty for today — thanks! Try again tomorrow.')
    if email:
        existing = db.execute("SELECT id FROM users WHERE email = ? AND role = 'trade'", (email,)).fetchone()
        if existing:
            return None, existing
    p = db.execute('SELECT * FROM prospects WHERE email = ?', (email,)).fetchone() if email else None
    now = ts(at or utcnow())
    if not p:
        token = secrets.token_urlsafe(12)
        db.execute('INSERT INTO prospects (category_id, business_name, first_name, email, phone, source, '
                   "recommended_by, notes, status, token, created_at, updated_at) VALUES (?,?,?,?,?,'customer',?,?,'new',?,?,?)",
                   (cat['id'], business, name.split(' ')[0] if name else None, email or None, phone or None,
                    customer['id'], (f.get('note') or '').strip()[:300] or None, token, now, now))
        p = db.execute('SELECT * FROM prospects WHERE token = ?', (token,)).fetchone()
        db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (p['id'], area['id']))
    elif not p['recommended_by']:
        db.execute("UPDATE prospects SET recommended_by = ?, updated_at = ? WHERE id = ?", (customer['id'], now, p['id']))
        p = db.execute('SELECT * FROM prospects WHERE id = ?', (p['id'],)).fetchone()
    db.commit()
    return p, None


def invite_email(prospect, customer, category_name):
    join = f'{integrations.site_url()}/o/{prospect["token"]}'
    stop = f'{join}/stop'
    first = (customer['name'] or 'A customer').split(' ')[0]
    hi = prospect['first_name'] or 'there'
    subject = f'{first} recommended you on {config.BRAND}'
    body = (f'Hi {hi},\n\n{first} rates your work and recommended you on {config.BRAND}, a new Wellington site where '
            f'homeowners post jobs for local trades.\n\n'
            f'Each job goes to up to {config.TRADES_PER_JOB} {category_name.lower()} businesses in the area and closes '
            f'at {config.MAX_QUOTES} quotes. It’s free during our pilot — no card, no tokens, no lead fees.\n\n'
            f'Join here (2 minutes):\n{join}\n\n'
            f'This is the only email we’ll send unless you join. If you’d rather not hear from us at all, use the '
            f'link below.\n\nCheers,\nThe {config.BRAND} team\n{integrations.site_url()}')
    return subject, body, stop


def send_invite(db, prospect, customer):
    """Email the recommended tradie once, on the customer's behalf. Runs in the background."""
    if not prospect['email'] or prospect['status'] in ('unsubscribed', 'not_interested', 'signed_up') \
            or prospect['do_not_contact'] or prospect['leads_sent'] > 0:
        return False
    cat = db.execute('SELECT name FROM categories WHERE id = ?', (prospect['category_id'],)).fetchone()
    subject, body, stop = invite_email(prospect, customer, cat['name'])
    db.execute("UPDATE prospects SET leads_sent = leads_sent + 1, last_sent_at = ?, status = 'sent' WHERE id = ?",
               (ts(utcnow()), prospect['id']))
    db.commit()
    reply_to = integrations.get('smtp_user') or None
    threading.Thread(target=mailer.send, args=(prospect['email'], None, subject, body),
                     kwargs={'unsubscribe_url': stop, 'reply_to': reply_to}, daemon=True).start()
    return True


def recommended_joined(db, prospect, trade_user_id):
    """A recommended tradie signed up: tell both sides."""
    if not prospect['recommended_by']:
        return
    customer = db.execute('SELECT id, name FROM users WHERE id = ?', (prospect['recommended_by'],)).fetchone()
    if not customer:
        return
    db.execute('UPDATE users SET referred_by = COALESCE(referred_by, ?) WHERE id = ?', (customer['id'], trade_user_id))
    notify(db, customer['id'], f'{prospect["business_name"]} joined {config.BRAND} after you recommended them. Thank you!',
           f'/pros/{trade_user_id}')
    notify(db, trade_user_id, f'Welcome! {(customer["name"] or "A customer").split(" ")[0]} recommended you — '
                              'that’s how you found us.', '/trade')
    db.commit()


def leaderboard(db, limit=20):
    trades = db.execute(
        "SELECT u.id, t.business_name AS name, COUNT(r.id) AS joined, "
        "(SELECT COALESCE(SUM(months),0) FROM referral_rewards rr WHERE rr.referrer_id = u.id) AS months "
        "FROM users u JOIN trades t ON t.user_id = u.id JOIN users r ON r.referred_by = u.id "
        "WHERE u.role = 'trade' GROUP BY u.id, t.business_name ORDER BY COUNT(r.id) DESC LIMIT ?", (limit,)).fetchall()
    customers = db.execute(
        "SELECT u.id, u.name, "
        "(SELECT COUNT(*) FROM users f WHERE f.referred_by = u.id AND f.role = 'customer') AS friends, "
        "(SELECT COUNT(*) FROM prospects p WHERE p.recommended_by = u.id) AS recommended, "
        "(SELECT COUNT(*) FROM prospects p WHERE p.recommended_by = u.id AND p.status = 'signed_up') AS joined "
        "FROM users u WHERE u.role = 'customer' AND (EXISTS (SELECT 1 FROM users f WHERE f.referred_by = u.id) "
        "OR EXISTS (SELECT 1 FROM prospects p WHERE p.recommended_by = u.id)) "
        "ORDER BY 3 DESC, 4 DESC LIMIT ?", (limit,)).fetchall()
    return trades, customers
