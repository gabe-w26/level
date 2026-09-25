"""
Sending a won job to Docket.

A tradie who uses Docket shouldn't have to retype a job they've just won. They
paste a key once, and from then on accepting a quote on Level puts a real job in
their Docket: the customer as a client, the site address, how to get in, what
they quoted, and a link back here.

Rules this sticks to:

  • **The tradie sets nothing up.** Level and Docket know each other already, so
    a tradie who uses both just finds their jobs there. We ask Docket "do you
    know this email address?" and if it does, that's the connection. Pasting
    keys was a step nobody should have had to take.
  • One direction only. Level pushes; it never reads anything out of Docket. A
    key that can only create jobs is a key that can't leak anything, and the
    tradie's own business data stays their own business.
  • Nothing is sent without the customer's say-so having already happened. The
    push fires on `accept_quote` — the moment the customer chose this tradie and
    their details became the tradie's to hold anyway. Nothing goes out about a
    job that was only quoted on.
  • It can fail and nobody notices. A Docket that's down, a revoked key, a typo
    in the address — none of it may break accepting a quote. Failures are
    recorded and retried by the sweep, and the tradie sees the state on their
    own page.
  • Sending twice is safe. Every push carries the same `external_ref`, and
    Docket updates rather than duplicating, so a retry after a timeout can't
    leave two jobs.
"""
import json
import urllib.error
import urllib.request

import config

TIMEOUT = 12                # a slow Docket must not hold up accepting a quote
MAX_TRIES = 5               # then stop and show the tradie what went wrong
RETRY_AFTER_MINUTES = 15


class DocketError(Exception):
    """Shown to the tradie as-is."""


def _post(base_url, key, path, payload=None, method='POST'):
    url = base_url.rstrip('/') + path
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json',
        'X-Partner-Key': key,
        'User-Agent': f'{config.BRAND}/1.0',
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = json.loads(e.read().decode() or '{}').get('error', '')
        except Exception:
            pass
        if e.code == 401:
            raise DocketError('Docket didn’t recognise that key. Make a new one in Docket and paste it again.')
        raise DocketError(body or f'Docket said no ({e.code}).')
    except urllib.error.URLError as e:
        raise DocketError(f'Couldn’t reach Docket at {base_url} — {e.reason}.')
    except ValueError:
        raise DocketError('That address answered, but not like Docket. Check it’s the right one.')


def clean_url(url):
    """The address a person pasted, made into something we can call."""
    url = (url or '').strip().rstrip('/')
    if not url:
        return ''
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


def test(base_url, key):
    """Behind the "Test" button. Returns the business name Docket reports."""
    base_url = clean_url(base_url)
    if not base_url or not key:
        raise DocketError('Paste both the key and your Docket address.')
    result = _post(base_url, key, '/api/partner/ping', method='GET')
    if not result.get('ok'):
        raise DocketError(result.get('error') or 'Docket didn’t recognise that key.')
    return result.get('company') or 'your business'


# ── What we send ────────────────────────────────────────────────────────────

