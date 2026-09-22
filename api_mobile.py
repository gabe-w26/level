"""
JSON API for the Level phone app (iOS and Android), mounted at /api/mobile.

The app signs in with a username or email and password and gets back a random
bearer token. Only a SHA-256 hash of it is stored (api_tokens), the same way
reset links are stored in accounts.py, so a copy of the database can't be used
to sign in. Requests send it as `Authorization: Bearer <token>`. The session
cookie is never read here, which is why app.py's CSRF check skips this prefix.

Business rules are never repeated here: jobs, offers, quotes, closing and
account closure all go through engine.py and accounts.py, and the helpers the
web pages use (create_user, validate_account, quotes_for_job, offer_stats...)
are reused from app.py through `web`, which init_app() sets.

Plans and payment are deliberately not part of the app (App Store guideline
3.1.1 / 3.1.3). A trade who hasn't finished setting up is told to finish on
the website.
"""
import functools
import hashlib
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

import accounts
import config
import engine
import integrations
import mailer
import referrals
import reporting
import sms
from engine import RuleError, parse_ts, ts, utcnow

bp = Blueprint('api_mobile', __name__, url_prefix='/api/mobile')
web = None                      # the app module, set by init_app()

TOKEN_IDLE_DAYS = 90            # a token unused for this long stops working
TOUCH_EVERY_SECONDS = 600       # how often last_used_at is refreshed


def init_app(app, web_module):
    global web
    web = web_module
    app.register_blueprint(bp)


def db():
    return web.db()


# ── Responses ─────────────────────────────────────────────────────────────────

def fail(message, status=400, errors=None):
    body = {'error': message}
    if errors:
        body['errors'] = errors
    return jsonify(body), status


@bp.errorhandler(HTTPException)
def _http_error(e):
    messages = {400: 'That request wasn’t understood.', 401: 'Please sign in again.',
                403: 'Your account can’t do that.', 404: 'Not found. It may have closed or been removed.',
                405: 'Not allowed.', 413: 'Photos too large. Keep each one under 10 MB.'}
    return fail(messages.get(e.code, e.description or 'Something went wrong.'), e.code)


def payload():
    """Fields from a JSON body or a form/multipart body, as a plain dict."""
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def text(data, key, default=''):
    v = data.get(key, default)
    return default if v is None else str(v)


def truthy(v):
    return v is True or str(v).lower() in ('1', 'true', 'yes', 'on')


def client_ip():
    return (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()


def site_url():
    # Same as integrations.site_url(), but locally it falls back to the address the app is using.
    return (integrations.get('site_url') or os.environ.get('RENDER_EXTERNAL_URL') or request.url_root).rstrip('/')


def photo_url(filename):
    return f'{request.url_root.rstrip("/")}/uploads/{filename}'


# ── Tokens ────────────────────────────────────────────────────────────────────

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(user_id, device=None):
    token = secrets.token_urlsafe(32)
    now_s = ts(utcnow())
    db().execute('INSERT INTO api_tokens (user_id, token_hash, device, created_at, last_used_at) VALUES (?,?,?,?,?)',
                 (user_id, _hash(token), (device or '')[:120] or None, now_s, now_s))
    db().execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now_s, user_id))
    db().commit()
    return token


def _bearer():
    header = request.headers.get('Authorization', '')
    return header[7:].strip() if header[:7].lower() == 'bearer ' else ''


def token_user():
    if 'api_user' in g:
        return g.api_user
    g.api_user = None
    token = _bearer()
    if token:
        row = db().execute('SELECT t.id AS token_id, t.last_used_at AS token_used_at, u.* FROM api_tokens t '
                           'JOIN users u ON u.id = t.user_id WHERE t.token_hash = ? AND u.closed_at IS NULL',
                           (_hash(token),)).fetchone()
        now = utcnow()
        if row:
            last = parse_ts(row['token_used_at'])
            if last and last < now - timedelta(days=TOKEN_IDLE_DAYS):
                db().execute('DELETE FROM api_tokens WHERE id = ?', (row['token_id'],))
                db().commit()
            else:
                if not last or (now - last).total_seconds() > TOUCH_EVERY_SECONDS:
                    db().execute('UPDATE api_tokens SET last_used_at = ? WHERE id = ?', (ts(now), row['token_id']))
                    db().commit()
                g.api_user = row
    return g.api_user


def auth(role=None):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            u = token_user()
            if not u:
                return fail('Please sign in again.', 401)
            if role and u['role'] != role:
                return fail('Your account can’t do that.', 403)
            return fn(*args, **kwargs)
        return inner
    return deco


def me():
    return g.api_user


# ── Shapes sent to the app ────────────────────────────────────────────────────

def trade_status(uid):
    """Can this trade get jobs, and if not, what to tell them. Never mentions
    prices or payment: plans are managed on the website."""
    trade = db().execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
    if not trade:
        return None
    setup = web.setup_complete(uid)
    active = engine.is_subscribed(trade)
    message = link = None
    if not setup:
        message = ('Finish setting up your business on the Level website — pick the trades you do and '
                   'the areas you cover — and jobs will start arriving here.')
        link = f'{site_url()}/trade/setup'
    elif not active:
        message = 'Your account isn’t set up to receive jobs yet. Finish setting it up on the Level website.'
        # While trades pay nothing (free pilot) the link just finishes setup. Once charging starts,
        # the app must not send people to a payment page, so it only names the website.
        link = None if config.CHARGING else f'{site_url()}/trade/setup'
    return {'business_name': trade['business_name'], 'setup_complete': setup, 'active': active,
            'needs_web_setup': not (setup and active), 'setup_message': message, 'setup_url': link,
            'paused': bool(trade['paused']), 'paused_until': trade['paused_until']}


