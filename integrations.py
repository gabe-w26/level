"""
Keys and addresses for email, text messages and the AI helper.

Admins paste these into Level itself (Admin → Setup), so nothing needs changing
in the hosting dashboard. A value set as an environment variable still wins,
which keeps the old way working.
"""
import os
import time

# setting name -> environment variable that overrides it
KEYS = {
    'smtp_host': 'SMTP_HOST',
    'smtp_port': 'SMTP_PORT',
    'smtp_user': 'SMTP_USER',
    'smtp_pass': 'SMTP_PASS',
    'mail_from': 'MAIL_FROM',
    'twilio_sid': 'TWILIO_ACCOUNT_SID',
    'twilio_token': 'TWILIO_AUTH_TOKEN',
    'twilio_from': 'TWILIO_FROM',
    'anthropic_key': 'ANTHROPIC_API_KEY',
    'site_url': 'BASE_URL',
    'docket_url': 'DOCKET_URL',
    'docket_key': 'DOCKET_KEY',
}
SECRET = {'smtp_pass', 'twilio_token', 'anthropic_key', 'docket_key'}
PREFIX = 'integration.'

_cache = {'at': 0.0, 'values': {}}


def refresh(db, force=False):
    """Reload saved settings — at most once a minute per worker unless forced."""
    if not force and time.time() - _cache['at'] < 60:
        return
    rows = db.execute('SELECT key, value FROM settings WHERE key LIKE ?', (PREFIX + '%',)).fetchall()
    _cache['values'] = {r['key'][len(PREFIX):]: r['value'] or '' for r in rows}
    _cache['at'] = time.time()


def from_env(name):
    return bool(os.environ.get(KEYS[name]))


def get(name):
    return (os.environ.get(KEYS[name]) or _cache['values'].get(name) or '').strip()


def save(db, values):
    for name, value in values.items():
        if name not in KEYS:
            continue
        db.execute('DELETE FROM settings WHERE key = ?', (PREFIX + name,))
        if value:
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (PREFIX + name, value.strip()))
    db.commit()
    refresh(db, force=True)


def masked(name):
    value = get(name)
    if not value:
        return ''
    if name in SECRET:
        return '•' * 8 + value[-4:]
    return value


def site_url():
    return (get('site_url') or os.environ.get('RENDER_EXTERNAL_URL') or 'http://localhost:5050').rstrip('/')
