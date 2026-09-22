"""
Push notifications to the Level phone app, through Expo's push service (Expo
passes them on to Apple and Google, so this is one HTTPS call per 100).

The sweep calls flush() every couple of minutes, next to mailer.flush and
sms.flush. It picks up notifications nobody has pushed yet, for people with
the app installed, and marks them with pushed_at. Only the last hour's worth,
and only ones created after that phone registered, so installing the app (or
turning pushes on) never sends a backlog. Tokens Expo says are dead are deleted.

With no phones registered this does nothing at all.
"""
import json
import urllib.error
import urllib.request
from datetime import timedelta

import config
from engine import ts, utcnow

EXPO_URL = 'https://exp.host/--/api/v2/push/send'
BATCH = 100                     # Expo's limit per request


def _post(messages):
    """Send one batch. Returns Expo's per-message tickets (same order), or None on failure."""
    req = urllib.request.Request(EXPO_URL, data=json.dumps(messages).encode(),
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get('data') or []
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ''
        print(f'[push] Expo refused a batch: {e.code} {detail}', flush=True)
    except Exception as e:                      # never let a push break the sweep
        print(f'[push] could not reach Expo: {e}', flush=True)
    return None


def flush(db, limit=200):
    """Push recent unsent notifications. Returns how many phones Expo accepted."""
    if not db.execute('SELECT 1 FROM push_tokens LIMIT 1').fetchone():
        return 0
    since = ts(utcnow() - timedelta(hours=1))
    rows = db.execute(
        'SELECT n.id, n.user_id, n.body, n.link FROM notifications n JOIN users u ON u.id = n.user_id '
        'WHERE n.pushed_at IS NULL AND n.created_at >= ? AND u.closed_at IS NULL '
        'AND EXISTS (SELECT 1 FROM push_tokens p WHERE p.user_id = n.user_id AND p.created_at <= n.created_at) '
        'ORDER BY n.id LIMIT ?', (since, limit)).fetchall()
    if not rows:
        return 0

    tokens, badges = {}, {}
    for uid in {r['user_id'] for r in rows}:
        tokens[uid] = [t['token'] for t in db.execute('SELECT token FROM push_tokens WHERE user_id = ?',
                                                      (uid,)).fetchall()]
        badges[uid] = db.execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                                 (uid,)).fetchone()['n']

    outgoing = []                               # (notification id, token, message)
    for r in rows:
        for token in tokens.get(r['user_id'], []):
            outgoing.append((r['id'], token, {
                'to': token, 'title': config.BRAND, 'body': r['body'], 'sound': 'default',
                'badge': badges[r['user_id']], 'channelId': 'default', 'priority': 'high',
                'data': {'link': r['link'] or '', 'notification_id': r['id']},
            }))

    accepted, dead, failed = 0, set(), set()
    for start in range(0, len(outgoing), BATCH):
        chunk = outgoing[start:start + BATCH]
        tickets = _post([m for _, _, m in chunk])
        if tickets is None:                     # Expo unreachable: try again next sweep (within the hour)
            failed.update(nid for nid, _, _ in chunk)
            continue
        for (nid, token, _), ticket in zip(chunk, tickets):
            ticket = ticket or {}
            if ticket.get('status') == 'ok':
                accepted += 1
            elif (ticket.get('details') or {}).get('error') == 'DeviceNotRegistered':
                dead.add(token)

    now_s = ts(utcnow())
    for token in dead:
        db.execute('DELETE FROM push_tokens WHERE token = ?', (token,))
    for r in rows:
        if r['id'] not in failed:
            db.execute('UPDATE notifications SET pushed_at = ? WHERE id = ?', (now_s, r['id']))
    db.commit()
    return accepted