def user_json(u):
    out = {'id': u['id'], 'role': u['role'], 'name': u['name'], 'email': u['email'], 'username': u['username'],
           'phone': u['phone'], 'email_verified': bool(u['email_verified_at']),
           'phone_verified': bool(u['phone_verified_at'])}
    if u['role'] == 'trade':
        out['trade'] = trade_status(u['id'])
    return out


def counts(u):
    n = lambda sql, args: db().execute(sql, args).fetchone()['n']
    out = {'notifications': n('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                              (u['id'],)), 'messages': 0, 'offers': 0}
    if u['role'] == 'customer':
        out['messages'] = n('SELECT COUNT(*) AS n FROM messages m JOIN jobs j ON j.id = m.job_id '
                            'WHERE j.customer_id = ? AND m.sender_id <> ? AND m.read_at IS NULL', (u['id'], u['id']))
    elif u['role'] == 'trade':
        out['messages'] = n('SELECT COUNT(*) AS n FROM messages WHERE trade_id = ? AND sender_id <> ? '
                            'AND read_at IS NULL', (u['id'], u['id']))
        out['offers'] = n("SELECT COUNT(*) AS n FROM offers WHERE trade_id = ? AND status = 'active' "
                          'AND expires_at > ?', (u['id'], ts(utcnow())))
    return out


def job_json(j, include_address=False):
    keys = j.keys()
    out = {k: j[k] for k in ('id', 'title', 'description', 'suburb', 'value_band', 'timing', 'property_type',
                             'status', 'quote_count', 'created_at', 'closes_at', 'closed_at', 'close_reason',
                             'category_id', 'area_id', 'hired_trade_id') if k in keys}
    for k in ('category_name', 'area_name', 'region', 'licence_note'):
        if k in keys:
            out[k] = j[k]
    band = config.VALUE_BANDS.get(j['value_band']) if 'value_band' in keys else None
    out['value_label'] = band['label'] if band else None
    out['timing_label'] = config.TIMING.get(j['timing']) if 'timing' in keys else None
    out['property_label'] = config.PROPERTY_TYPES.get(j['property_type']) if 'property_type' in keys else None
    out['max_quotes'] = config.MAX_QUOTES
    if include_address and 'address' in keys:
        out['address'] = j['address']
    return out


def quote_json(q, with_contact=False):
    fields = ('id', 'job_id', 'trade_id', 'price_type', 'amount_low', 'amount_high', 'message', 'inclusions',
              'exclusions', 'warranty', 'available_from', 'duration', 'status', 'created_at', 'responded_at')
    keys = q.keys()
    out = {k: q[k] for k in fields if k in keys}
    out['gst_included'] = bool(q['gst_included'])
    out['act_docs_promised'] = bool(q['act_docs_promised']) if 'act_docs_promised' in keys else False
    out['price_text'] = engine.price_text(q)
    for k in ('business_name', 'licence_type', 'licence_number', 'years_trading', 'workmanship_guarantee',
              'msg_count', 'rating', 'needs_act', 'title', 'suburb', 'area_name', 'category_name', 'job_status',
              'unread', 'value_band', 'quote_count'):
        if k in keys:
            out[k] = q[k]
    if 'licence_type' in keys:
        out['licence_checked'] = bool(q['licence_checked_at'])
        out['insurance_checked'] = bool(q['insurance_checked_at'])
        out['nzbn_checked'] = bool(q['nzbn_checked_at'])
    if 'report_plan' in keys:
        out['report_plan'] = reporting.parse(q['report_plan'])
        out['report_plan_text'] = reporting.describe(q['report_plan'])
    if 'report_record' in keys:
        out['report_record'] = q['report_record']
    if with_contact and q['status'] in ('shortlisted', 'accepted'):
        out['contact'] = {'name': q['contact_name'], 'phone': q['phone'], 'email': q['email']}
    return out


def progress_json(job):
    """The progress-updates section of a hired job, from the same helper the web pages use."""
    p = web._progress(job)
    if not p:
        return None
    return {
        'report_plan': p['report_plan'], 'report_plan_text': p['report_plan_text'],
        'work_started_on': job['work_started_on'], 'work_done_on': job['work_done_on'],
        'score': p['report_score'],
        'due': [k for k, v in p['report_score'].items() if v['due']],
        'updates': [{'id': u['id'], 'body': u['body'], 'kinds': reporting.parse(u['kinds']),
                     'local_date': u['local_date'], 'created_at': u['created_at'],
                     'photos': [photo_url(ph['filename']) for ph in p['update_photos'].get(u['id'], [])]}
                    for u in p['updates']],
        'today': p['nz_today'].isoformat(), 'max_photos': reporting.MAX_PHOTOS,
    }


def notification_json(n):
    return {'id': n['id'], 'body': n['body'], 'link': n['link'], 'created_at': n['created_at'],
            'read': bool(n['read_at'])}


# ── Public: app settings, sign in, sign up ────────────────────────────────────

@bp.get('/config')
def app_config():
    regions = {}
    for a in db().execute('SELECT * FROM areas ORDER BY id').fetchall():
        regions.setdefault(a['region'], []).append({'id': a['id'], 'slug': a['slug'], 'name': a['name']})
    return {
        'brand': config.BRAND,
        'categories': [{'id': c['id'], 'slug': c['slug'], 'name': c['name'], 'licence_note': c['licence_note']}
                       for c in web.all_categories()],
        'regions': [{'region': r, 'areas': areas} for r, areas in regions.items()],
        'value_bands': [{'key': k, 'label': v['label'], 'short': v['short']} for k, v in config.VALUE_BANDS.items()],
        'timing': [{'key': k, 'label': v} for k, v in config.TIMING.items()],
        'property_types': [{'key': k, 'label': v} for k, v in config.PROPERTY_TYPES.items()],
        'max_quotes': config.MAX_QUOTES, 'offer_window_hours': config.OFFER_WINDOW_HOURS,
        'trades_per_job': config.TRADES_PER_JOB, 'max_photos': web.MAX_PHOTOS,
        'contract_threshold': config.CONTRACT_THRESHOLD, 'support_email': config.SUPPORT_EMAIL,
        'links': {'site': site_url(), 'terms': f'{site_url()}/terms', 'privacy': f'{site_url()}/privacy'},
    }


@bp.post('/login')
def login():
    """Same rules as the web login: email or username, 10 failures per IP in
    15 minutes, closed accounts refused."""
    f = payload()
    ip = client_ip()
    if web._too_many_attempts(ip):
        return fail('Too many attempts. Wait 15 minutes, then try again.', 429)
    ident = re.sub(r'\s+', ' ', text(f, 'login') or text(f, 'email') or text(f, 'username')).strip().lower()
    u = db().execute('SELECT * FROM users WHERE email = ? OR username = ?', (ident, ident)).fetchone() if ident else None
    if u and u['closed_at']:
        return fail('That account has been closed. Sign up again if you’d like to come back.', 401)
    if not u or not check_password_hash(u['password_hash'], text(f, 'password')):
        web._login_failures[ip].append(time.time())
        return fail('That email or username and password don’t match.', 401)
    if u['role'] == 'admin':
        return fail('Admin accounts use the Level website.', 403)
    token = issue_token(u['id'], text(f, 'device'))
    return {'token': token, 'user': user_json(u)}


@bp.post('/signup')
def signup():
    """Customer or trade sign-up, with the web form's checks. Trades finish
    their profile (and plan) on the website."""
    f = payload()
    role = text(f, 'role')
    if role not in ('customer', 'trade'):
        return fail('Choose whether you need a tradie or you are one.')
    form = {k: text(f, k) for k in ('name', 'email', 'phone', 'password', 'business_name')}
    errors = web.validate_account(form)
    if role == 'trade' and len(form['business_name'].strip()) < 2:
        errors['business_name'] = 'Enter your business or trading name.'
    if errors:
        return fail(next(iter(errors.values())), 400, errors)
    try:
        uid = web.create_user(role, form)
    except sqlite3.IntegrityError:
        return fail('There’s already an account with this email. Log in instead.', 400,
                    {'email': 'There’s already an account with this email. Log in instead.'})
    if role == 'trade':
        db().execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                     (uid, form['business_name'].strip(), ts(utcnow())))
    db().commit()
    u = db().execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
    accounts.welcome(db(), u, site_url())
    token = issue_token(uid, text(f, 'device'))
    return {'token': token, 'user': user_json(u)}, 201


@bp.post('/forgot')
def forgot():
    """Emails a reset link. Always answers the same way, like the web page."""
    f = payload()
    ip = client_ip()
    email = text(f, 'email').strip().lower()
    u = db().execute('SELECT * FROM users WHERE email = ? AND closed_at IS NULL', (email,)).fetchone()
    if u and not web._too_many_attempts(ip):
        link = accounts.send_reset(db(), u, site_url())
        if not mailer.enabled():
            web.app.logger.warning('Email is not set up. Reset link for %s: %s', email, link)
    web._login_failures[ip].append(time.time())
    return {'ok': True, 'message': 'If there’s an account with that email, a reset link is on its way.'}


# ── The signed-in person ──────────────────────────────────────────────────────

@bp.post('/logout')
@auth()
def logout():
    f = payload()
    if text(f, 'push_token'):
        db().execute('DELETE FROM push_tokens WHERE token = ? AND user_id = ?', (text(f, 'push_token'), me()['id']))
    db().execute('DELETE FROM api_tokens WHERE id = ?', (me()['token_id'],))
    db().commit()
    return {'ok': True}


@bp.get('/me')
@auth()
def get_me():
    return {'user': user_json(me()), 'counts': counts(me())}


@bp.delete('/me')
@auth()
def delete_me():
    """Delete the account from inside the app (App Store 5.1.1(v)). Uses the
    same close-and-scrub as Settings → Close account on the website."""
    f = payload()
    u = me()
    if not check_password_hash(u['password_hash'], text(f, 'password')):
        return fail('That password isn’t right.', 400, {'password': 'That password isn’t right.'})
    person = db().execute('SELECT * FROM users WHERE id = ?', (u['id'],)).fetchone()
    accounts.close(db(), person)
    db().execute('DELETE FROM api_tokens WHERE user_id = ?', (u['id'],))
    db().execute('DELETE FROM push_tokens WHERE user_id = ?', (u['id'],))
    db().commit()
    return {'ok': True, 'message': 'Your account is closed and your details have been deleted.'}


@bp.post('/push-tokens')
@auth()
def add_push_token():
    f = payload()
    token = text(f, 'token').strip()
    if not re.fullmatch(r'(Expo|Exponent)PushToken\[[^\]]{10,200}\]', token):
        return fail('That isn’t an Expo push token.')
    platform = text(f, 'platform')[:20] or None
    # A phone that changes hands (sign out, someone else signs in) moves to the new person.
    db().execute('DELETE FROM push_tokens WHERE token = ?', (token,))
    db().execute('INSERT INTO push_tokens (user_id, token, platform, created_at) VALUES (?,?,?,?)',
                 (me()['id'], token, platform, ts(utcnow())))
    db().commit()
    return {'ok': True}


@bp.delete('/push-tokens')
@auth()
def remove_push_token():
    db().execute('DELETE FROM push_tokens WHERE token = ? AND user_id = ?', (text(payload(), 'token'), me()['id']))
    db().commit()
    return {'ok': True}


@bp.post('/verify-phone/send')
@auth()
def send_phone_code():
    if me()['phone_verified_at']:
        return {'ok': True, 'verified': True}
    _, sent = accounts.send_phone_code(db(), me())
    return {'ok': True, 'sent': sent, 'texts_on': sms.enabled()}


@bp.post('/verify-phone')
@auth()
def verify_phone():
    result = accounts.check_phone_code(db(), me(), text(payload(), 'code'))
    if result != 'ok':
        return fail({'wrong': 'That code isn’t right. Check the text and try again.',
                     'expired': 'That code has run out. Send yourself a new one.',
                     'locked': 'Too many wrong tries. Send yourself a new code.'}[result])
    released = engine.release_held(db(), me()['id'])
    return {'ok': True, 'released_to': released}


# ── Notifications ─────────────────────────────────────────────────────────────

@bp.get('/notifications')
@auth()
def notifications():
    rows = db().execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 100',
                        (me()['id'],)).fetchall()
    return {'notifications': [notification_json(n) for n in rows], 'counts': counts(me())}


