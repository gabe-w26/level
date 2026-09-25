"""
Accounts: password reset, email verification, unsubscribe links and closing an
account.

Reset and verification links are random one-use tokens. Only a hash of each one
is stored, so a copy of the database doesn't hand anyone a way into an account.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

import config
import mailer
from engine import notify, ts, utcnow

RESET_HOURS = 2
VERIFY_HOURS = 72


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db, user_id, purpose, hours=None, at=None):
    at = at or utcnow()
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO tokens (user_id, purpose, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)',
               (user_id, purpose, _hash(token), ts(at), ts(at + timedelta(hours=hours)) if hours else None))
    db.commit()
    return token


def find(db, token, purpose, at=None):
    """The matching token, or None if it's wrong, used or out of date."""
    row = db.execute('SELECT * FROM tokens WHERE token_hash = ? AND purpose = ?',
                     (_hash(token or ''), purpose)).fetchone()
    if not row or row['used_at'] or (row['expires_at'] and row['expires_at'] <= ts(at or utcnow())):
        return None
    return row


def spend(db, token_id, at=None):
    db.execute('UPDATE tokens SET used_at = ? WHERE id = ?', (ts(at or utcnow()), token_id))
    db.commit()


def first_name(user):
    return (user['name'] or '').split(' ')[0] or 'there'


def send_reset(db, user, base_url):
    """Emails a reset link and returns it, so it can be logged while you're
    setting email up."""
    link = f'{base_url}/reset/{issue(db, user["id"], "reset", RESET_HOURS)}'
    mailer.send(user['email'], user['name'], f'Reset your {config.BRAND} password',
                f'Kia ora {first_name(user)},\n\n'
                f'Use this link within {RESET_HOURS} hours to choose a new password:\n\n{link}\n\n'
                'If you didn’t ask for this, ignore this email — nothing has changed.\n')
    return link


def send_verify(db, user, base_url):
    link = f'{base_url}/verify/{issue(db, user["id"], "verify", VERIFY_HOURS)}'
    mailer.send(user['email'], user['name'], f'Confirm your email for {config.BRAND}',
                f'Kia ora {first_name(user)},\n\n'
                'Confirm this is your email address:\n\n'
                f'{link}\n\n'
                'It helps trades and customers know they’re dealing with someone real.\n')
    return link


def close(db, user, at=None):
    """Close an account: stop the work, then scrub the personal details."""
    import billing
    import engine
    at = at or utcnow()
    now_s = ts(at)

    if user['role'] == 'customer':
        for row in db.execute("SELECT id FROM jobs WHERE customer_id = ? AND status IN ('open','full')",
                              (user['id'],)).fetchall():
            engine.close_job(db, engine.get_job(db, row['id']), 'not_going_ahead', at=at)
    if user['role'] == 'trade':
        trade = db.execute('SELECT * FROM trades WHERE user_id = ?', (user['id'],)).fetchone()
        if trade and trade['stripe_subscription_id'] and config.CHARGING:
            billing.cancel(db, trade)
        db.execute("UPDATE trades SET sub_status = 'closed', cancel_at_period_end = 0, paused = 1 "
                   'WHERE user_id = ?', (user['id'],))
        db.execute("UPDATE offers SET status = 'closed', resolved_at = ? WHERE trade_id = ? AND status = 'active'",
                   (now_s, user['id']))
        # The optional personal things go with the account, as the privacy page
        # promises. The referees' details are not even this person's to leave
        # behind — they belong to whoever agreed to vouch for them.
        db.execute('UPDATE trades SET photo = NULL, photo_at = NULL, photo_checked_at = NULL, '
                   'id_checked_at = NULL, vetting_status = NULL, vetting_at = NULL, vetting_note = NULL '
                   'WHERE user_id = ?', (user['id'],))
        db.execute('DELETE FROM trade_referees WHERE trade_id = ?', (user['id'],))

    db.execute('UPDATE users SET name = ?, email = ?, phone = NULL, password_hash = ?, email_alerts = 0, '
               'unsub_token = NULL, closed_at = ? WHERE id = ?',
               ('Closed account', f'closed+{user["id"]}@{config.BRAND.lower()}.invalid',
                secrets.token_urlsafe(32), now_s, user['id']))
    db.execute('DELETE FROM tokens WHERE user_id = ?', (user['id'],))
    db.commit()


def welcome(db, user, base_url, at=None):
    """First message after signing up: verify the email, and a pointer to what's next."""
    link = send_verify(db, user, base_url)
    where = '/trade' if user['role'] == 'trade' else '/me'
    notify(db, user['id'], f'Welcome to {config.BRAND}. Check your email to confirm your address.', where, at)
    db.commit()
    return link


PHONE_CODE_MINUTES = 15
PHONE_CODE_TRIES = 5


def _phone_hash(user_id, code):
    # Scoped to the user so two people who happen to get the same code never clash.
    return _hash(f'phone:{user_id}:{code}')


def send_phone_code(db, user, at=None):
    """Text a 6-digit code. Returns (code, sent) — the code is only for the log
    when texts aren't set up yet."""
    import sms
    at = at or utcnow()
    code = f'{secrets.randbelow(1000000):06d}'
    db.execute("DELETE FROM tokens WHERE user_id = ? AND purpose = 'phone'", (user['id'],))
    db.execute('INSERT INTO tokens (user_id, purpose, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)',
               (user['id'], 'phone', _phone_hash(user['id'], code), ts(at),
                ts(at + timedelta(minutes=PHONE_CODE_MINUTES))))
    db.commit()
    sent = sms.send(user['phone'], f'Your {config.BRAND} code is {code}. It expires in {PHONE_CODE_MINUTES} minutes.')
    return code, sent


def check_phone_code(db, user, code, at=None):
    """Returns 'ok', 'wrong', 'expired' or 'locked'."""
    at = at or utcnow()
    row = db.execute("SELECT * FROM tokens WHERE user_id = ? AND purpose = 'phone' AND used_at IS NULL "
                     'ORDER BY id DESC LIMIT 1', (user['id'],)).fetchone()
    if not row or row['expires_at'] <= ts(at):
        return 'expired'
    if row['attempts'] >= PHONE_CODE_TRIES:
        return 'locked'
    if not hmac.compare_digest(row['token_hash'], _phone_hash(user['id'], (code or '').strip())):
        db.execute('UPDATE tokens SET attempts = attempts + 1 WHERE id = ?', (row['id'],))
        db.commit()
        return 'wrong'
    db.execute('UPDATE tokens SET used_at = ? WHERE id = ?', (ts(at), row['id']))
    db.execute('UPDATE users SET phone_verified_at = ? WHERE id = ?', (ts(at), user['id']))
    db.commit()
    return 'ok'
