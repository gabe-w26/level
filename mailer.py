"""
Email.

Two kinds go out:
  • Transactional — password resets and email confirmations. Always sent.
  • Alerts — new jobs, quotes and messages. These respect each person's alert
    setting and carry an unsubscribe link, which NZ spam law requires.

Nothing is sent until email is set up in Admin → Setup. Until then each message is printed to
the terminal instead, so you can still click a reset link while testing.
"""
import os
import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage

import config
import integrations
from engine import ts, utcnow



def enabled():
    return bool(integrations.get('smtp_host'))


def _mail_from():
    # Gmail and most providers only accept mail "from" the account you log in as.
    return (integrations.get('mail_from')
            or f'{config.BRAND} <{integrations.get("smtp_user") or config.SUPPORT_EMAIL}>')


def _build(to_email, to_name, subject, body, unsubscribe_url=None, reply_to=None):
    msg = EmailMessage()
    msg['Subject'] = subject if len(subject) <= 90 else subject[:87] + '…'
    msg['From'] = _mail_from()
    msg['To'] = f'{to_name} <{to_email}>' if to_name else to_email
    msg['Reply-To'] = reply_to or config.SUPPORT_EMAIL
    if unsubscribe_url:
        msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        body += f'\n\n—\nStop these emails: {unsubscribe_url}'
    msg.set_content(body)
    return msg


def send(to_email, to_name, subject, body, unsubscribe_url=None, reply_to=None):
    """Send one message now. Returns True if it actually went out."""
    msg = _build(to_email, to_name, subject, body, unsubscribe_url, reply_to)
    if not enabled():
        print(f'\n[email not set up — would have sent]\nTo: {to_email}\nSubject: {subject}\n\n{body}\n', flush=True)
        return False
    try:
        port = int(integrations.get('smtp_port') or 587)
        with smtplib.SMTP(integrations.get('smtp_host'), port, timeout=20) as smtp:
            smtp.starttls()
            if integrations.get('smtp_user'):
                smtp.login(integrations.get('smtp_user'), integrations.get('smtp_pass'))
            smtp.send_message(msg)
        return True
    except Exception as e:                       # never let email break a request
        print(f'[email] could not send to {to_email}: {e}', flush=True)
        return False


def _unsub_url(db, user_id, token):
    if not token:
        token = secrets.token_urlsafe(16)
        db.execute('UPDATE users SET unsub_token = ? WHERE id = ?', (token, user_id))
    return f'{integrations.site_url()}/unsubscribe/{token}'


def flush(db, limit=50):
    """Email any alerts that haven't gone out yet. Only the last day's worth, so
    switching email on doesn't dump a week of old news on everyone."""
    if not enabled():
        return 0
    since = ts(utcnow() - timedelta(days=1))
    rows = db.execute('SELECT n.id, n.body, n.link, u.id AS user_id, u.email, u.name, u.unsub_token '
                      'FROM notifications n JOIN users u ON u.id = n.user_id '
                      'WHERE n.emailed_at IS NULL AND n.created_at >= ? AND u.email_alerts = 1 '
                      'AND u.closed_at IS NULL ORDER BY n.id LIMIT ?', (since, limit)).fetchall()
    sent = 0
    for r in rows:
        body = f'Kia ora {(r["name"] or "").split(" ")[0]},\n\n{r["body"]}\n\n{integrations.site_url()}{r["link"] or ""}\n'
        if send(r['email'], r['name'], r['body'], body, _unsub_url(db, r['user_id'], r['unsub_token'])):
            sent += 1
        db.execute('UPDATE notifications SET emailed_at = ? WHERE id = ?', (ts(utcnow()), r['id']))
    db.commit()
    return sent