@bp.post('/notifications/read')
@auth()
def notifications_read_all():
    db().execute('UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL',
                 (ts(utcnow()), me()['id']))
    db().commit()
    return {'ok': True}


@bp.post('/notifications/<int:nid>/read')
@auth()
def notification_read(nid):
    db().execute('UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ? AND read_at IS NULL',
                 (ts(utcnow()), nid, me()['id']))
    db().commit()
    return {'ok': True}


# ── Messages (customer ↔ trade, per job) ──────────────────────────────────────

@bp.get('/threads')
@auth()
def threads():
    u = me()
    if u['role'] not in ('customer', 'trade'):
        return fail('Your account can’t do that.', 403)
    whose = 'j.customer_id = ?' if u['role'] == 'customer' else 'q.trade_id = ?'
    rows = db().execute(
        'SELECT q.job_id, q.trade_id, q.created_at, j.title, t.business_name, cu.name AS customer_name, '
        '  (SELECT body FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        '    ORDER BY m.id DESC LIMIT 1) AS last_body, '
        '  (SELECT created_at FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        '    ORDER BY m.id DESC LIMIT 1) AS last_at, '
        '  (SELECT COUNT(*) FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        '    AND m.sender_id <> ? AND m.read_at IS NULL) AS unread '
        'FROM quotes q JOIN jobs j ON j.id = q.job_id JOIN trades t ON t.user_id = q.trade_id '
        'JOIN users cu ON cu.id = j.customer_id '
        f'WHERE {whose} '
        'ORDER BY COALESCE((SELECT MAX(created_at) FROM messages m WHERE m.job_id = q.job_id '
        '  AND m.trade_id = q.trade_id), q.created_at) DESC LIMIT 100', (u['id'], u['id'])).fetchall()
    out = []
    for r in rows:
        out.append({'job_id': r['job_id'], 'trade_id': r['trade_id'], 'title': r['title'],
                    'with': r['business_name'] if u['role'] == 'customer' else r['customer_name'],
                    'last_body': r['last_body'], 'last_at': r['last_at'] or r['created_at'],
                    'unread': r['unread']})
    return {'threads': out}


