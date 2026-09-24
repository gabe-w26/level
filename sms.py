"""
Text messages through Twilio — for things that can't wait, mainly telling a
tradie about a new job, since the window to quote is short.

Nothing is sent until the Twilio details are entered in Admin → Setup. Until
then each text is printed to the log instead.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

import integrations
from engine import ts, utcnow


def enabled():
    return all(integrations.get(k) for k in ('twilio_sid', 'twilio_token', 'twilio_from'))


def nz_number(raw):
    """Turn '021 123 4567', '+64 21 123 4567' or '6421...' into +6421... ."""
    digits = re.sub(r'[^\d+]', '', raw or '')
    if digits.startswith('+'):
        number = '+' + re.sub(r'\D', '', digits)
    elif digits.startswith('00'):
        number = '+' + digits[2:]
    elif digits.startswith('64'):
        number = '+' + digits
    elif digits.startswith('0'):
        number = '+64' + digits[1:]
    else:
        return None
    return number if 10 <= len(number) <= 16 else None


def send(to, body):
    """Send one text now. Returns True if Twilio accepted it."""
    number = nz_number(to)
    if not number:
        return False
    if not enabled():
        print(f'[text not set up — would have sent] To: {number}\n{body}\n', flush=True)
        return False
    sid, token = integrations.get('twilio_sid'), integrations.get('twilio_token')
    data = urllib.parse.urlencode({'To': number, 'From': integrations.get('twilio_from'),
                                   'Body': body[:600]}).encode()
    req = urllib.request.Request(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json', data=data)
    req.add_header('Authorization', 'Basic ' + base64.b64encode(f'{sid}:{token}'.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get('message', '')
        except Exception:
            detail = ''
        print(f'[text] Twilio refused the text to {number}: {e.code} {detail}', flush=True)
    except Exception as e:                      # never let a text break a request
        print(f'[text] could not send to {number}: {e}', flush=True)
    return False


def last_error_hint():
    return ('Check the Account SID, Auth Token and the Twilio number. On a Twilio trial account you can '
            'only text numbers you have verified in Twilio.')


def flush(db, limit=50):
    """Text anyone with an alert marked for texting. Only the last hour's worth,
    so switching texts on doesn't send yesterday's jobs."""
    if not enabled():
        return 0
    since = ts(utcnow() - timedelta(hours=1))
    rows = db.execute('SELECT n.id, n.body, n.link, u.phone, u.text_alerts FROM notifications n '
                      'JOIN users u ON u.id = n.user_id WHERE n.sms = 1 AND n.texted_at IS NULL '
                      'AND n.created_at >= ? AND u.closed_at IS NULL ORDER BY n.id LIMIT ?',
                      (since, limit)).fetchall()
    sent = 0
    for r in rows:
        if r['text_alerts'] and r['phone']:
            if send(r['phone'], f'{r["body"]} {integrations.site_url()}{r["link"] or ""}'):
                sent += 1
        db.execute('UPDATE notifications SET texted_at = ? WHERE id = ?', (ts(utcnow()), r['id']))
    db.commit()
    return sent
