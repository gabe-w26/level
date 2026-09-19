"""
Email.

Two kinds go out:
  • Transactional — password resets and email confirmations. Always sent.
  • Alerts — new jobs, quotes and messages. These respect each person's alert
    setting and carry an unsubscribe link, which NZ spam law requires.

Nothing is sent until SMTP_HOST is set. Until then each message is printed to
the terminal instead, so you can still click a reset link while testing.
"""
import os
import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage

import config
from engine import ts, utcnow

SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
MAIL_FROM = os.environ.get('MAIL_FROM', f'{config.BRAND} <{config.SUPPORT_EMAIL}>')
# Render sets RENDER_EXTERNAL_URL itself, so links work without any setup.
BASE_URL = (os.environ.get('BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL')
            or 'http://localhost:5050').rstrip('/')
ENABLED = bool(SMTP_HOST)


def _build(to_email, to_name, subject, body, unsubscribe_url=None):
    msg = EmailMessage()
    msg['Subject'] = subject if len(subject) <= 90 else subject[:87] + '…'
    msg['From'] = MAIL_FROM
    msg['To'] = f'{to_name} <{to_email}>' if to_name else to_email
    msg['Reply-To'] = config.SUPPORT_EMAIL
    if unsubscribe_url:
        msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        body += f'\n\n—\nStop these emails: {unsubscribe_url}'
    msg.set_content(body)
    return msg


def send(to_email, to_name, subject, body, unsubscribe_url=None):
    """Send one message now. Returns True if it actually went out."""
    msg = _build(to_email, to_name, subject, body, unsubscribe_url)
    if not ENABLED:
        print(f'\n[email not set up — would have sent]\nTo: {to_email}\nSubject: {subject}\n\n{body}\n', flush=True)
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:                       # never let email break a request
        print(f'[email] could not send to {to_email}: {e}', flush=True)
        return False


def _unsub_url(db, user_id, token):
    if not token:
        token = secrets.token_urlsafe(16)
        db.execute('UPDATE users SET unsub_token = ? WHERE id = ?', (token, user_id))
    return f'{BASE_URL}/unsubscribe/{token}'


def flush(db, limit=50):
    """Email any alerts that haven't gone out yet. Only the last day's worth, so
    switching email on doesn't dump a week of old news on everyone."""
    if not ENABLED:
        return 0
    since = ts(utcnow() - timedelta(days=1))
    rows = db.execute('SELECT n.id, n.body, n.link, u.id AS user_id, u.email, u.name, u.unsub_token '
                      'FROM notifications n JOIN users u ON u.id = n.user_id '
                      'WHERE n.emailed_at IS NULL AND n.created_at >= ? AND u.email_alerts = 1 '
                      'AND u.closed_at IS NULL ORDER BY n.id LIMIT ?', (since, limit)).fetchall()
    sent = 0
    for r in rows:
        body = f'Kia ora {(r["name"] or "").split(" ")[0]},\n\n{r["body"]}\n\n{BASE_URL}{r["link"] or ""}\n'
        if send(r['email'], r['name'], r['body'], body, _unsub_url(db, r['user_id'], r['unsub_token'])):
            sent += 1
        db.execute('UPDATE notifications SET emailed_at = ? WHERE id = ?', (ts(utcnow()), r['id']))
    db.commit()
    return sent