def payload_for(db, job, trade_id):
    """One job, as Docket wants it.

    Only what the tradie is already entitled to: this is their customer, on a
    job they have won. Nothing about anyone else's quote goes in.
    """
    import worksite
    customer = db.execute('SELECT name, email, phone FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
    quote = db.execute("SELECT * FROM quotes WHERE job_id = ? AND trade_id = ? AND status = 'accepted'",
                       (job['id'], trade_id)).fetchone()
    site = worksite.get_site(db, job['id'])
    category = db.execute('SELECT name FROM categories WHERE id = ?', (job['category_id'],)).fetchone()
    area = db.execute('SELECT name FROM areas WHERE id = ?', (job['area_id'],)).fetchone()

    notes = [f'Won through {config.BRAND}.']
    if quote:
        import engine
        notes.append(f'Quoted {engine.price_text(quote)}'
                     f'{" incl. GST" if quote["gst_included"] else " plus GST"}.')
        if quote['inclusions']:
            notes.append(f'Includes: {quote["inclusions"]}')
        if quote['exclusions']:
            notes.append(f'Not included: {quote["exclusions"]}')
        if quote['warranty']:
            notes.append(f'Warranty: {quote["warranty"]}')
    notes.append(f'{config.BRAND} job: {_site_url()}/trade/jobs/{job["id"]}')

    access = ' · '.join(filter(None, [
        site.get('access') and f'Getting in: {site["access"]}',
        site.get('parking') and f'Parking: {site["parking"]}',
        site.get('pets') and f'Pets and people: {site["pets"]}',
        site.get('hazards') and f'Watch out for: {site["hazards"]}',
    ]))

    return {
        'source': 'level',
        'external_ref': f'{config.BRAND.lower()}-job-{job["id"]}',
        'title': job['title'],
        'description': job['description'],
        'job_type': category['name'] if category else '',
        'site_address': (job['address'] or '').strip() or f'{job["suburb"] or ""} {area["name"] if area else ""}'.strip(),
        'start_date': quote['available_from'] if quote and _looks_like_a_date(quote['available_from']) else None,
        'site_contact': f'{customer["name"]}{" · " + customer["phone"] if customer["phone"] else ""}'
                        if customer else '',
        'access_notes': access,
        'notes': '\n'.join(notes),
        'client': {
            'name': customer['name'] if customer else '',
            'email': customer['email'] if customer else '',
            'phone': customer['phone'] if customer else '',
        },
    }


def _looks_like_a_date(value):
    """Docket wants a date; "next week" is not one. Better to send nothing."""
    import re
    return bool(value and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.strip()))


def _site_url():
    import integrations
    return integrations.site_url()


def worth_mentioning(db, trade_id):
    """Should we tell this trade about Docket, and with what to show for it?

    Only once they have actually won work here — a tradie with no jobs doesn't
    need job management, and saying so before they've had a win is an advert
    rather than a suggestion. They can turn it off for good.
    """
    row = db.execute('SELECT docket_url, docket_hidden, docket_off FROM trades WHERE user_id = ?',
                     (trade_id,)).fetchone()
    if not row or row['docket_url'] or row['docket_hidden']:
        return None
    # Answered from what we already know, never by asking Docket: this runs on
    # every dashboard load, and a page that waits on another system is a page
    # that hangs when that system is slow.
    already = db.execute('SELECT COUNT(*) AS n FROM jobs WHERE hired_trade_id = ? AND docket_at IS NOT NULL',
                         (trade_id,)).fetchone()['n'] or 0
    if already:
        return None                       # they clearly have one; nothing to sell them
    won = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE hired_trade_id = ? AND status = 'hired'",
                     (trade_id,)).fetchone()['n'] or 0
    if won < 1:
        return None
    return {'won': won}


# ── Sending, and not minding when it fails ──────────────────────────────────

def platform():
    """The address and key that connect Level to Docket for everybody.

    Set once, by whoever runs Level. Nothing appears for a tradie until both
    are there, so an unconfigured Level behaves exactly as it always did.
    """
    import integrations
    url = clean_url(integrations.get('docket_url'))
    key = (integrations.get('docket_key') or '').strip()
    return {'url': url, 'key': key} if url and len(key) >= 24 else None


def settings_for(db, trade_id):
    """How we reach this tradie's Docket, and what to call them there.

    A tradie with their own key — a self-hosted Docket, or one under a different
    email — keeps using it. Everyone else goes through the platform connection
    and never has to think about it.
    """
    row = db.execute('SELECT docket_url, docket_key, docket_off FROM trades WHERE user_id = ?',
                     (trade_id,)).fetchone()
    if not row or row['docket_off']:
        return None
    if row['docket_url'] and row['docket_key']:
        return {'url': row['docket_url'], 'key': row['docket_key'], 'email': None}
    shared = platform()
    if not shared:
        return None
    who = db.execute('SELECT email FROM users WHERE id = ?', (trade_id,)).fetchone()
    return dict(shared, email=who['email'] if who else None)


