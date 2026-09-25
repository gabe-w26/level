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
import json
import smtplib
import urllib.error
import urllib.request
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import config
import integrations
from engine import ts, utcnow



def enabled():
    return bool(integrations.get('resend_key') or integrations.get('smtp_host'))


def how():
    """Which way out the mail is taking, for the admin pages to report."""
    if integrations.get('resend_key'):
        return 'resend'
    return 'smtp' if integrations.get('smtp_host') else None


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
    # Some servers add these; not all do, and a message without them looks
    # machine-generated in the worst way. Cheap to set ourselves.
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=(_mail_from().rsplit('@', 1)[-1].strip('>') or None))
    if unsubscribe_url:
        msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        # Gmail and Yahoo have expected one-click unsubscribe from bulk senders
        # since February 2024. The link in the body doesn't satisfy it — this
        # header is what lets the mail app show an Unsubscribe button, and not
        # having it is read as "won't let people leave".
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        body += f'\n\n—\nStop these emails: {unsubscribe_url}'
    msg.set_content(body)
    return msg


def send(to_email, to_name, subject, body, unsubscribe_url=None, reply_to=None):
    """Send one message now. Returns True if it actually went out.

    Two ways out. Resend when there's a key — better for anything cold, because
    the mail is signed as your own domain and bounces come back to us. Plain
    SMTP otherwise, which is fine for a handful of password resets.

    The message is identical either way: plain text, one link, a real
    unsubscribe. The transport is the only thing that changes.
    """
    msg = _build(to_email, to_name, subject, body, unsubscribe_url, reply_to)
    if not enabled():
        print(f'\n[email not set up — would have sent]\nTo: {to_email}\nSubject: {subject}\n\n{body}\n', flush=True)
        return False
    if integrations.get('resend_key'):
        return _send_resend(msg, to_email, subject)
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


def _send_resend(msg, to_email, subject):
    """Hand the message to Resend. Same headers as the SMTP path, so a
    one-click unsubscribe still works and the body is still plain text."""
    payload = {
        'from': msg['From'],
        'to': [to_email],
        'subject': subject,
        'text': msg.get_content(),
        'headers': {k: msg[k] for k in ('List-Unsubscribe', 'List-Unsubscribe-Post') if msg[k]},
    }
    if msg['Reply-To']:
        payload['reply_to'] = msg['Reply-To']
    request = urllib.request.Request(
        'https://api.resend.com/emails',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {integrations.get("resend_key")}',
                 'Content-Type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            json.loads(response.read().decode() or '{}')
        return True
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = json.loads(e.read().decode() or '{}').get('message', '')
        except Exception:
            pass
        print(f'[email] Resend refused {to_email}: {e.code} {detail}', flush=True)
        return False
    except Exception as e:                       # never let email break a request
        print(f'[email] Resend could not send to {to_email}: {e}', flush=True)
        return False


def _unsub_url(db, user_id, token):
    if not token:
        token = secrets.token_urlsafe(16)
        db.execute('UPDATE users SET unsub_token = ? WHERE id = ?', (token, user_id))
    return f'{integrations.site_url()}/unsubscribe/{token}'


def sent_today(db, at=None):
    """How many emails have actually gone out in the last 24 hours.

    Counted from the two places that record a send, so there is no separate
    tally to drift out of step with reality.
    """
    since = ts((at or utcnow()) - timedelta(days=1))
    alerts = db.execute('SELECT COUNT(*) AS n FROM notifications WHERE emailed_at >= ?',
                        (since,)).fetchone()['n'] or 0
    leads = db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE status = 'sent' AND sent_at >= ?",
                       (since,)).fetchone()['n'] or 0
    return alerts + leads


def allowance(db, kind='alert', at=None):
    """How many more emails of this kind we may send before we stop.

    Outreach gets a share of the day's cap and nothing more. A password reset
    that doesn't arrive is a person locked out of their account; a free lead
    that doesn't arrive is a tradie who never knew. They are not the same thing,
    so they don't get the same headroom.
    """
    used = sent_today(db, at)
    ceiling = config.MAIL_DAILY_CAP
    if kind == 'outreach':
        ceiling = int(ceiling * config.MAIL_OUTREACH_SHARE)
    return max(0, ceiling - used)


def flush(db, limit=50):
    """Email any alerts that haven't gone out yet. Only the last day's worth, so
    switching email on doesn't dump a week of old news on everyone."""
    if not enabled():
        return 0
    limit = min(limit, allowance(db, 'alert'))
    if limit <= 0:
        print('[email] daily cap reached — holding alerts until tomorrow', flush=True)
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