def _thread_parts(job_id, trade_id):
    u = me()
    job = engine.get_job(db(), job_id)
    quote = db().execute('SELECT * FROM quotes WHERE job_id = ? AND trade_id = ?', (job_id, trade_id)).fetchone()
    if not job or not quote or u['id'] not in (job['customer_id'], trade_id):
        return None, None
    return job, quote


@bp.get('/threads/<int:job_id>/<int:trade_id>')
@auth()
def thread(job_id, trade_id):
    job, quote = _thread_parts(job_id, trade_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    u = me()
    db().execute('UPDATE messages SET read_at = ? WHERE job_id = ? AND trade_id = ? AND sender_id <> ? '
                 'AND read_at IS NULL', (ts(utcnow()), job_id, trade_id, u['id']))
    db().commit()
    msgs = db().execute('SELECT * FROM messages WHERE job_id = ? AND trade_id = ? ORDER BY id',
                        (job_id, trade_id)).fetchall()
    trade = db().execute('SELECT business_name FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    customer = db().execute('SELECT name FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
    return {'job': {'id': job['id'], 'title': job['title'], 'status': job['status']},
            'trade_name': trade['business_name'] if trade else '', 'customer_name': customer['name'] if customer else '',
            'with': (trade['business_name'] if trade else '') if u['id'] == job['customer_id'] else
                    (customer['name'] if customer else ''),
            'messages': [{'id': m['id'], 'body': m['body'], 'created_at': m['created_at'],
                          'mine': m['sender_id'] == u['id']} for m in msgs]}


@bp.post('/threads/<int:job_id>/<int:trade_id>')
@auth()
def send_message(job_id, trade_id):
    job, quote = _thread_parts(job_id, trade_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    body = text(payload(), 'body').strip()
    if not body:
        return fail('Write a message first.')
    u = me()
    other = trade_id if u['id'] == job['customer_id'] else job['customer_id']
    cur = db().execute('INSERT INTO messages (job_id, trade_id, sender_id, body, created_at) VALUES (?,?,?,?,?)',
                       (job_id, trade_id, u['id'], body[:4000], ts(utcnow())))
    engine.notify(db(), other, f'New message about “{job["title"]}”.', f'/thread/{job_id}/{trade_id}')
    db().commit()
    return {'ok': True, 'id': cur.lastrowid}, 201


# ── Customer ──────────────────────────────────────────────────────────────────

@bp.get('/customer/jobs')
@auth('customer')
def customer_jobs():
    rows = db().execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
                        'WHERE j.customer_id = ? ORDER BY j.id DESC', (me()['id'],)).fetchall()
    return {'jobs': [job_json(j, include_address=True) for j in rows]}


def _photo_files():
    return [p for p in request.files.getlist('photos') if p and p.filename]


def _photo_error(photos, existing=0):
    if existing + len(photos) > web.MAX_PHOTOS:
        return f'Add up to {web.MAX_PHOTOS} photos.'
    if any(p.filename.rsplit('.', 1)[-1].lower() not in web.PHOTO_TYPES for p in photos):
        return 'Photos must be JPG, PNG, WEBP or HEIC.'
    return None


def _save_photos(job_id, photos):
    for p in photos:
        name = f'{uuid.uuid4().hex}.{p.filename.rsplit(".", 1)[-1].lower()}'
        p.save(os.path.join(web.UPLOAD_DIR, name))
        db().execute('INSERT INTO job_photos (job_id, filename, created_at) VALUES (?,?,?)',
                     (job_id, name, ts(utcnow())))


@bp.post('/customer/jobs')
@auth('customer')
def post_job():
    """Post a job. JSON, or multipart with `photos` files. Checks match the
    web form in app.post_job."""
    f = payload()
    errors = {}
    cat = db().execute('SELECT * FROM categories WHERE slug = ?', (text(f, 'category'),)).fetchone()
    area = db().execute('SELECT * FROM areas WHERE slug = ?', (text(f, 'area'),)).fetchone()
    if not cat:
        errors['category'] = 'Choose the kind of trade you need.'
    if not area:
        errors['area'] = 'Choose where the job is.'
    if len(text(f, 'suburb').strip()) < 2:
        errors['suburb'] = 'Enter the suburb. Trades see this, not your address.'
    if not 5 <= len(text(f, 'title').strip()) <= 80:
        errors['title'] = 'Give the job a short title, 5 to 80 characters.'
    if len(text(f, 'description').strip()) < 30:
        errors['description'] = 'Describe the job in a few sentences so trades can quote accurately.'
    if text(f, 'value_band') not in config.VALUE_BANDS:
        errors['value_band'] = 'Pick a rough budget. Your best guess is fine.'
    if text(f, 'timing') not in config.TIMING:
        errors['timing'] = 'Choose when you’d like it done.'
    if text(f, 'property_type') not in config.PROPERTY_TYPES:
        errors['property_type'] = 'Choose the type of property.'
    photos = _photo_files()
    photo_error = _photo_error(photos)
    if photo_error:
        errors['photos'] = photo_error
    if errors:
        return fail(next(iter(errors.values())), 400, errors)
    u = me()
    # With texts switched on, a job only goes out once the phone number is confirmed (as on the web).
    hold = sms.enabled() and not u['phone_verified_at']
    job_id, offered = engine.post_job(db(), u['id'], dict(
        category_id=cat['id'], area_id=area['id'], suburb=text(f, 'suburb').strip(),
        address=text(f, 'address').strip() or None, title=text(f, 'title').strip(),
        description=text(f, 'description').strip(), value_band=text(f, 'value_band'), timing=text(f, 'timing'),
        property_type=text(f, 'property_type')), hold=hold)
    _save_photos(job_id, photos)
    db().commit()
    if hold:
        accounts.send_phone_code(db(), u)
    return {'job_id': job_id, 'offered': offered, 'held': hold,
            'message': ('Confirm your phone number to send this job out.' if hold else
                        f'Job posted. {offered} local {cat["name"].lower()} trades can see it now.' if offered else
                        'Job posted. No trades cover this area yet — we’ll offer it the moment one joins.')}, 201


def _my_job(job_id):
    job = engine.get_job(db(), job_id)
    return job if job and job['customer_id'] == me()['id'] else None


@bp.post('/customer/jobs/<int:job_id>/photos')
@auth('customer')
def add_photos(job_id):
    job = _my_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    if job['status'] not in ('open', 'full', 'held'):
        return fail('Closed jobs can’t be changed.')
    photos = _photo_files()
    if not photos:
        return fail('Choose a photo to add.')
    error = _photo_error(photos, existing=len(web._photos(job_id)))
    if error:
        return fail(error, 400, {'photos': error})
    _save_photos(job_id, photos)
    db().commit()
    return {'ok': True, 'photos': [photo_url(p['filename']) for p in web._photos(job_id)]}, 201


@bp.get('/customer/jobs/<int:job_id>')
@auth('customer')
def customer_job(job_id):
    job = _my_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    reviewed = bool(db().execute('SELECT 1 FROM reviews WHERE job_id = ?', (job_id,)).fetchone())
    stats = web.offer_stats(job_id)
    return {'job': job_json(job, include_address=True),
            'quotes': [quote_json(q, with_contact=True) for q in web.quotes_for_job(job)],
            'stats': {'offered': stats['total'], 'waiting': stats['active'], 'quoted': stats['quoted'],
                      'passed': stats['passed'], 'next_expiry': stats['next_expiry']},
            'photos': [photo_url(p['filename']) for p in web._photos(job_id)],
            'reviewed': reviewed, 'can_review': job['status'] == 'hired' and not reviewed,
            'can_close': job['status'] in ('open', 'full', 'expired'),
            'held': job['status'] == 'held', 'progress': progress_json(job)}


@bp.post('/customer/jobs/<int:job_id>/quotes/<int:quote_id>/<action>')
@auth('customer')
def quote_action(job_id, quote_id, action):
    job = _my_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    f = payload()
    try:
        if action == 'share':
            engine.share_contact(db(), job, quote_id)
            message = 'Your contact details are now visible to this trade.'
        elif action == 'accept':
            engine.accept_quote(db(), job, quote_id, act_ack=truthy(f.get('act_ack')))
            message = 'Quote accepted. The trade has your contact details and the other trades have been told.'
        elif action == 'decline':
            engine.decline_quote(db(), job, quote_id)
            message = 'Quote declined. The trade has been told.'
        elif action == 'flag':
            engine.flag_quote(db(), job, quote_id, text(f, 'reason') or 'Not a genuine quote')
            message = 'Thanks. We’ll review this quote.'
        else:
            return fail('Not found.', 404)
    except RuleError as e:
        return fail(str(e))
    return {'ok': True, 'message': message}


@bp.post('/customer/jobs/<int:job_id>/close')
@auth('customer')
def close_job(job_id):
    job = _my_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    try:
        engine.close_job(db(), job, text(payload(), 'outcome') or 'not_going_ahead')
    except RuleError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Job closed. Trades waiting on it have been told.'}


@bp.post('/customer/jobs/<int:job_id>/review')
@auth('customer')
def review(job_id):
    job = _my_job(job_id)
    if not job or job['status'] != 'hired':
        return fail('Not found. It may have closed or been removed.', 404)
    if db().execute('SELECT 1 FROM reviews WHERE job_id = ?', (job_id,)).fetchone():
        return fail('You’ve already reviewed this job.')
    f = payload()
    scores, errors = {}, {}
    for key, _ in web.REVIEW_PARTS:
        v = text(f, key)
        if v not in ('1', '2', '3', '4', '5'):
            errors[key] = 'Choose 1 to 5 stars.'
        else:
            scores[key] = int(v)
    if errors:
        return fail('Choose 1 to 5 stars for each part.', 400, errors)
    trade_id = job['hired_trade_id']
    db().execute('INSERT INTO reviews (job_id, trade_id, customer_id, rating, workmanship, communication, '
                 'timeliness, value_for_money, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (job_id, trade_id, me()['id'], sum(scores.values()) / 4, scores['workmanship'],
                  scores['communication'], scores['timeliness'], scores['value_for_money'],
                  text(f, 'body').strip(), ts(utcnow())))
    engine.notify(db(), trade_id, f'You have a new review for “{job["title"]}”.', f'/pros/{trade_id}')
    db().commit()
    return {'ok': True, 'message': 'Review posted. Thanks for helping other Kiwis choose.'}, 201


@bp.get('/review-parts')
def review_parts():
    return {'parts': [{'key': k, 'label': v} for k, v in web.REVIEW_PARTS]}


# ── Trade ─────────────────────────────────────────────────────────────────────

@bp.get('/trade/offers')
@auth('trade')
def trade_offers():
    now = utcnow()
    rows = db().execute(
        'SELECT o.id AS offer_id, o.expires_at, o.offered_at, o.seen_at, j.*, '
        'c.name AS category_name, a.name AS area_name FROM offers o JOIN jobs j ON j.id = o.job_id '
        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
        "WHERE o.trade_id = ? AND o.status = 'active' AND o.expires_at > ? ORDER BY o.expires_at",
        (me()['id'], ts(now))).fetchall()
    offers = []
    for r in rows:
        item = job_json(r)
        item.update(offer_id=r['offer_id'], expires_at=r['expires_at'], offered_at=r['offered_at'],
                    seen=bool(r['seen_at']),
                    seconds_left=max(0, int((parse_ts(r['expires_at']) - now).total_seconds())))
        offers.append(item)
    return {'offers': offers, 'server_time': ts(now), 'trade': trade_status(me()['id'])}


@bp.get('/trade/jobs/<int:job_id>')
@auth('trade')
def trade_job(job_id):
    uid = me()['id']
    offer = engine.get_offer(db(), job_id, uid)
    if not offer:
        return fail('Not found. It may have closed or been removed.', 404)
    engine.mark_seen(db(), job_id, uid)
    job = engine.get_job(db(), job_id)
    quote = db().execute('SELECT * FROM quotes WHERE job_id = ? AND trade_id = ?', (job_id, uid)).fetchone()
    contact = None
    if quote and quote['status'] in ('shortlisted', 'accepted'):
        c = db().execute('SELECT name, phone, email FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
        contact = {'name': c['name'], 'phone': c['phone'], 'email': c['email']} if c else None
    now = utcnow()
    can_quote = offer['status'] == 'active' and parse_ts(offer['expires_at']) > now and job['status'] == 'open'
    templates = web._templates(uid)
    return {'job': job_json(job, include_address=bool(contact)),
            'offer': {'status': offer['status'], 'expires_at': offer['expires_at'], 'offered_at': offer['offered_at'],
                      'seconds_left': max(0, int((parse_ts(offer['expires_at']) - now).total_seconds()))},
            'quote': quote_json(quote) if quote else None,
            'can_edit_quote': bool(quote and quote['status'] == 'sent'),
            'contact': contact, 'can_quote': can_quote,
            'customer_record': engine.customer_record(db(), job['customer_id']),
            'photos': [photo_url(p['filename']) for p in web._photos(job_id)],
            'templates': [{'id': t['id'], 'name': t['name'], 'price_type': t['price_type'], 'message': t['message'],
                           'inclusions': t['inclusions'], 'exclusions': t['exclusions'],
                           'warranty': t['warranty'], 'duration': t['duration']} for t in templates],
            'contract_threshold': config.CONTRACT_THRESHOLD, 'server_time': ts(now),
            'default_report_plan': _default_plan(uid),
            'progress': progress_json(job) if job['hired_trade_id'] == uid else None}


def _default_plan(uid):
    t = db().execute('SELECT report_plan FROM trades WHERE user_id = ?', (uid,)).fetchone()
    return reporting.parse(t['report_plan']) if t else []


def _quote_fields(f):
    """The same fields the web quote form sends. engine._clean_quote does the checking."""
    q = dict(price_type=text(f, 'price_type') or None, amount_low=web._money_field(text(f, 'amount_low')),
                amount_high=web._money_field(text(f, 'amount_high')),
                gst_included=text(f, 'gst', 'incl') == 'incl' if 'gst' in f else truthy(f.get('gst_included', True)),
                message=text(f, 'message'), inclusions=text(f, 'inclusions').strip() or None,
                exclusions=text(f, 'exclusions').strip() or None, warranty=text(f, 'warranty').strip() or None,
                available_from=text(f, 'available_from').strip() or None,
                duration=text(f, 'duration').strip() or None, act_docs_promised=truthy(f.get('act_docs_promised')))
    if 'report_plan' in f:              # a list or 'daily,weekly'; engine turns it into the stored plan
        plan = f.get('report_plan')
        q['report_plan'] = plan if isinstance(plan, (list, tuple)) else str(plan or '').split(',')
    return q


@bp.post('/trade/jobs/<int:job_id>/quote')
@auth('trade')
def trade_quote(job_id):
    f = payload()
    q = _quote_fields(f)
    try:
        n = engine.submit_quote(db(), job_id, me()['id'], q)
    except RuleError as e:
        return fail(str(e))
    if truthy(f.get('save_template')) and text(f, 'template_name').strip():
        web._save_template(me()['id'], text(f, 'template_name'), q)
    return {'ok': True, 'quote_number': n, 'max_quotes': config.MAX_QUOTES,
            'message': f'Quote sent. It’s quote {n} of {config.MAX_QUOTES} for this job.'}, 201


@bp.post('/trade/jobs/<int:job_id>/quote/edit')
@auth('trade')
def trade_quote_edit(job_id):
    job = engine.get_job(db(), job_id)
    if not job or not engine.get_offer(db(), job_id, me()['id']):
        return fail('Not found. It may have closed or been removed.', 404)
    try:
        engine.revise_quote(db(), job, me()['id'], _quote_fields(payload()))
    except RuleError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Quote updated. The customer has been told.'}


@bp.post('/trade/jobs/<int:job_id>/pass')
@auth('trade')
def trade_pass(job_id):
    try:
        engine.decline_offer(db(), job_id, me()['id'])
    except RuleError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Passed. The job has gone to another trade.'}


@bp.get('/trade/quotes')
@auth('trade')
def trade_quotes():
    rows = db().execute(
        'SELECT q.*, j.title, j.suburb, j.value_band, j.quote_count, j.status AS job_status, '
        'c.name AS category_name, a.name AS area_name, '
        '(SELECT COUNT(*) FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        ' AND m.sender_id <> q.trade_id AND m.read_at IS NULL) AS unread '
        'FROM quotes q JOIN jobs j ON j.id = q.job_id JOIN categories c ON c.id = j.category_id '
        'JOIN areas a ON a.id = j.area_id WHERE q.trade_id = ? ORDER BY q.id DESC', (me()['id'],)).fetchall()
    return {'quotes': [quote_json(q) for q in rows]}


@bp.get('/trade/profile')
@auth('trade')
def trade_profile():
    uid = me()['id']
    t = db().execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
    cats = db().execute('SELECT c.name FROM categories c JOIN trade_categories tc ON tc.category_id = c.id '
                        'WHERE tc.trade_id = ? ORDER BY c.name', (uid,)).fetchall()
    areas = db().execute('SELECT a.name FROM areas a JOIN trade_areas ta ON ta.area_id = a.id '
                         'WHERE ta.trade_id = ? ORDER BY a.id', (uid,)).fetchall()
    return {'profile': {
        'business_name': t['business_name'], 'about': t['about'], 'years_trading': t['years_trading'],
        'licence_type': t['licence_type'], 'licence_number': t['licence_number'],
        'licence_checked': bool(t['licence_checked_at']), 'insurance_checked': bool(t['insurance_checked_at']),
        'nzbn_checked': bool(t['nzbn_checked_at']), 'workmanship_guarantee': t['workmanship_guarantee'],
        'categories': [c['name'] for c in cats], 'areas': [a['name'] for a in areas],
        'rating': engine.trade_rating(db(), uid), 'public_url': f'{site_url()}/pros/{uid}',
        # Saving the profile sends a trade without a plan on to the plan page, so no link once charging starts.
        'edit_url': None if config.CHARGING and not engine.is_subscribed(t) else f'{site_url()}/trade/setup',
        'report_plan': reporting.parse(t['report_plan']), 'report_record': reporting.trade_record(db(), uid)},
        'status': trade_status(uid)}


@bp.post('/trade/availability')
@auth('trade')
def trade_availability():
    choice = text(payload(), 'choice')
    uid = me()['id']
    if choice == 'on':
        engine.set_pause(db(), uid, False)
        message = 'New jobs are on.'
    else:
        days = {'1w': 7, '2w': 14, '4w': 28}.get(choice)
        if choice != 'off' and not days:
            return fail('Choose on, off, 1w, 2w or 4w.')
        engine.set_pause(db(), uid, True, ts(utcnow() + timedelta(days=days)) if days else None)
        message = 'New jobs are paused.' + (f' They’ll turn back on in {days} days.' if days else '')
    db().commit()
    return {'ok': True, 'message': message, 'status': trade_status(uid)}


# ── Progress updates ──────────────────────────────────────────────────────────

def _kinds():
    """'kinds' as repeated multipart fields, a JSON list, or a comma string."""
    if request.is_json:
        v = payload().get('kinds') or []
        return v if isinstance(v, list) else str(v).split(',')
    return ','.join(request.form.getlist('kinds')).split(',')


def _hired_job(job_id):
    job = engine.get_job(db(), job_id)
    return job if job and job['hired_trade_id'] == me()['id'] else None


@bp.post('/trade/jobs/<int:job_id>/updates')
@auth('trade')
def post_update(job_id):
    """Post a progress update (multipart: body, kinds, photos). Hired trade only."""
    job = _hired_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    names, error = web._save_photos(request.files.getlist('photos'), reporting.MAX_PHOTOS)
    if error:
        return fail(error, 400, {'photos': error})
    try:
        uid = reporting.post_update(db(), job, me()['id'], text(payload(), 'body'), _kinds(), names)
    except reporting.ReportError as e:
        return fail(str(e))
    return {'ok': True, 'id': uid, 'message': 'Update posted. The customer has been told.',
            'progress': progress_json(engine.get_job(db(), job_id))}, 201


@bp.post('/trade/jobs/<int:job_id>/start')
@auth('trade')
def set_start(job_id):
    job = _hired_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    try:
        reporting.set_start(db(), job, me()['id'], text(payload(), 'start'))
    except reporting.ReportError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Start date saved.', 'progress': progress_json(engine.get_job(db(), job_id))}


@bp.post('/trade/jobs/<int:job_id>/finish')
@auth('trade')
def trade_finish(job_id):
    job = _hired_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    try:
        reporting.finish(db(), job, me()['id'])
    except reporting.ReportError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Marked as finished. Updates are no longer due.'}


@bp.post('/customer/jobs/<int:job_id>/finish')
@auth('customer')
def customer_finish(job_id):
    job = _my_job(job_id)
    if not job:
        return fail('Not found. It may have closed or been removed.', 404)
    try:
        reporting.finish(db(), job, me()['id'])
    except reporting.ReportError as e:
        return fail(str(e))
    return {'ok': True, 'message': 'Marked as finished.'}


# ── Referrals ─────────────────────────────────────────────────────────────────

@bp.get('/trade/referrals')
@auth('trade')
def trade_referrals():
    """A trade's invite link and the free months it has earned. No prices: months, not money."""
    uid = me()['id']
    t = db().execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
    bank = referrals.banked(db(), uid)
    joined = db().execute("SELECT u.created_at, tr.business_name, "
                          "(SELECT COUNT(*) FROM quotes q WHERE q.trade_id = u.id) AS quotes "
                          "FROM users u JOIN trades tr ON tr.user_id = u.id WHERE u.referred_by = ? "
                          "ORDER BY u.id DESC", (uid,)).fetchall()
    return {'invite_url': f'{site_url()}/join/{web._invite_code(t)}',
            'months': {'waiting': bank['waiting'], 'used': bank['used'], 'earned': bank['earned'], 'cap': bank['cap']},
            'rewards': [{'business_name': r['business_name'], 'months': r['months'], 'earned_at': r['earned_at'],
                         'used': bool(r['applied_at'])} for r in bank['rows']],
            'joined': [{'business_name': j['business_name'], 'joined_at': j['created_at'], 'quoted': j['quotes'] > 0}
                       for j in joined]}


def _share_state(u):
    mine = db().execute("SELECT p.business_name, p.status, c.name AS category_name FROM prospects p "
                        "JOIN categories c ON c.id = p.category_id WHERE p.recommended_by = ? ORDER BY p.id DESC",
                        (u['id'],)).fetchall()
    friends = db().execute("SELECT COUNT(*) AS n FROM users WHERE referred_by = ? AND role = 'customer'",
                           (u['id'],)).fetchone()['n']
    return {'share_url': f'{site_url()}/r/{referrals.ref_code(db(), u)}', 'friends': friends,
            'email_on': mailer.enabled(),
            'recommended': [{'business_name': p['business_name'], 'category_name': p['category_name'],
                             'joined': p['status'] == 'signed_up'} for p in mine]}


@bp.get('/customer/share')
@auth('customer')
def customer_share():
    return _share_state(me())


@bp.post('/customer/recommend')
@auth('customer')
def customer_recommend():
    """Recommend a tradie. With email_them, we email them once on the customer's behalf."""
    f = payload()
    fields = {k: text(f, k) for k in ('name', 'business_name', 'email', 'phone', 'note', 'category_id', 'area_id')}
    u = db().execute('SELECT * FROM users WHERE id = ?', (me()['id'],)).fetchone()
    try:
        prospect, existing = referrals.recommend(db(), u, fields)
    except referrals.RecommendError as e:
        return fail(str(e))
    if existing:
        return {'ok': True, 'already_on_level': True, 'profile_url': f'{site_url()}/pros/{existing["id"]}',
                'message': 'They’re already on Level — thanks!'}
    # Only say we emailed them if email is actually set up (otherwise it's just written to the log)
    emailed = bool(truthy(f.get('email_them')) and referrals.send_invite(db(), prospect, u) and mailer.enabled())
    return {'ok': True, 'already_on_level': False, 'emailed': emailed,
            'join_url': f'{site_url()}/o/{prospect["token"]}',
            'message': (f'Thanks! We’ve emailed {prospect["business_name"]} an invite.' if emailed else
                        f'Thanks! Send {prospect["business_name"]} this link so they can join.')}, 201