def look_for(db, trade_id):
    """Does this tradie have a Docket? Returns the business name, or None.

    Never raises: a Docket that is down simply means we don't know yet, and the
    page says nothing rather than something wrong.
    """
    shared = platform()
    who = db.execute('SELECT email FROM users WHERE id = ?', (trade_id,)).fetchone()
    if not shared or not who or not who['email']:
        return None
    try:
        from urllib.parse import quote
        result = _post(shared['url'], shared['key'],
                       f'/api/partner/ping?email={quote(who["email"])}', method='GET')
    except Exception:
        return None
    return result.get('company') if result.get('matched') else None


def push(db, job, trade_id, at=None):
    """Send one job. Returns (ok, message). Never raises — see the docstring."""
    from engine import ts, utcnow
    at = at or utcnow()
    settings = settings_for(db, trade_id)
    if not settings:
        return True, None                       # not connected; nothing to do and nothing wrong
    try:
        body = payload_for(db, job, trade_id)
        if settings.get('email'):
            body['for_email'] = settings['email']        # the platform key has to say whose job it is
        result = _post(settings['url'], settings['key'], '/api/partner/job', body)
        if result.get('matched') is False:
            # They don't have a Docket. That is the normal case, not a failure,
            # and we mustn't keep asking about it.
            db.execute("UPDATE jobs SET docket_at = NULL, docket_error = NULL, docket_tries = ? "
                       'WHERE id = ?', (MAX_TRIES, job['id']))
            db.commit()
            return True, None
        number = result.get('job_number') or result.get('job_id')
        db.execute('UPDATE jobs SET docket_at = ?, docket_ref = ?, docket_error = NULL, docket_tries = 0 '
                   'WHERE id = ?', (ts(at), str(number or ''), job['id']))
        db.commit()
        return True, f'Sent to Docket as {number}.' if number else 'Sent to Docket.'
    except DocketError as e:
        db.execute('UPDATE jobs SET docket_error = ?, docket_tries = COALESCE(docket_tries, 0) + 1 '
                   'WHERE id = ?', (str(e)[:300], job['id']))
        db.commit()
        return False, str(e)
    except Exception as e:                      # a bug here must not cost somebody their job
        db.execute('UPDATE jobs SET docket_error = ?, docket_tries = COALESCE(docket_tries, 0) + 1 '
                   'WHERE id = ?', (f'Unexpected: {e}'[:300], job['id']))
        db.commit()
        return False, str(e)


def retry_failed(db, at=None, limit=10):
    """Have another go at the ones that didn't land. Runs with the sweep.

    Gives up after MAX_TRIES rather than hammering a Docket that is off, moved
    or no longer paying — the tradie can see the error and press the button.
    """
    from engine import parse_ts, utcnow
    at = at or utcnow()
    rows = db.execute(
        "SELECT * FROM jobs WHERE status = 'hired' AND hired_trade_id IS NOT NULL "
        'AND docket_at IS NULL AND docket_error IS NOT NULL '
        'AND COALESCE(docket_tries, 0) < ? ORDER BY id DESC LIMIT ?', (MAX_TRIES, limit)).fetchall()
    done = 0
    for job in rows:
        ok, _ = push(db, job, job['hired_trade_id'], at)
        done += 1 if ok else 0
    return done


def state(job):
    """What to tell the tradie on the job page."""
    keys = job.keys()

    def col(name):
        return job[name] if name in keys else None

    if col('docket_at'):
        return {'sent': True, 'ref': col('docket_ref'), 'at': col('docket_at')}
    if col('docket_error'):
        return {'sent': False, 'error': col('docket_error'),
                'gave_up': (col('docket_tries') or 0) >= MAX_TRIES}
    return None
