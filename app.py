"""
Level — web app.

Routing, forms and access control. The business rules live in engine.py (who
sees a job, the quote cap, redistribution, the guarantee) and billing.py
(plans and refunds).
"""
import functools
import os
import re
import json
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import timedelta, timezone

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

import accounts
import ai
import billing
import config
import engine
import integrations
import mailer
import outreach
import push
import referrals
import reporting
import sms
from db import _USE_PG, get_db, release_db
from engine import RuleError, parse_ts, ts, utcnow
from schema import hash_password, init_db

try:
    from zoneinfo import ZoneInfo
    NZ = ZoneInfo('Pacific/Auckland')
except Exception:                       # no tz database on this machine
    NZ = timezone(timedelta(hours=12))

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE, 'static', 'uploads'))
PHOTO_TYPES = {'jpg', 'jpeg', 'png', 'webp', 'heic'}
MAX_PHOTOS = 6
DEMO_TOOLS = os.environ.get('DEMO_TOOLS', '0' if _USE_PG else '1') == '1'
SWEEP_SECONDS = int(os.environ.get('SWEEP_SECONDS', 120))

LICENCES = {
    'none':      ('No licence needed for my work', None),
    'lbp':       ('Licensed Building Practitioner (LBP)', 'https://www.lbp.govt.nz/for-homeowners/find-an-lbp/'),
    'ewrb':      ('Registered electrical worker', 'https://kete.mbie.govt.nz/ew/ewprsearch/'),
    'pgdb':      ('Plumber, gasfitter or drainlayer', 'https://www.pgdb.co.nz/advice_for_consumers/search_the_register/'),
    'architect': ('Registered architect', 'https://www.nzrab.nz/'),
}

app = Flask(__name__)
_secret = os.environ.get('SECRET_KEY')
if _USE_PG and not _secret:
    raise RuntimeError('SECRET_KEY must be set in production.')
app.config.update(
    SECRET_KEY=_secret or 'dev-only-not-secret',
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_USE_PG,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
os.makedirs(UPLOAD_DIR, exist_ok=True)
init_db()


# ── Database per request ──────────────────────────────────────────────────────

def db():
    if 'db' not in g:
        g.db = get_db()
    return g.db


@app.teardown_appcontext
def _teardown(exc):
    conn = g.pop('db', None)
    if conn is not None and not _USE_PG:
        conn.close()
    release_db(exc)


def _load_clock(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'clock_offset_hours'").fetchone()
    engine.set_clock_offset(row['value'] if row else 0)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Content-Security-Policy',
                            "default-src 'self'; img-src 'self' data:; "
                            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; "
                            "worker-src 'self'; manifest-src 'self'; "
                            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'")
    if _USE_PG:                                  # production sits behind HTTPS
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp


@app.route('/health')
def health():
    """Used by the host to check the site is alive."""
    for attempt in (1, 2):                      # one retry, in case a pooled connection just died
        try:
            db().execute('SELECT 1 FROM users LIMIT 1').fetchone()
            return {'status': 'ok', 'database': 'postgres' if _USE_PG else 'sqlite',
                    'free_pilot': config.FREE_PILOT, 'charging': config.CHARGING, 'email': mailer.enabled(), 'texts': sms.enabled(), 'ai': ai.enabled(),
                    'version': os.environ.get('RENDER_GIT_COMMIT', 'local')[:7]}, 200
        except Exception as e:
            app.logger.warning('Health check attempt %s failed: %s', attempt, e)
            release_db()                        # drop it and take a fresh one
            g.pop('db', None)
            if attempt == 2:
                return {'status': 'error', 'detail': str(e)[:200]}, 500


@app.route('/manifest.json')
def manifest():
    """Tells a phone how to install Level to the home screen."""
    return send_from_directory(os.path.join(BASE, 'static'), 'manifest.json',
                               mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(os.path.join(BASE, 'static'), 'sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'        # must be served from the root to cover the whole site
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/offline')
def offline():
    return render_template('offline.html')


@app.route('/robots.txt')
def robots():
    body = '\n'.join(['User-agent: *'] + [f'Disallow: {p}' for p in
                     ('/me', '/trade', '/admin', '/thread', '/settings', '/uploads', '/demo', '/o/')] + ['Allow: /', ''])
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}


# ── Sessions, CSRF, auth ──────────────────────────────────────────────────────

def _csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_urlsafe(32)
    return session['_csrf']


@app.before_request
def _before():
    if request.endpoint == 'static':
        return
    if DEMO_TOOLS:
        _load_clock(db())
    integrations.refresh(db())
    # The phone app's API authenticates with a bearer token, never the session cookie, so CSRF doesn't apply.
    if request.method == 'POST' and request.endpoint != 'stripe_webhook' and not request.path.startswith('/api/mobile/'):
        sent = request.form.get('_csrf', '')
        if not sent or not secrets.compare_digest(sent, session.get('_csrf', '')):
            abort(400)


def current_user():
    if 'user' not in g:
        uid = session.get('uid')
        g.user = db().execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone() if uid else None
    return g.user


def current_trade():
    u = current_user()
    if not u or u['role'] != 'trade':
        return None
    if 'trade' not in g:
        g.trade = db().execute('SELECT * FROM trades WHERE user_id = ?', (u['id'],)).fetchone()
    return g.trade


def home_for(u):
    return url_for({'customer': 'customer_home', 'trade': 'trade_home', 'admin': 'admin_home'}[u['role']])


def requires(role):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            u = current_user()
            if not u:
                return redirect(url_for('login', next=request.full_path))
            if u['role'] != role:
                abort(403)
            return fn(*args, **kwargs)
        return inner
    return deco


def login_user(uid):
    session.clear()
    session['uid'] = uid
    session.permanent = True
    db().execute('UPDATE users SET last_login_at = ? WHERE id = ?', (ts(utcnow()), uid))
    db().commit()


def _safe_next():
    nxt = request.args.get('next') or request.form.get('next') or ''
    return nxt if nxt.startswith('/') and not nxt.startswith('//') else None


_login_failures = defaultdict(list)


def _too_many_attempts(ip):
    now = time.time()
    _login_failures[ip] = [t for t in _login_failures[ip] if now - t < 900]
    return len(_login_failures[ip]) >= 10


# ── Template helpers ──────────────────────────────────────────────────────────

def _nz(value):
    d = parse_ts(value)
    return d.replace(tzinfo=timezone.utc).astimezone(NZ) if d else None


@app.template_filter('money')
def money(v):
    return '' if v is None else f'${v:,.0f}'


@app.template_filter('day')
def day(v):
    if v and len(str(v)) == 10:             # a plain date, e.g. an insurance expiry
        from datetime import datetime
        d = datetime.strptime(str(v), '%Y-%m-%d')
    else:
        d = _nz(v)
    return f'{d.day} {d:%b %Y}' if d else ''


@app.template_filter('when')
def when(v):
    d = _nz(v)
    if not d:
        return ''
    return f'{d.day} {d:%b}, {d.hour % 12 or 12}:{d:%M} {"am" if d.hour < 12 else "pm"}'


@app.template_filter('ago')
def ago(v):
    d = parse_ts(v)
    if not d:
        return ''
    s = (utcnow() - d).total_seconds()
    if s < 60:
        return 'just now'
    if s < 3600:
        return f'{int(s // 60)} min ago'
    if s < 86400:
        return f'{int(s // 3600)} h ago'
    return f'{int(s // 86400)} d ago'


@app.template_filter('secs_left')
def secs_left(v):
    d = parse_ts(v)
    return max(0, int((d - utcnow()).total_seconds())) if d else 0


@app.template_filter('price')
def price(q):
    if q['price_type'] == 'site_visit':
        return 'Site visit first'
    if q['price_type'] == 'range':
        return f'{money(q["amount_low"])} – {money(q["amount_high"])}'
    return money(q['amount_low'])


@app.context_processor
def _globals():
    u = current_user()
    unread = unread_msgs = leads_waiting = 0
    if u:
        unread = db().execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                              (u['id'],)).fetchone()['n']
        if u['role'] == 'customer':
            unread_msgs = db().execute('SELECT COUNT(*) AS n FROM messages m JOIN jobs j ON j.id = m.job_id '
                                       'WHERE j.customer_id = ? AND m.sender_id <> ? AND m.read_at IS NULL',
                                       (u['id'], u['id'])).fetchone()['n']
        elif u['role'] == 'trade':
            unread_msgs = db().execute('SELECT COUNT(*) AS n FROM messages WHERE trade_id = ? AND sender_id <> ? '
                                       'AND read_at IS NULL', (u['id'], u['id'])).fetchone()['n']
            leads_waiting = db().execute("SELECT COUNT(*) AS n FROM offers WHERE trade_id = ? AND status = 'active' "
                                         'AND expires_at > ?', (u['id'], ts(utcnow()))).fetchone()['n']
    return dict(cfg=config, me=u, my_trade=current_trade(), unread=unread, unread_msgs=unread_msgs,
                leads_waiting=leads_waiting, csrf_token=_csrf_token, demo_tools=DEMO_TOOLS, licences=LICENCES,
                bands=config.VALUE_BANDS, tiers=config.TIERS)


def all_categories():
    return db().execute('SELECT * FROM categories ORDER BY name').fetchall()


def areas_by_region():
    grouped = {}
    for r in db().execute('SELECT * FROM areas ORDER BY id').fetchall():
        grouped.setdefault(r['region'], []).append(r)
    return grouped


def clean_username(value):
    """Usernames are stored lower case, so 'Admin 2' and 'admin 2' are the same
    login. Returns (username or None, error or None)."""
    name = re.sub(r'\s+', ' ', (value or '').strip()).lower()
    if not name:
        return None, None
    if not re.fullmatch(r'[a-z0-9 ._-]{3,30}', name):
        return None, ('Usernames are 3 to 30 characters, using letters, numbers, spaces, '
                      'dots, dashes or underscores.')
    return name, None


def username_taken(name, user_id=None):
    return bool(db().execute('SELECT 1 FROM users WHERE (username = ? OR email = ?) AND id <> ?',
                             (name, name, user_id or 0)).fetchone())


def valid_nzbn(value):
    d = re.sub(r'\D', '', value or '')
    if len(d) != 13 or not d.startswith('94'):
        return False
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d[:12]))
    return (10 - total % 10) % 10 == int(d[12])


def _photos(job_id):
    return db().execute('SELECT * FROM job_photos WHERE job_id = ? ORDER BY id', (job_id,)).fetchall()


@app.route('/uploads/<path:name>')
def upload(name):
    return send_from_directory(UPLOAD_DIR, name)


# ── Public pages ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', categories=all_categories())


@app.route('/pricing')
def pricing():
    return render_template('pricing.html')


@app.route('/terms')
def terms():
    return render_template('legal.html', page='terms')


@app.route('/privacy')
def privacy():
    return render_template('legal.html', page='privacy')


@app.route('/pros/<int:trade_id>')
def pro_profile(trade_id):
    t = db().execute('SELECT t.*, u.name, u.created_at AS joined FROM trades t JOIN users u ON u.id = t.user_id '
                     'WHERE t.user_id = ?', (trade_id,)).fetchone()
    if not t:
        abort(404)
    cats = db().execute('SELECT c.name FROM categories c JOIN trade_categories tc ON tc.category_id = c.id '
                        'WHERE tc.trade_id = ? ORDER BY c.name', (trade_id,)).fetchall()
    areas = db().execute('SELECT a.name FROM areas a JOIN trade_areas ta ON ta.area_id = a.id '
                         'WHERE ta.trade_id = ? ORDER BY a.id', (trade_id,)).fetchall()
    reviews = db().execute('SELECT r.*, j.title, u.name AS customer_name FROM reviews r JOIN jobs j ON j.id = r.job_id '
                           'JOIN users u ON u.id = r.customer_id WHERE r.trade_id = ? ORDER BY r.id DESC',
                           (trade_id,)).fetchall()
    done = db().execute("SELECT COUNT(*) AS n FROM jobs WHERE hired_trade_id = ? AND status = 'hired'",
                        (trade_id,)).fetchone()['n']
    return render_template('profile.html', t=t, cats=cats, areas=areas, reviews=reviews,
                           rating=engine.trade_rating(db(), trade_id), done=done,
                           report_record=reporting.trade_record(db(), trade_id),
                           report_plan=reporting.describe(t['report_plan']))


# ── Accounts ──────────────────────────────────────────────────────────────────

def validate_account(f):
    errors = {}
    if len(f.get('name', '').strip()) < 2:
        errors['name'] = 'Enter your name.'
    email = f.get('email', '').strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        errors['email'] = 'Enter a valid email address.'
    elif db().execute('SELECT 1 FROM users WHERE email = ?', (email,)).fetchone():
        errors['email'] = 'There’s already an account with this email. Log in instead.'
    if len(f.get('password', '')) < 8:
        errors['password'] = 'Use at least 8 characters.'
    if len(re.sub(r'\D', '', f.get('phone', ''))) < 8:
        errors['phone'] = 'Enter a phone number, e.g. 021 123 4567.'
    return errors


def create_user(role, f):
    cur = db().execute('INSERT INTO users (role, email, password_hash, name, phone, created_at) VALUES (?,?,?,?,?,?)',
                       (role, f['email'].strip().lower(), hash_password(f['password']), f['name'].strip(),
                        f['phone'].strip(), ts(utcnow())))
    return cur.lastrowid


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    role = request.values.get('role', 'trade')
    role = role if role in ('trade', 'customer') else 'trade'
    if role == 'customer' and request.method == 'GET':
        return redirect(url_for('post_job'))
    errors = {}
    inviter = _inviter()
    if request.method == 'POST':
        errors = validate_account(request.form)
        business = request.form.get('business_name', '').strip()
        if len(business) < 2:
            errors['business_name'] = 'Enter your business or trading name.'
        if not errors:
            try:
                uid = create_user('trade', request.form)
            except sqlite3.IntegrityError:
                errors['email'] = 'There’s already an account with this email. Log in instead.'
            else:
                db().execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                             (uid, business, ts(utcnow())))
                if inviter:
                    db().execute('UPDATE users SET referred_by = ? WHERE id = ?', (inviter['user_id'], uid))
                    engine.notify(db(), inviter['user_id'], f'{business} joined {config.BRAND} from your invite. Cheers!',
                                  url_for('trade_home'))
                db().commit()
                prospect = outreach.by_token(db(), session.get('prospect'))
                login_user(uid)
                if prospect:
                    # They came from an outreach email: credit it, and pre-tick their trade and areas.
                    outreach.signed_up(db(), prospect, uid)
                    referrals.recommended_joined(db(), prospect, uid)
                    session['setup_hint'] = outreach.setup_hint(db(), prospect)
                accounts.welcome(db(), db().execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone(),
                                 request.url_root.rstrip('/'))
                return redirect(url_for('trade_setup'))
    form = request.form
    prospect = outreach.by_token(db(), session.get('prospect')) if request.method == 'GET' else None
    if prospect:
        form = {'business_name': prospect['business_name'], 'email': prospect['email'] or '',
                'name': prospect['first_name'] or '', 'phone': prospect['phone'] or ''}
    return render_template('signup.html', errors=errors, form=form, inviter=inviter)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
        if _too_many_attempts(ip):
            error = 'Too many attempts. Wait 15 minutes, then try again.'
        else:
            # People can sign in with their email or their username.
            ident = re.sub(r'\s+', ' ', request.form.get('email', '').strip()).lower()
            u = db().execute('SELECT * FROM users WHERE email = ? OR username = ?', (ident, ident)).fetchone()
            if u and u['closed_at']:
                error = 'That account has been closed. Sign up again if you’d like to come back.'
            elif u and check_password_hash(u['password_hash'], request.form.get('password', '')):
                login_user(u['id'])
                return redirect(_safe_next() or home_for(u))
            else:
                _login_failures[ip].append(time.time())
                error = 'That email and password don’t match.'
    return render_template('login.html', error=error)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/demo/login/<role>')
def demo_login(role):
    """Local demo only: open the site as one of the seeded accounts."""
    if not DEMO_TOOLS:
        abort(404)
    email = {'customer': 'customer@level.local', 'trade': 'trade@level.local',
             'admin': os.environ.get('ADMIN_EMAIL', 'admin@level.local')}.get(role)
    u = db().execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone() if email else None
    if not u:
        flash('Load the demo data from the admin page first.', 'error')
        return redirect(url_for('login'))
    login_user(u['id'])
    return redirect(_safe_next() or home_for(u))


@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    """Sends a reset link. Always says the same thing, so nobody can use this
    page to find out which email addresses have accounts."""
    sent = False
    if request.method == 'POST':
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
        email = request.form.get('email', '').strip().lower()
        u = db().execute('SELECT * FROM users WHERE email = ? AND closed_at IS NULL', (email,)).fetchone()
        if u and not _too_many_attempts(ip):
            link = accounts.send_reset(db(), u, request.url_root.rstrip('/'))
            if not mailer.enabled():
                app.logger.warning('Email is not set up. Reset link for %s: %s', email, link)
        _login_failures[ip].append(time.time())
        sent = True
    return render_template('forgot.html', sent=sent)


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset(token):
    row = accounts.find(db(), token, 'reset')
    if not row:
        return render_template('reset.html', expired=True)
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if len(password) < 8:
            error = 'Use at least 8 characters.'
        else:
            db().execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(password), row['user_id']))
            accounts.spend(db(), row['id'])
            db().commit()
            login_user(row['user_id'])
            flash('Password changed, and you’re logged in.')
            return redirect(home_for(db().execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone()))
    return render_template('reset.html', expired=False, error=error)


@app.route('/verify/<token>')
def verify_email(token):
    row = accounts.find(db(), token, 'verify')
    if not row:
        flash('That confirmation link has run out. Send yourself a new one from your settings.', 'error')
        return redirect(url_for('settings') if current_user() else url_for('login'))
    db().execute('UPDATE users SET email_verified_at = ? WHERE id = ?', (ts(utcnow()), row['user_id']))
    accounts.spend(db(), row['id'])
    db().commit()
    if not current_user():
        login_user(row['user_id'])
    flash('Email confirmed — thanks.')
    return redirect(home_for(db().execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone()))


@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    u = db().execute('SELECT * FROM users WHERE unsub_token = ?', (token,)).fetchone()
    if u:
        db().execute('UPDATE users SET email_alerts = 0 WHERE id = ?', (u['id'],))
        db().commit()
        return render_template('error.html', title='Emails turned off',
                               body='You won’t get any more job or quote emails from us. '
                                    'Turn them back on any time in your settings.')
    return render_template('error.html', title='Link not recognised',
                           body='That unsubscribe link is no longer valid. You can turn emails off in your settings.')


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    u = current_user()
    if not u:
        return redirect(url_for('login', next='/settings'))
    errors, f = {}, request.form
    if request.method == 'POST':
        action = f.get('action')
        needs_password = action in ('email', 'password', 'close')
        if needs_password and not check_password_hash(u['password_hash'], f.get('current_password', '')):
            errors['current_password'] = 'That password isn’t right.'

        if action == 'details' and not errors:
            name, phone = f.get('name', '').strip(), f.get('phone', '').strip()
            username, username_error = clean_username(f.get('username'))
            if len(name) < 2:
                errors['name'] = 'Enter your name.'
            if len(re.sub(r'\D', '', phone)) < 8:
                errors['phone'] = 'Enter a phone number.'
            if username_error:
                errors['username'] = username_error
            elif username and username_taken(username, u['id']):
                errors['username'] = 'Someone already uses that username.'
            if not errors:
                db().execute('UPDATE users SET name = ?, phone = ?, username = ?, email_alerts = ?, text_alerts = ?, '
                             'phone_verified_at = CASE WHEN phone = ? THEN phone_verified_at END WHERE id = ?',
                             (name, phone, username, 1 if f.get('email_alerts') else 0,
                              1 if f.get('text_alerts') else 0, phone, u['id']))
                db().commit()
                flash('Saved.')
                return redirect(url_for('settings'))

        elif action == 'email' and not errors:
            email = f.get('email', '').strip().lower()
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
                errors['email'] = 'Enter a valid email address.'
            elif db().execute('SELECT 1 FROM users WHERE email = ? AND id <> ?', (email, u['id'])).fetchone():
                errors['email'] = 'Another account already uses that email.'
            if not errors:
                db().execute('UPDATE users SET email = ?, email_verified_at = NULL WHERE id = ?', (email, u['id']))
                db().commit()
                accounts.send_verify(db(), db().execute('SELECT * FROM users WHERE id = ?', (u['id'],)).fetchone(),
                                     request.url_root.rstrip('/'))
                flash('Email changed. Check the new address for a confirmation link.')
                return redirect(url_for('settings'))

        elif action == 'password' and not errors:
            if len(f.get('password', '')) < 8:
                errors['password'] = 'Use at least 8 characters.'
            else:
                db().execute('UPDATE users SET password_hash = ? WHERE id = ?',
                             (hash_password(f['password']), u['id']))
                db().commit()
                flash('Password changed.')
                return redirect(url_for('settings'))

        elif action == 'verify':
            accounts.send_verify(db(), u, request.url_root.rstrip('/'))
            flash('Confirmation email sent.' if mailer.enabled() else
                  'Email isn’t set up yet, so the link was written to the server log.')
            return redirect(url_for('settings'))

        elif action == 'close':
            if f.get('confirm', '').strip().upper() != 'CLOSE':
                errors['confirm'] = 'Type CLOSE to confirm.'
            if not errors:
                accounts.close(db(), u)
                session.clear()
                flash('Your account is closed. Thanks for giving it a go.')
                return redirect(url_for('index'))
    return render_template('settings.html', errors=errors, form=f)


@app.route('/notifications')
def notifications():
    u = current_user()
    if not u:
        return redirect(url_for('login', next=request.full_path))
    rows = db().execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 100',
                        (u['id'],)).fetchall()
    return render_template('notifications.html', rows=rows)


@app.route('/n/<int:nid>')
def open_notification(nid):
    u = current_user()
    if not u:
        return redirect(url_for('login'))
    n = db().execute('SELECT * FROM notifications WHERE id = ? AND user_id = ?', (nid, u['id'])).fetchone()
    if not n:
        abort(404)
    db().execute('UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL', (ts(utcnow()), nid))
    db().commit()
    return redirect(n['link'] or url_for('notifications'))


@app.post('/notifications/read')
def read_all():
    u = current_user()
    if u:
        db().execute('UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL', (ts(utcnow()), u['id']))
        db().commit()
    return redirect(url_for('notifications'))


# ── Posting a job ─────────────────────────────────────────────────────────────

@app.route('/post', methods=['GET', 'POST'])
def post_job():
    u = current_user()
    if u and u['role'] != 'customer':
        flash('Trade and admin accounts can’t post jobs. Log out and post with a customer account.', 'error')
        return redirect(home_for(u))
    form = request.form if request.method == 'POST' else {'category': request.args.get('category', '')}
    errors = {}
    if request.method == 'POST':
        f = request.form
        cat = db().execute('SELECT * FROM categories WHERE slug = ?', (f.get('category'),)).fetchone()
        area = db().execute('SELECT * FROM areas WHERE slug = ?', (f.get('area'),)).fetchone()
        if not cat:
            errors['category'] = 'Choose the kind of trade you need.'
        if not area:
            errors['area'] = 'Choose where the job is.'
        if len(f.get('suburb', '').strip()) < 2:
            errors['suburb'] = 'Enter the suburb. Trades see this, not your address.'
        if not 5 <= len(f.get('title', '').strip()) <= 80:
            errors['title'] = 'Give the job a short title, 5 to 80 characters.'
        if len(f.get('description', '').strip()) < 30:
            errors['description'] = 'Describe the job in a few sentences so trades can quote accurately.'
        if f.get('value_band') not in config.VALUE_BANDS:
            errors['value_band'] = 'Pick a rough budget. Your best guess is fine.'
        if f.get('timing') not in config.TIMING:
            errors['timing'] = 'Choose when you’d like it done.'
        if f.get('property_type') not in config.PROPERTY_TYPES:
            errors['property_type'] = 'Choose the type of property.'
        photos = [p for p in request.files.getlist('photos') if p and p.filename]
        if len(photos) > MAX_PHOTOS:
            errors['photos'] = f'Add up to {MAX_PHOTOS} photos.'
        if any(p.filename.rsplit('.', 1)[-1].lower() not in PHOTO_TYPES for p in photos):
            errors['photos'] = 'Photos must be JPG, PNG, WEBP or HEIC.'
        if not u:
            errors.update(validate_account(f))
        if not errors:
            if not u:
                uid = create_user('customer', f)
                db().commit()
                sharer = referrals.referrer_for_code(db(), session.get('ref'))
                login_user(uid)
                if sharer:
                    referrals.customer_joined(db(), uid, sharer['id'])
                accounts.welcome(db(), db().execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone(),
                                 request.url_root.rstrip('/'))
            poster = db().execute('SELECT * FROM users WHERE id = ?', (session['uid'],)).fetchone()
            # With texts switched on, a job only goes out once the phone number is confirmed —
            # that's what stops fake and dead jobs reaching tradies.
            hold = sms.enabled() and not poster['phone_verified_at']
            job_id, offered = engine.post_job(db(), session['uid'], dict(
                category_id=cat['id'], area_id=area['id'], suburb=f['suburb'].strip(),
                address=f.get('address', '').strip() or None, title=f['title'].strip(),
                description=f['description'].strip(), value_band=f['value_band'], timing=f['timing'],
                property_type=f['property_type']), hold=hold)
            for p in photos:
                name = f'{uuid.uuid4().hex}.{p.filename.rsplit(".", 1)[-1].lower()}'
                p.save(os.path.join(UPLOAD_DIR, name))
                db().execute('INSERT INTO job_photos (job_id, filename, created_at) VALUES (?,?,?)',
                             (job_id, name, ts(utcnow())))
            db().commit()
            if hold:
                accounts.send_phone_code(db(), poster)
                return redirect(url_for('verify_phone', next=url_for('customer_job', job_id=job_id)))
            if offered:
                flash(f'Job posted. {offered} local {cat["name"].lower()} trades can see it now.')
            else:
                flash('Job posted. No trades cover this area yet — we’ll offer it the moment one joins.')
            return redirect(url_for('customer_job', job_id=job_id))
    return render_template('post.html', form=form, errors=errors, categories=all_categories(), ai_on=ai.enabled(),
                           regions=areas_by_region())


# ── Customer area ─────────────────────────────────────────────────────────────

@app.route('/me')
@requires('customer')
def customer_home():
    jobs = db().execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
                        'WHERE j.customer_id = ? ORDER BY j.id DESC', (current_user()['id'],)).fetchall()
    return render_template('customer/dashboard.html', jobs=jobs)


def _my_job(job_id):
    job = engine.get_job(db(), job_id)
    if not job or job['customer_id'] != current_user()['id']:
        abort(404)
    return job


def offer_stats(job_id):
    counts = {r['status']: r['n'] for r in db().execute(
        'SELECT status, COUNT(*) AS n FROM offers WHERE job_id = ? GROUP BY status', (job_id,)).fetchall()}
    nxt = db().execute("SELECT MIN(expires_at) AS e FROM offers WHERE job_id = ? AND status = 'active'",
                       (job_id,)).fetchone()['e']
    return dict(counts=counts, total=sum(counts.values()), active=counts.get('active', 0),
                quoted=counts.get('quoted', 0), passed=counts.get('expired', 0) + counts.get('declined', 0),
                next_expiry=nxt)


def quotes_for_job(job):
    rows = db().execute(
        'SELECT q.*, t.business_name, t.licence_type, t.licence_number, t.licence_checked_at, '
        't.insurance_insurer, t.insurance_expiry, t.insurance_checked_at, t.nzbn, t.nzbn_checked_at, '
        't.years_trading, t.workmanship_guarantee, u.phone, u.email, u.name AS contact_name, '
        '(SELECT COUNT(*) FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id) AS msg_count '
        'FROM quotes q JOIN trades t ON t.user_id = q.trade_id JOIN users u ON u.id = q.trade_id '
        'WHERE q.job_id = ? ORDER BY q.created_at, q.id', (job['id'],)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['rating'] = engine.trade_rating(db(), r['trade_id'])
        d['needs_act'] = engine.needs_act_docs(job, d)
        d['report_record'] = reporting.trade_record(db(), r['trade_id'])
        out.append(d)
    return out


@app.route('/me/jobs/<int:job_id>')
@requires('customer')
def customer_job(job_id):
    job = _my_job(job_id)
    reviewed = db().execute('SELECT 1 FROM reviews WHERE job_id = ?', (job_id,)).fetchone()
    return render_template('customer/job.html', job=job, quotes=quotes_for_job(job), stats=offer_stats(job_id),
                           photos=_photos(job_id), reviewed=bool(reviewed), **_progress(job))


@app.post('/me/jobs/<int:job_id>/quotes/<int:quote_id>/<action>')
@requires('customer')
def quote_action(job_id, quote_id, action):
    job = _my_job(job_id)
    try:
        if action == 'share':
            engine.share_contact(db(), job, quote_id)
            flash('Your contact details are now visible to this trade.')
        elif action == 'accept':
            engine.accept_quote(db(), job, quote_id, act_ack=bool(request.form.get('act_ack')))
            flash('Quote accepted. The trade has your contact details and the other trades have been told.')
        elif action == 'decline':
            engine.decline_quote(db(), job, quote_id)
            flash('Quote declined. The trade has been told.')
        elif action == 'flag':
            engine.flag_quote(db(), job, quote_id, request.form.get('reason') or 'Not a genuine quote')
            flash('Thanks. We’ll review this quote.')
        else:
            abort(404)
    except RuleError as e:
        flash(str(e), 'error')
    return redirect(url_for('customer_job', job_id=job_id) + f'#q{quote_id}')


@app.post('/me/jobs/<int:job_id>/close')
@requires('customer')
def close_job(job_id):
    job = _my_job(job_id)
    try:
        engine.close_job(db(), job, request.form.get('outcome', 'not_going_ahead'))
        flash('Job closed. Trades waiting on it have been told.')
    except RuleError as e:
        flash(str(e), 'error')
    return redirect(url_for('customer_job', job_id=job_id))


@app.route('/me/jobs/<int:job_id>/edit', methods=['GET', 'POST'])
@requires('customer')
def edit_job(job_id):
    job = _my_job(job_id)
    if job['status'] not in ('open', 'full'):
        flash('Closed jobs can’t be edited, but you can post the job again.', 'error')
        return redirect(url_for('customer_job', job_id=job_id))
    offered = db().execute('SELECT COUNT(*) AS n FROM offers WHERE job_id = ?', (job_id,)).fetchone()['n']
    errors = {}
    if request.method == 'POST':
        f = request.form
        photos = [p for p in request.files.getlist('photos') if p and p.filename]
        if not 5 <= len(f.get('title', '').strip()) <= 80:
            errors['title'] = 'Give the job a short title, 5 to 80 characters.'
        if len(f.get('description', '').strip()) < 30:
            errors['description'] = 'Describe the job in a few sentences.'
        if f.get('timing') not in config.TIMING:
            errors['timing'] = 'Choose when you’d like it done.'
        if len(f.get('suburb', '').strip()) < 2:
            errors['suburb'] = 'Enter the suburb.'
        if len(_photos(job_id)) + len(photos) > MAX_PHOTOS:
            errors['photos'] = f'Up to {MAX_PHOTOS} photos in total.'
        elif any(p.filename.rsplit('.', 1)[-1].lower() not in PHOTO_TYPES for p in photos):
            errors['photos'] = 'Photos must be JPG, PNG, WEBP or HEIC.'
        if not offered and f.get('value_band') not in config.VALUE_BANDS:
            errors['value_band'] = 'Pick a rough budget.'
        if not errors:
            db().execute('UPDATE jobs SET title = ?, description = ?, timing = ?, suburb = ?, address = ? WHERE id = ?',
                         (f['title'].strip(), f['description'].strip(), f['timing'], f['suburb'].strip(),
                          f.get('address', '').strip() or None, job_id))
            if not offered:      # the budget decides who sees it, so it locks once trades have it
                db().execute('UPDATE jobs SET value_band = ? WHERE id = ?', (f['value_band'], job_id))
            for p in photos:
                name = f'{uuid.uuid4().hex}.{p.filename.rsplit(".", 1)[-1].lower()}'
                p.save(os.path.join(UPLOAD_DIR, name))
                db().execute('INSERT INTO job_photos (job_id, filename, created_at) VALUES (?,?,?)',
                             (job_id, name, ts(utcnow())))
            for r in db().execute("SELECT trade_id FROM offers WHERE job_id = ? AND status IN ('active','quoted')",
                                  (job_id,)).fetchall():
                engine.notify(db(), r['trade_id'], f'The customer updated “{f["title"].strip()}”.',
                              url_for('trade_job', job_id=job_id))
            db().commit()
            flash('Job updated. Trades looking at it have been told.')
            return redirect(url_for('customer_job', job_id=job_id))
    return render_template('customer/edit_job.html', job=job, errors=errors, form=request.form or job,
                           photos=_photos(job_id), locked=offered > 0)


@app.post('/me/jobs/<int:job_id>/photos/<int:photo_id>/delete')
@requires('customer')
def delete_photo(job_id, photo_id):
    _my_job(job_id)
    row = db().execute('SELECT * FROM job_photos WHERE id = ? AND job_id = ?', (photo_id, job_id)).fetchone()
    if row:
        db().execute('DELETE FROM job_photos WHERE id = ?', (photo_id,))
        db().commit()
        if not db().execute('SELECT 1 FROM job_photos WHERE filename = ?', (row['filename'],)).fetchone():
            try:
                os.remove(os.path.join(UPLOAD_DIR, row['filename']))
            except OSError:
                pass
    return redirect(url_for('edit_job', job_id=job_id))


@app.post('/me/jobs/<int:job_id>/repost')
@requires('customer')
def repost_job(job_id):
    job = _my_job(job_id)
    if job['status'] in ('open', 'full'):
        flash('That job is still taking quotes.', 'error')
        return redirect(url_for('customer_job', job_id=job_id))
    new_id, offered = engine.post_job(db(), job['customer_id'], dict(
        category_id=job['category_id'], area_id=job['area_id'], suburb=job['suburb'], address=job['address'],
        title=job['title'], description=job['description'], value_band=job['value_band'],
        timing=job['timing'], property_type=job['property_type']))
    for p in _photos(job_id):
        db().execute('INSERT INTO job_photos (job_id, filename, created_at) VALUES (?,?,?)',
                     (new_id, p['filename'], ts(utcnow())))
    db().commit()
    flash(f'Posted again — {offered} local trades can see it now.' if offered else
          'Posted again. No matching trades right now, so we’ll offer it as they join.')
    return redirect(url_for('customer_job', job_id=new_id))


REVIEW_PARTS = [('workmanship', 'Quality of work'), ('communication', 'Communication'),
                ('timeliness', 'Turned up and finished on time'), ('value_for_money', 'Value for money')]


@app.route('/me/jobs/<int:job_id>/review', methods=['GET', 'POST'])
@requires('customer')
def review(job_id):
    job = _my_job(job_id)
    if job['status'] != 'hired':
        abort(404)
    trade = db().execute('SELECT * FROM trades WHERE user_id = ?', (job['hired_trade_id'],)).fetchone()
    if db().execute('SELECT 1 FROM reviews WHERE job_id = ?', (job_id,)).fetchone():
        flash('You’ve already reviewed this job.')
        return redirect(url_for('customer_job', job_id=job_id))
    errors = {}
    if request.method == 'POST':
        scores = {}
        for key, _ in REVIEW_PARTS:
            v = request.form.get(key, '')
            if v not in ('1', '2', '3', '4', '5'):
                errors[key] = 'Choose 1 to 5 stars.'
            else:
                scores[key] = int(v)
        if not errors:
            db().execute('INSERT INTO reviews (job_id, trade_id, customer_id, rating, workmanship, communication, '
                         'timeliness, value_for_money, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (job_id, trade['user_id'], current_user()['id'], sum(scores.values()) / 4,
                          scores['workmanship'], scores['communication'], scores['timeliness'],
                          scores['value_for_money'], request.form.get('body', '').strip(), ts(utcnow())))
            engine.notify(db(), trade['user_id'], f'You have a new review for “{job["title"]}”.',
                          url_for('pro_profile', trade_id=trade['user_id']))
            db().commit()
            flash('Review posted. Thanks for helping other Kiwis choose.')
            return redirect(url_for('customer_job', job_id=job_id))
    return render_template('customer/review.html', job=job, trade=trade, parts=REVIEW_PARTS, errors=errors)


# ── Messages (customer ↔ trade, per job) ──────────────────────────────────────

@app.route('/messages')
def messages():
    """Every conversation this person is part of, newest first."""
    u = current_user()
    if not u:
        return redirect(url_for('login', next='/messages'))
    if u['role'] == 'admin':
        abort(404)
    whose = 'j.customer_id = ?' if u['role'] == 'customer' else 'q.trade_id = ?'
    threads = db().execute(
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
    return render_template('messages.html', threads=threads)


@app.route('/thread/<int:job_id>/<int:trade_id>', methods=['GET', 'POST'])
def thread(job_id, trade_id):
    u = current_user()
    if not u:
        return redirect(url_for('login', next=request.full_path))
    job = engine.get_job(db(), job_id)
    quote = db().execute('SELECT * FROM quotes WHERE job_id = ? AND trade_id = ?', (job_id, trade_id)).fetchone()
    if not job or not quote or u['id'] not in (job['customer_id'], trade_id):
        abort(404)
    other = trade_id if u['id'] == job['customer_id'] else job['customer_id']
    if request.method == 'POST':
        body = request.form.get('body', '').strip()
        if body:
            db().execute('INSERT INTO messages (job_id, trade_id, sender_id, body, created_at) VALUES (?,?,?,?,?)',
                         (job_id, trade_id, u['id'], body[:4000], ts(utcnow())))
            engine.notify(db(), other, f'New message about “{job["title"]}”.',
                          url_for('thread', job_id=job_id, trade_id=trade_id))
            db().commit()
        return redirect(url_for('thread', job_id=job_id, trade_id=trade_id) + '#end')
    db().execute('UPDATE messages SET read_at = ? WHERE job_id = ? AND trade_id = ? AND sender_id <> ? AND read_at IS NULL',
                 (ts(utcnow()), job_id, trade_id, u['id']))
    db().commit()
    msgs = db().execute('SELECT * FROM messages WHERE job_id = ? AND trade_id = ? ORDER BY id',
                        (job_id, trade_id)).fetchall()
    trade = db().execute('SELECT business_name FROM trades WHERE user_id = ?', (trade_id,)).fetchone()
    customer = db().execute('SELECT name FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
    return render_template('thread.html', job=job, quote=quote, msgs=msgs, trade=trade, customer=customer,
                           trade_id=trade_id)


# ── Trade area ────────────────────────────────────────────────────────────────

def setup_complete(trade_id):
    c = db().execute('SELECT COUNT(*) AS n FROM trade_categories WHERE trade_id = ?', (trade_id,)).fetchone()['n']
    a = db().execute('SELECT COUNT(*) AS n FROM trade_areas WHERE trade_id = ?', (trade_id,)).fetchone()['n']
    return c > 0 and a > 0


@app.route('/trade')
@requires('trade')
def trade_home():
    t = current_trade()
    if not setup_complete(t['user_id']):
        return redirect(url_for('trade_setup'))
    now = utcnow()
    leads = db().execute(
        'SELECT o.*, j.title, j.value_band, j.suburb, j.quote_count, j.description, j.timing, '
        'c.name AS category_name, a.name AS area_name FROM offers o JOIN jobs j ON j.id = o.job_id '
        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
        "WHERE o.trade_id = ? AND o.status = 'active' AND o.expires_at > ? ORDER BY o.expires_at",
        (t['user_id'], ts(now))).fetchall()
    quoted = db().execute(
        'SELECT q.*, j.title, j.status AS job_status, j.suburb, a.name AS area_name, '
        '(SELECT COUNT(*) FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        '  AND m.sender_id <> q.trade_id AND m.read_at IS NULL) AS unread '
        'FROM quotes q JOIN jobs j ON j.id = q.job_id JOIN areas a ON a.id = j.area_id '
        'WHERE q.trade_id = ? ORDER BY q.id DESC LIMIT 30', (t['user_id'],)).fetchall()
    missed = db().execute(
        "SELECT o.status, j.title, o.resolved_at FROM offers o JOIN jobs j ON j.id = o.job_id "
        "WHERE o.trade_id = ? AND o.status IN ('expired','closed','declined') ORDER BY o.id DESC LIMIT 8",
        (t['user_id'],)).fetchall()
    return render_template('trade/dashboard.html', t=t, leads=leads, quoted=quoted, missed=missed,
                           progress=engine.guarantee_progress(db(), t['user_id'], now),
                           fair=engine.fairness_snapshot(db(), t['user_id'], t['tier'], now) if t['tier'] else None,
                           rating=engine.trade_rating(db(), t['user_id']),
                           subscribed=engine.is_subscribed(t, now),
                           invite_url=f'{integrations.site_url()}/join/{_invite_code(t)}',
                           invited=db().execute('SELECT COUNT(*) AS n FROM users WHERE referred_by = ?',
                                                (t['user_id'],)).fetchone()['n'])


@app.route('/trade/quotes')
@requires('trade')
def trade_quotes():
    t = current_trade()
    quotes = db().execute(
        'SELECT q.*, j.title, j.suburb, j.value_band, j.quote_count, j.status AS job_status, '
        'c.name AS category_name, a.name AS area_name, '
        '(SELECT COUNT(*) FROM messages m WHERE m.job_id = q.job_id AND m.trade_id = q.trade_id '
        ' AND m.sender_id <> q.trade_id AND m.read_at IS NULL) AS unread '
        'FROM quotes q JOIN jobs j ON j.id = q.job_id JOIN categories c ON c.id = j.category_id '
        'JOIN areas a ON a.id = j.area_id WHERE q.trade_id = ? ORDER BY q.id DESC', (t['user_id'],)).fetchall()
    counts = {key: sum(1 for q in quotes if q['status'] == key) for key in ('sent', 'shortlisted', 'accepted', 'declined')}
    counts['sent'] += counts['shortlisted']
    return render_template('trade/quotes.html', quotes=quotes, counts=counts)


@app.route('/trade/setup', methods=['GET', 'POST'])
@requires('trade')
def trade_setup():
    t = current_trade()
    tid = t['user_id']
    chosen_c = {r['category_id'] for r in db().execute('SELECT category_id FROM trade_categories WHERE trade_id = ?', (tid,))}
    chosen_a = {r['area_id'] for r in db().execute('SELECT area_id FROM trade_areas WHERE trade_id = ?', (tid,))}
    errors, form = {}, t
    hint = session.get('setup_hint')
    if hint and not chosen_c and not chosen_a:
        chosen_c, chosen_a = set(hint.get('categories', [])), set(hint.get('areas', []))
    if request.method == 'POST':
        f = request.form
        form = f
        chosen_c = {int(x) for x in f.getlist('categories') if x.isdigit()}
        chosen_a = {int(x) for x in f.getlist('areas') if x.isdigit()}
        if len(f.get('business_name', '').strip()) < 2:
            errors['business_name'] = 'Enter your business or trading name.'
        if not chosen_c:
            errors['categories'] = 'Pick at least one trade.'
        elif len(chosen_c) > 5:
            errors['categories'] = 'Pick up to 5 trades — the ones you’d quote on this week.'
        if not chosen_a:
            errors['areas'] = 'Pick at least one area you cover.'
        nzbn = re.sub(r'\D', '', f.get('nzbn', ''))
        if nzbn and not valid_nzbn(nzbn):
            errors['nzbn'] = 'That NZBN doesn’t look right. It’s 13 digits and starts with 94.'
        lic = f.get('licence_type', 'none')
        if lic not in LICENCES:
            errors['licence_type'] = 'Choose a licence type.'
        elif lic != 'none' and not f.get('licence_number', '').strip():
            errors['licence_number'] = 'Enter your licence or registration number.'
        expiry = f.get('insurance_expiry', '').strip()
        if expiry and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
            errors['insurance_expiry'] = 'Use the date picker.'
        years = f.get('years_trading', '').strip()
        if years and not years.isdigit():
            errors['years_trading'] = 'Enter a whole number of years.'
        if not errors:
            lic_no = f.get('licence_number', '').strip() if lic != 'none' else None
            insurer = f.get('insurance_insurer', '').strip() or None
            # Changing a checked detail clears its check, so badges only show what we verified.
            db().execute(
                'UPDATE trades SET business_name = ?, about = ?, years_trading = ?, workmanship_guarantee = ?, '
                'nzbn = ?, nzbn_checked_at = CASE WHEN nzbn = ? THEN nzbn_checked_at END, '
                'licence_type = ?, licence_number = ?, '
                'licence_checked_at = CASE WHEN licence_type = ? AND licence_number = ? THEN licence_checked_at END, '
                'insurance_insurer = ?, insurance_expiry = ?, '
                'insurance_checked_at = CASE WHEN insurance_insurer = ? AND insurance_expiry = ? THEN insurance_checked_at END '
                'WHERE user_id = ?',
                (f['business_name'].strip(), f.get('about', '').strip() or None, int(years) if years else None,
                 f.get('workmanship_guarantee', '').strip() or None, nzbn or None, nzbn or '',
                 lic, lic_no, lic, lic_no or '', insurer, expiry or None, insurer or '', expiry or '', tid))
            db().execute('DELETE FROM trade_categories WHERE trade_id = ?', (tid,))
            db().execute('DELETE FROM trade_areas WHERE trade_id = ?', (tid,))
            for c in chosen_c:
                db().execute('INSERT INTO trade_categories (trade_id, category_id) VALUES (?,?)', (tid, c))
            for a in chosen_a:
                db().execute('INSERT INTO trade_areas (trade_id, area_id) VALUES (?,?)', (tid, a))
            db().execute('UPDATE trades SET report_plan = ? WHERE user_id = ?',
                         (reporting.clean(f.getlist('report_plan')), tid))
            db().commit()
            session.pop('setup_hint', None)
            flash('Profile saved.')
            fresh = db().execute('SELECT * FROM trades WHERE user_id = ?', (tid,)).fetchone()
            return redirect(url_for('trade_home') if engine.is_subscribed(fresh) else url_for('trade_plan'))
    plan = (request.form.getlist('report_plan') if request.method == 'POST'
            else reporting.parse(t['report_plan']) if t['report_plan'] is not None else ['weekly'])
    return render_template('trade/setup.html', t=t, form=form, errors=errors, categories=all_categories(),
                           regions=areas_by_region(), chosen_c=chosen_c, chosen_a=chosen_a,
                           report_plan=plan, report_kinds=reporting.LABELS)


def _render_trade_job(job_id, form=None, error=None):
    t = current_trade()
    offer = engine.get_offer(db(), job_id, t['user_id'])
    if not offer:
        abort(404)
    engine.mark_seen(db(), job_id, t['user_id'])
    job = engine.get_job(db(), job_id)
    quote = db().execute('SELECT * FROM quotes WHERE job_id = ? AND trade_id = ?', (job_id, t['user_id'])).fetchone()
    contact = None
    if quote and quote['status'] in ('shortlisted', 'accepted'):
        contact = db().execute('SELECT name, phone, email FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
    can_quote = (offer['status'] == 'active' and parse_ts(offer['expires_at']) > utcnow()
                 and job['status'] == 'open')
    reported = db().execute('SELECT 1 FROM job_reports WHERE job_id = ? AND trade_id = ?',
                            (job_id, t['user_id'])).fetchone()
    return render_template('trade/job.html', t=t, job=job, offer=offer, quote=quote, contact=contact,
                           record=engine.customer_record(db(), job['customer_id']), photos=_photos(job_id),
                           can_quote=can_quote, reported=bool(reported), form=form or {}, error=error,
                           threshold=config.CONTRACT_THRESHOLD, templates=_templates(t['user_id']),
                           default_plan=reporting.parse(t['report_plan']) if 'report_plan' in t.keys() else [],
                           **(_progress(job) if job['hired_trade_id'] == t['user_id'] else {}))


@app.route('/trade/jobs/<int:job_id>')
@requires('trade')
def trade_job(job_id):
    return _render_trade_job(job_id)


def _money_field(value):
    v = re.sub(r'[^\d.]', '', value or '')
    try:
        return int(round(float(v))) if v else None
    except ValueError:
        return None


@app.post('/trade/jobs/<int:job_id>/quote')
@requires('trade')
def trade_quote(job_id):
    f = request.form
    q = dict(price_type=f.get('price_type'), amount_low=_money_field(f.get('amount_low')),
             amount_high=_money_field(f.get('amount_high')), gst_included=f.get('gst', 'incl') == 'incl',
             message=f.get('message', ''), inclusions=f.get('inclusions', '').strip() or None,
             exclusions=f.get('exclusions', '').strip() or None, warranty=f.get('warranty', '').strip() or None,
             available_from=f.get('available_from', '').strip() or None,
             duration=f.get('duration', '').strip() or None, act_docs_promised=bool(f.get('act_docs_promised')),
             report_plan=f.getlist('report_plan'))
    try:
        n = engine.submit_quote(db(), job_id, current_trade()['user_id'], q)
    except RuleError as e:
        return _render_trade_job(job_id, form=f, error=str(e))
    if f.get('save_template') and f.get('template_name', '').strip():
        _save_template(current_trade()['user_id'], f.get('template_name'), q)
    flash(f'Quote sent. It’s quote {n} of {config.MAX_QUOTES} for this job.')
    return redirect(url_for('trade_job', job_id=job_id))


@app.post('/trade/jobs/<int:job_id>/quote/edit')
@requires('trade')
def edit_quote(job_id):
    f = request.form
    fields = dict(price_type=f.get('price_type'), amount_low=_money_field(f.get('amount_low')),
                  amount_high=_money_field(f.get('amount_high')), gst_included=f.get('gst', 'incl') == 'incl',
                  message=f.get('message', ''), inclusions=f.get('inclusions', '').strip() or None,
                  exclusions=f.get('exclusions', '').strip() or None, warranty=f.get('warranty', '').strip() or None,
                  available_from=f.get('available_from', '').strip() or None,
                  duration=f.get('duration', '').strip() or None, act_docs_promised=bool(f.get('act_docs_promised')),
                  report_plan=f.getlist('report_plan'))
    try:
        engine.revise_quote(db(), engine.get_job(db(), job_id), current_trade()['user_id'], fields)
    except RuleError as e:
        return _render_trade_job(job_id, form=f, error=str(e))
    flash('Quote updated. The customer has been told.')
    return redirect(url_for('trade_job', job_id=job_id))


@app.post('/trade/jobs/<int:job_id>/pass')
@requires('trade')
def trade_pass(job_id):
    try:
        engine.decline_offer(db(), job_id, current_trade()['user_id'])
        flash('Passed. The job has gone to another trade.')
    except RuleError as e:
        flash(str(e), 'error')
    return redirect(url_for('trade_home'))


@app.post('/trade/jobs/<int:job_id>/report')
@requires('trade')
def trade_report(job_id):
    if not engine.get_offer(db(), job_id, current_trade()['user_id']):
        abort(404)
    engine.report_job(db(), job_id, current_trade()['user_id'], request.form.get('reason', 'other'),
                      request.form.get('note', '').strip())
    flash('Thanks. We’ll look into this job.')
    return redirect(url_for('trade_job', job_id=job_id))


@app.post('/trade/availability')
@requires('trade')
def trade_availability():
    t = current_trade()
    choice = request.form.get('choice')
    if choice == 'on':
        engine.set_pause(db(), t['user_id'], False)
        flash('New jobs are on.')
    else:
        days = {'1w': 7, '2w': 14, '4w': 28}.get(choice)
        until = ts(utcnow() + timedelta(days=days)) if days else None
        engine.set_pause(db(), t['user_id'], True, until)
        flash('New jobs are paused.' + (f' They’ll turn back on in {days} days.' if days else ''))
    db().commit()
    return redirect(url_for('trade_home') + '#availability')


@app.post('/trade/reviews/<int:review_id>/reply')
@requires('trade')
def review_reply(review_id):
    body = request.form.get('reply', '').strip()[:1500]
    db().execute('UPDATE reviews SET reply = ? WHERE id = ? AND trade_id = ?', (body or None, review_id,
                                                                                 current_trade()['user_id']))
    db().commit()
    return redirect(url_for('pro_profile', trade_id=current_trade()['user_id']))


@app.route('/trade/plan')
@requires('trade')
def trade_plan():
    t = current_trade()
    payments = db().execute('SELECT * FROM payments WHERE trade_id = ? ORDER BY period_start DESC LIMIT 24',
                            (t['user_id'],)).fetchall()
    claims = {c['payment_id']: c for c in db().execute('SELECT * FROM guarantee_claims WHERE trade_id = ?',
                                                        (t['user_id'],)).fetchall()}
    stats = {p['id']: engine.period_stats(db(), t['user_id'], p['period_start'], p['period_end']) for p in payments}
    return render_template('trade/plan.html', t=t, payments=payments, claims=claims, stats=stats,
                           subscribed=engine.is_subscribed(t), live=config.CHARGING,
                           progress=engine.guarantee_progress(db(), t['user_id']))


@app.post('/trade/plan/choose')
@requires('trade')
def choose_plan():
    t = current_trade()
    try:
        url = billing.choose_plan(db(), t, current_user()['email'], request.form.get('tier'),
                                  url_for('trade_plan', _external=True) + '?done=1',
                                  url_for('trade_plan', _external=True))
    except RuleError as e:
        flash(str(e), 'error')
        return redirect(url_for('trade_plan'))
    if url:
        return redirect(url, code=303)
    flash(f'You’re on {config.TIERS[request.form["tier"]]["name"]}. Matching jobs will start arriving.')
    return redirect(url_for('trade_home'))


@app.post('/trade/plan/cancel')
@requires('trade')
def cancel_plan():
    billing.cancel(db(), current_trade())
    flash('Cancelled. You keep getting jobs until the end of the month you’ve paid for.')
    return redirect(url_for('trade_plan'))


@app.post('/trade/plan/resume')
@requires('trade')
def resume_plan():
    billing.undo_cancel(db(), current_trade())
    flash('Your plan will renew as normal.')
    return redirect(url_for('trade_plan'))


# ── Admin ─────────────────────────────────────────────────────────────────────

def _count(sql, params=()):
    return db().execute(sql, params).fetchone()['n']



# ── Phone check ───────────────────────────────────────────────────────────────

@app.route('/verify-phone', methods=['GET', 'POST'])
def verify_phone():
    u = current_user()
    if not u:
        return redirect(url_for('login', next=request.full_path))
    nxt = _safe_next() or home_for(u)
    if u['phone_verified_at']:
        return redirect(nxt)
    error = None
    if request.method == 'POST':
        if request.form.get('action') == 'resend':
            code, sent = accounts.send_phone_code(db(), u)
            flash('New code sent.' if sent else 'Couldn’t send a text just now — try again shortly.')
            return redirect(url_for('verify_phone', next=nxt))
        result = accounts.check_phone_code(db(), u, request.form.get('code'))
        if result == 'ok':
            released = engine.release_held(db(), u['id'])
            if released:
                flash(f'Phone confirmed. Your job has gone out to {released} local trades.')
            elif u['role'] == 'customer':
                flash('Phone confirmed. Your job is live — we’ll offer it as matching trades join.')
            else:
                flash('Phone confirmed.')
            return redirect(nxt)
        error = {'wrong': 'That code isn’t right. Check the text and try again.',
                 'expired': 'That code has run out. Send yourself a new one.',
                 'locked': 'Too many wrong tries. Send yourself a new code.'}[result]
    return render_template('verify_phone.html', error=error, nxt=nxt, texts_on=sms.enabled())


# ── Describe helper ───────────────────────────────────────────────────────────

_ai_calls = defaultdict(list)


@app.post('/post/help')
def describe_help():
    """Rewrites a customer's rough notes into a clear brief. JSON in, JSON out."""
    if not ai.enabled():
        return {'error': 'The describe helper isn’t switched on.'}, 404
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    now = time.time()
    _ai_calls[ip] = [t for t in _ai_calls[ip] if now - t < 3600]
    if len(_ai_calls[ip]) >= 20:
        return {'error': 'That’s a lot of help for one hour — try again later.'}, 429
    notes = request.form.get('description', '').strip()
    if len(notes) < 8:
        return {'error': 'Write a few words about the job first, then I can tidy it up.'}, 400
    _ai_calls[ip].append(now)
    cat = db().execute('SELECT name FROM categories WHERE slug = ?', (request.form.get('category'),)).fetchone()
    try:
        result = ai.describe_job(cat['name'] if cat else '', request.form.get('title', '')[:120], notes[:4000])
    except ai.AIError as e:
        return {'error': str(e)}, 502
    return result


# ── Quote templates ───────────────────────────────────────────────────────────

def _templates(trade_id):
    return db().execute('SELECT * FROM quote_templates WHERE trade_id = ? ORDER BY name', (trade_id,)).fetchall()


def _save_template(trade_id, name, q):
    db().execute('INSERT INTO quote_templates (trade_id, name, price_type, message, inclusions, exclusions, '
                 'warranty, duration, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                 (trade_id, name.strip()[:60], q.get('price_type'), q.get('message'), q.get('inclusions'),
                  q.get('exclusions'), q.get('warranty'), q.get('duration'), ts(utcnow())))
    db().commit()


@app.route('/trade/templates', methods=['GET', 'POST'])
@requires('trade')
def trade_templates():
    t = current_trade()
    errors = {}
    if request.method == 'POST':
        f = request.form
        if len(f.get('name', '').strip()) < 2:
            errors['name'] = 'Give it a short name, like “Deck repair” or “Hot water cylinder”.'
        if len(f.get('message', '').strip()) < 20:
            errors['message'] = 'Write the message you usually send — at least a sentence.'
        if not errors:
            _save_template(t['user_id'], f['name'], dict(
                price_type=f.get('price_type') or None, message=f['message'].strip(),
                inclusions=f.get('inclusions', '').strip() or None, exclusions=f.get('exclusions', '').strip() or None,
                warranty=f.get('warranty', '').strip() or None, duration=f.get('duration', '').strip() or None))
            flash('Template saved. Pick it from the top of any quote form.')
            return redirect(url_for('trade_templates'))
    return render_template('trade/templates.html', templates=_templates(t['user_id']), errors=errors,
                           form=request.form)


@app.post('/trade/templates/<int:template_id>/delete')
@requires('trade')
def delete_template(template_id):
    db().execute('DELETE FROM quote_templates WHERE id = ? AND trade_id = ?', (template_id, current_trade()['user_id']))
    db().commit()
    flash('Template deleted.')
    return redirect(url_for('trade_templates'))


# ── Invite a mate ─────────────────────────────────────────────────────────────

def _invite_code(trade):
    if trade['invite_code']:
        return trade['invite_code']
    for _ in range(5):
        code = secrets.token_urlsafe(5).replace('-', '').replace('_', '')[:7].lower()
        try:
            db().execute('UPDATE trades SET invite_code = ? WHERE user_id = ?', (code, trade['user_id']))
            db().commit()
            return code
        except sqlite3.IntegrityError:
            continue
    return ''


def _inviter():
    code = session.get('invite')
    if not code:
        return None
    return db().execute('SELECT t.user_id, t.business_name FROM trades t JOIN users u ON u.id = t.user_id '
                        'WHERE t.invite_code = ? AND u.closed_at IS NULL', (code,)).fetchone()


@app.route('/join/<code>')
def join(code):
    trade = db().execute('SELECT business_name FROM trades WHERE invite_code = ?', (code.lower(),)).fetchone()
    if trade:
        session['invite'] = code.lower()
    return redirect(url_for('signup'))


# ── Price guides ──────────────────────────────────────────────────────────────

def _price_points(category_id, band=None):
    """Mid-point of every priced quote in a trade (and band), GST-inclusive."""
    sql = ("SELECT q.amount_low, q.amount_high, q.gst_included FROM quotes q JOIN jobs j ON j.id = q.job_id "
           "WHERE j.category_id = ? AND q.price_type <> 'site_visit' AND q.flagged = 0")
    args = [category_id]
    if band:
        sql += ' AND j.value_band = ?'
        args.append(band)
    points = []
    for r in db().execute(sql, args).fetchall():
        low, high = r['amount_low'], r['amount_high'] or r['amount_low']
        if not low:
            continue
        mid = (low + high) / 2
        points.append(mid if r['gst_included'] else mid * 1.15)
    return sorted(points)


def _spread(points):
    if len(points) < GUIDE_MIN_QUOTES:
        return None
    pick = lambda f: points[min(len(points) - 1, int(f * (len(points) - 1) + .5))]
    return {'n': len(points), 'low': pick(.25), 'mid': pick(.5), 'high': pick(.75)}


GUIDE_MIN_QUOTES = 5


@app.route('/guides')
def guides():
    counts = {r['category_id']: r['n'] for r in db().execute(
        "SELECT j.category_id AS category_id, COUNT(*) AS n FROM quotes q JOIN jobs j ON j.id = q.job_id "
        "WHERE q.price_type <> 'site_visit' GROUP BY j.category_id").fetchall()}
    return render_template('guides.html', categories=all_categories(), counts=counts, need=GUIDE_MIN_QUOTES)


@app.route('/guides/<slug>')
def guide(slug):
    cat = db().execute('SELECT * FROM categories WHERE slug = ?', (slug,)).fetchone()
    if not cat:
        abort(404)
    bands = {key: _spread(_price_points(cat['id'], key)) for key in config.VALUE_BANDS}
    return render_template('guide.html', cat=cat, bands=bands, need=GUIDE_MIN_QUOTES,
                           total=len(_price_points(cat['id'])))


@app.route('/sitemap.xml')
def sitemap():
    base = integrations.site_url()
    pages = ['/', '/pricing', '/post', '/signup', '/guides', '/terms', '/privacy'] + \
            [f'/guides/{c["slug"]}' for c in all_categories()]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
           ''.join(f'  <url><loc>{base}{p}</loc></url>\n' for p in pages) + '</urlset>\n'
    return body, 200, {'Content-Type': 'application/xml'}


# ── Admin: setup (email, texts, AI, site address) ─────────────────────────────

SETUP_GROUPS = [
    ('Email', 'So customers and tradies get alerts, and people can reset their passwords.',
     [('smtp_host', 'Email server', 'smtp.gmail.com'), ('smtp_port', 'Port', '587'),
      ('smtp_user', 'Username (your email address)', 'you@gmail.com'),
      ('smtp_pass', 'Password (for Gmail, an App Password)', ''),
      ('mail_from', 'Send from (optional)', f'{config.BRAND} <you@gmail.com>')]),
    ('Text messages', 'So tradies hear about new jobs straight away, and customers can confirm their phone.',
     [('twilio_sid', 'Twilio Account SID', 'AC…'), ('twilio_token', 'Twilio Auth Token', ''),
      ('twilio_from', 'Twilio phone number', '+6421…')]),
    ('“Help me describe it”', 'Turns a customer’s rough notes into a clear job description.',
     [('anthropic_key', 'Anthropic API key', 'sk-ant-…')]),
    ('Your web address', 'Used in every link we email or text. Set it once your own domain is connected.',
     [('site_url', 'Site address', 'https://level.co.nz')]),
]


@app.route('/admin/setup', methods=['GET', 'POST'])
@requires('admin')
def admin_setup():
    integrations.refresh(db(), force=True)
    if request.method == 'POST':
        f = request.form
        action = f.get('action', 'save')
        if action == 'save':
            changes = {}
            for _, _, fields in SETUP_GROUPS:
                for name, _, _ in fields:
                    if integrations.from_env(name):
                        continue
                    if f.get(f'clear_{name}'):
                        changes[name] = ''
                    elif f.get(name, '').strip():
                        changes[name] = f[name].strip()
            if changes.get('site_url'):
                changes['site_url'] = changes['site_url'].rstrip('/')
            integrations.save(db(), changes)
            flash('Saved.' if changes else 'Nothing changed.')
        elif action == 'test_email':
            to = f.get('to', '').strip() or current_user()['email']
            ok = mailer.send(to, current_user()['name'], f'Test email from {config.BRAND}',
                             f'This is a test from {config.BRAND}. If you can read it, email is working.')
            flash(f'Test email sent to {to}. Check the inbox (and spam).' if ok else
                  'The email didn’t send. Check the server, username and password — for Gmail it must be an '
                  'App Password, not your normal password.', 'message' if ok else 'error')
        elif action == 'test_text':
            to = f.get('to', '').strip() or (current_user()['phone'] or '')
            ok = sms.send(to, f'Test text from {config.BRAND}. Texts are working.')
            flash(f'Test text sent to {to}.' if ok else 'The text didn’t send. ' + sms.last_error_hint(),
                  'message' if ok else 'error')
        elif action == 'test_ai':
            try:
                result = ai.describe_job('Builder', '', 'back deck is rotten in places, about 4x5m, the rail wobbles')
                flash(f'The helper works. It suggested: “{result["title"]}”.')
            except ai.AIError as e:
                flash(str(e), 'error')
        return redirect(url_for('admin_setup'))
    status = {'email': mailer.enabled(), 'texts': sms.enabled(), 'ai': ai.enabled()}
    return render_template('admin/setup.html', groups=SETUP_GROUPS, status=status, integrations=integrations)


@app.route('/admin')
@requires('admin')
def admin_home():
    live_trade = 'FROM trades t JOIN users u ON u.id = t.user_id WHERE u.closed_at IS NULL'
    stats = dict(
        customers=_count("SELECT COUNT(*) AS n FROM users WHERE role = 'customer' AND closed_at IS NULL"),
        trades=_count(f'SELECT COUNT(*) AS n {live_trade}'),
        paying=_count(f"SELECT COUNT(*) AS n {live_trade} AND t.sub_status = 'active' AND t.period_end > ?",
                      (ts(utcnow()),)),
        open_jobs=_count("SELECT COUNT(*) AS n FROM jobs WHERE status = 'open'"),
        quotes=_count('SELECT COUNT(*) AS n FROM quotes'),
        claims=_count("SELECT COUNT(*) AS n FROM guarantee_claims WHERE status = 'pending'"),
        reports=_count("SELECT COUNT(*) AS n FROM job_reports WHERE status = 'open'") +
                _count('SELECT COUNT(*) AS n FROM quotes WHERE flagged = 1'),
        unverified=_count(f"SELECT COUNT(*) AS n {live_trade} AND ((t.licence_type IS NOT NULL "
                          "AND t.licence_type <> 'none' AND t.licence_checked_at IS NULL) "
                          'OR (t.nzbn IS NOT NULL AND t.nzbn_checked_at IS NULL) '
                          'OR (t.insurance_insurer IS NOT NULL AND t.insurance_checked_at IS NULL))'),
    )
    by_tier = {r['tier']: r['n'] for r in db().execute(
        'SELECT t.tier AS tier, COUNT(*) AS n FROM trades t JOIN users u ON u.id = t.user_id '
        "WHERE t.sub_status = 'active' AND u.closed_at IS NULL GROUP BY t.tier").fetchall()}
    jobs = db().execute(
        'SELECT j.*, c.name AS category_name, a.name AS area_name, '
        "(SELECT COUNT(*) FROM offers o WHERE o.job_id = j.id) AS offers_total, "
        "(SELECT COUNT(*) FROM offers o WHERE o.job_id = j.id AND o.status = 'active') AS offers_active "
        'FROM jobs j JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
        'ORDER BY j.id DESC LIMIT 25').fetchall()
    offset = db().execute("SELECT value FROM settings WHERE key = 'clock_offset_hours'").fetchone()
    return render_template('admin/index.html', stats=stats, by_tier=by_tier, jobs=jobs,
                           offset=float(offset['value']) if offset else 0, now=utcnow())


@app.route('/admin/jobs/<int:job_id>')
@requires('admin')
def admin_job(job_id):
    job = engine.get_job(db(), job_id)
    if not job:
        abort(404)
    offers = db().execute('SELECT o.*, t.business_name, t.tier FROM offers o JOIN trades t ON t.user_id = o.trade_id '
                          'WHERE o.job_id = ? ORDER BY o.wave, o.id', (job_id,)).fetchall()
    quotes = db().execute('SELECT q.*, t.business_name FROM quotes q JOIN trades t ON t.user_id = q.trade_id '
                          'WHERE q.job_id = ? ORDER BY q.id', (job_id,)).fetchall()
    customer = db().execute('SELECT * FROM users WHERE id = ?', (job['customer_id'],)).fetchone()
    return render_template('admin/job.html', job=job, offers=offers, quotes=quotes, customer=customer)


@app.route('/admin/inspect')
@requires('admin')
def admin_inspect():
    """Behind the scenes: how one job moves through the rules."""
    wanted = request.args.get('job', '')
    job = engine.get_job(db(), int(wanted)) if wanted.isdigit() else None
    if not job:
        latest = db().execute('SELECT id FROM jobs ORDER BY '
                              '(SELECT COUNT(*) FROM offers o WHERE o.job_id = jobs.id) DESC, id DESC '
                              'LIMIT 1').fetchone()
        job = engine.get_job(db(), latest['id']) if latest else None
    if not job:
        flash('No jobs yet. Load the demo data first.', 'error')
        return redirect(url_for('admin_home'))
    jobs = db().execute('SELECT j.id, j.title, j.status, c.name AS category_name FROM jobs j '
                        'JOIN categories c ON c.id = j.category_id ORDER BY j.id DESC LIMIT 40').fetchall()
    return render_template('admin/inspect.html', job=job, ex=engine.explain(db(), job), jobs=jobs,
                           now=utcnow())


@app.post('/admin/jobs/<int:job_id>/delete')
@requires('admin')
def admin_job_delete(job_id):
    """Remove a job completely — for spam, duplicates and test posts."""
    job = engine.get_job(db(), job_id)
    if not job:
        abort(404)
    for photo in _photos(job_id):
        if not db().execute('SELECT 1 FROM job_photos WHERE filename = ? AND job_id <> ?',
                            (photo['filename'], job_id)).fetchone():
            try:
                os.remove(os.path.join(UPLOAD_DIR, photo['filename']))
            except OSError:
                pass
    for table in ('messages', 'quotes', 'offers', 'job_reports', 'job_photos', 'reviews'):
        db().execute(f'DELETE FROM {table} WHERE job_id = ?', (job_id,))
    db().execute('DELETE FROM jobs WHERE id = ?', (job_id,))
    db().commit()
    flash(f'“{job["title"]}” and everything attached to it has been deleted.')
    return redirect(url_for('admin_home'))


@app.route('/admin/trades')
@requires('admin')
def admin_trades():
    """Every business on Level, with the numbers that matter for support."""
    search = request.args.get('q', '').strip().lower()
    show = request.args.get('show', 'all')
    since = ts(utcnow() - timedelta(days=30))
    where, args = ['1 = 1'], []
    if search:
        where.append('(LOWER(t.business_name) LIKE ? OR LOWER(u.email) LIKE ? OR LOWER(u.name) LIKE ? '
                     'OR LOWER(COALESCE(t.licence_number, \'\')) LIKE ?)')
        args += [f'%{search}%'] * 4
    if show == 'active':
        where.append("t.sub_status = 'active' AND t.paused = 0")
    elif show == 'paused':
        where.append('t.paused = 1')
    elif show == 'inactive':
        where.append("t.sub_status <> 'active'")
    elif show == 'unchecked':
        where.append("((t.licence_type IS NOT NULL AND t.licence_type <> 'none' AND t.licence_checked_at IS NULL) "
                     'OR (t.nzbn IS NOT NULL AND t.nzbn_checked_at IS NULL) '
                     'OR (t.insurance_insurer IS NOT NULL AND t.insurance_checked_at IS NULL))')

    rows = db().execute(
        'SELECT t.*, u.name, u.email, u.username, u.phone, u.closed_at, u.last_login_at, '
        '  (SELECT COUNT(*) FROM offers o WHERE o.trade_id = t.user_id AND o.offered_at >= ?) AS offers30, '
        '  (SELECT COUNT(*) FROM quotes q WHERE q.trade_id = t.user_id AND q.created_at >= ?) AS quotes30, '
        "  (SELECT COUNT(*) FROM quotes q WHERE q.trade_id = t.user_id AND q.status = 'accepted') AS wins, "
        '  (SELECT COUNT(*) FROM reviews r WHERE r.trade_id = t.user_id) AS reviews, '
        '  (SELECT AVG(rating) FROM reviews r WHERE r.trade_id = t.user_id) AS rating '
        'FROM trades t JOIN users u ON u.id = t.user_id '
        f'WHERE {" AND ".join(where)} ORDER BY t.created_at DESC LIMIT 300', (since, since, *args)).fetchall()

    work = {}
    for r in db().execute('SELECT tc.trade_id, c.name FROM trade_categories tc '
                          'JOIN categories c ON c.id = tc.category_id ORDER BY c.name').fetchall():
        work.setdefault(r['trade_id'], []).append(r['name'])
    places = {}
    for r in db().execute('SELECT ta.trade_id, a.name FROM trade_areas ta '
                          'JOIN areas a ON a.id = ta.area_id ORDER BY a.id').fetchall():
        places.setdefault(r['trade_id'], []).append(r['name'])
    return render_template('admin/trades.html', rows=rows, work=work, places=places,
                           search=request.args.get('q', ''), show=show)


@app.route('/admin/trades/<int:trade_id>')
@requires('admin')
def admin_trade(trade_id):
    t = db().execute('SELECT t.*, u.name, u.email, u.username, u.phone, u.closed_at, u.created_at AS joined, '
                     'u.last_login_at, u.email_verified_at FROM trades t JOIN users u ON u.id = t.user_id '
                     'WHERE t.user_id = ?', (trade_id,)).fetchone()
    if not t:
        abort(404)
    now = utcnow()
    return render_template(
        'admin/trade.html', t=t, now=now,
        work=[r['name'] for r in db().execute('SELECT c.name FROM trade_categories tc JOIN categories c '
                                              'ON c.id = tc.category_id WHERE tc.trade_id = ? ORDER BY c.name',
                                              (trade_id,)).fetchall()],
        places=[r['name'] for r in db().execute('SELECT a.name FROM trade_areas ta JOIN areas a ON a.id = ta.area_id '
                                                'WHERE ta.trade_id = ? ORDER BY a.id', (trade_id,)).fetchall()],
        offers=db().execute('SELECT o.*, j.title, j.value_band, a.name AS area_name FROM offers o '
                            'JOIN jobs j ON j.id = o.job_id JOIN areas a ON a.id = j.area_id '
                            'WHERE o.trade_id = ? ORDER BY o.id DESC LIMIT 20', (trade_id,)).fetchall(),
        quotes=db().execute('SELECT q.*, j.title FROM quotes q JOIN jobs j ON j.id = q.job_id '
                            'WHERE q.trade_id = ? ORDER BY q.id DESC LIMIT 20', (trade_id,)).fetchall(),
        reviews=db().execute('SELECT r.*, j.title FROM reviews r JOIN jobs j ON j.id = r.job_id '
                             'WHERE r.trade_id = ? ORDER BY r.id DESC LIMIT 10', (trade_id,)).fetchall(),
        payments=db().execute('SELECT * FROM payments WHERE trade_id = ? ORDER BY period_start DESC LIMIT 12',
                              (trade_id,)).fetchall(),
        claims=db().execute('SELECT * FROM guarantee_claims WHERE trade_id = ? ORDER BY id DESC LIMIT 12',
                            (trade_id,)).fetchall(),
        reported=db().execute('SELECT r.*, j.title FROM job_reports r JOIN jobs j ON j.id = r.job_id '
                              'WHERE r.trade_id = ? ORDER BY r.id DESC LIMIT 10', (trade_id,)).fetchall(),
        rating=engine.trade_rating(db(), trade_id),
        progress=engine.guarantee_progress(db(), trade_id, now),
        subscribed=engine.is_subscribed(t, now))


@app.post('/admin/trades/<int:trade_id>/<action>')
@requires('admin')
def admin_trade_action(trade_id, action):
    t = db().execute('SELECT t.*, u.name, u.email FROM trades t JOIN users u ON u.id = t.user_id '
                     'WHERE t.user_id = ?', (trade_id,)).fetchone()
    if not t:
        abort(404)
    f = request.form
    if action == 'check':
        field = {'nzbn': 'nzbn_checked_at', 'licence': 'licence_checked_at',
                 'insurance': 'insurance_checked_at'}.get(f.get('what'))
        if not field:
            abort(400)
        db().execute(f'UPDATE trades SET {field} = ? WHERE user_id = ?',
                     (ts(utcnow()) if f.get('ok') == '1' else None, trade_id))
        db().commit()
        flash('Checks updated.')
    elif action == 'plan':
        tier = f.get('tier')
        if tier in config.TIERS:
            billing.choose_plan(db(), t, t['email'], tier, '', '')
            flash(f'{t["business_name"]} is now on {config.TIERS[tier]["name"]}.')
        elif tier == 'stop':
            db().execute("UPDATE trades SET sub_status = 'cancelled', cancel_at_period_end = 0 WHERE user_id = ?",
                         (trade_id,))
            db().commit()
            flash(f'{t["business_name"]}’s plan has been stopped — they’ll get no new jobs.')
    elif action == 'availability':
        engine.set_pause(db(), trade_id, f.get('choice') == 'pause')
        db().commit()
        flash('Jobs paused for this trade.' if f.get('choice') == 'pause' else 'Jobs turned back on.')
    elif action == 'suspend':
        suspend = f.get('choice') == 'on'
        db().execute('UPDATE trades SET sub_status = ?, paused = ? WHERE user_id = ?',
                     ('suspended' if suspend else 'cancelled', 1 if suspend else 0, trade_id))
        if suspend:
            db().execute("UPDATE offers SET status = 'closed', resolved_at = ? WHERE trade_id = ? AND status = 'active'",
                         (ts(utcnow()), trade_id))
            engine.notify(db(), trade_id, 'Your account has been suspended. Get in touch if you think that’s wrong.',
                          '/trade')
        db().commit()
        flash('Business suspended — no more jobs, and their live offers were closed.' if suspend
              else 'Suspension lifted. They’ll need to choose a plan again.')
    elif action == 'password':
        if len(f.get('password', '')) < 8:
            flash('Use at least 8 characters.', 'error')
        elif not check_password_hash(current_user()['password_hash'], f.get('current_password', '')):
            flash('That isn’t your password, so nothing changed.', 'error')
        else:
            db().execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(f['password']), trade_id))
            db().commit()
            flash(f'New password set for {t["name"]}. Tell them, and ask them to change it in Settings.')
    elif action == 'message':
        body = f.get('body', '').strip()
        if body:
            engine.notify(db(), trade_id, body, '/trade')
            db().commit()
            flash('Message sent. They’ll see it in their notifications' +
                  (' and by email.' if mailer.enabled() else ' (email isn’t set up yet).'))
    elif action == 'remove':
        person = db().execute('SELECT * FROM users WHERE id = ?', (trade_id,)).fetchone()
        accounts.close(db(), person)
        flash(f'{t["business_name"]} has been removed and their details deleted.')
        return redirect(url_for('admin_trades'))
    else:
        abort(404)
    return redirect(url_for('admin_trade', trade_id=trade_id))


@app.route('/admin/customers')
@requires('admin')
def admin_customers():
    search = request.args.get('q', '').strip().lower()
    where, args = ["u.role = 'customer'"], []
    if search:
        where.append('(LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ? OR LOWER(COALESCE(u.phone, \'\')) LIKE ?)')
        args += [f'%{search}%'] * 3
    rows = db().execute(
        'SELECT u.*, (SELECT COUNT(*) FROM jobs j WHERE j.customer_id = u.id) AS jobs, '
        "  (SELECT COUNT(*) FROM jobs j WHERE j.customer_id = u.id AND j.status = 'hired') AS hired, "
        '  (SELECT COUNT(*) FROM quotes q JOIN jobs j ON j.id = q.job_id WHERE j.customer_id = u.id) AS quotes, '
        '  (SELECT MAX(j.created_at) FROM jobs j WHERE j.customer_id = u.id) AS last_job '
        f'FROM users u WHERE {" AND ".join(where)} ORDER BY u.id DESC LIMIT 300', args).fetchall()
    return render_template('admin/customers.html', rows=rows, search=request.args.get('q', ''))


@app.route('/admin/customers/<int:user_id>')
@requires('admin')
def admin_customer(user_id):
    person = db().execute("SELECT * FROM users WHERE id = ? AND role = 'customer'", (user_id,)).fetchone()
    if not person:
        abort(404)
    jobs = db().execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
                        'WHERE j.customer_id = ? ORDER BY j.id DESC LIMIT 50', (user_id,)).fetchall()
    return render_template('admin/customer.html', person=person, jobs=jobs,
                           record=engine.customer_record(db(), user_id))


@app.post('/admin/customers/<int:user_id>/<action>')
@requires('admin')
def admin_customer_action(user_id, action):
    person = db().execute("SELECT * FROM users WHERE id = ? AND role = 'customer'", (user_id,)).fetchone()
    if not person:
        abort(404)
    f = request.form
    if action == 'message':
        if f.get('body', '').strip():
            engine.notify(db(), user_id, f['body'].strip(), '/me')
            db().commit()
            flash('Message sent.')
    elif action == 'password':
        if len(f.get('password', '')) < 8:
            flash('Use at least 8 characters.', 'error')
        elif not check_password_hash(current_user()['password_hash'], f.get('current_password', '')):
            flash('That isn’t your password, so nothing changed.', 'error')
        else:
            db().execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(f['password']), user_id))
            db().commit()
            flash(f'New password set for {person["name"]}.')
    elif action == 'remove':
        accounts.close(db(), person)
        flash(f'{person["name"]}’s account has been closed and their details deleted.')
        return redirect(url_for('admin_customers'))
    else:
        abort(404)
    return redirect(url_for('admin_customer', user_id=user_id))


@app.route('/admin/team', methods=['GET', 'POST'])
@requires('admin')
def admin_team():
    """Other people who can run the site. Admins see every customer's contact
    details, so adding one asks for your own password first."""
    errors = {}
    if request.method == 'POST':
        f = request.form
        name, email = f.get('name', '').strip(), f.get('email', '').strip().lower()
        password = f.get('password', '')
        username, username_error = clean_username(f.get('username'))
        if not check_password_hash(current_user()['password_hash'], f.get('current_password', '')):
            errors['current_password'] = 'That isn’t your password.'
        if len(name) < 2:
            errors['name'] = 'Enter their name.'
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            errors['email'] = 'Enter a valid email address.'
        elif db().execute('SELECT 1 FROM users WHERE email = ?', (email,)).fetchone():
            errors['email'] = 'There’s already an account with that email.'
        if username_error:
            errors['username'] = username_error
        elif username and username_taken(username):
            errors['username'] = 'Someone already uses that username.'
        if len(password) < 8:
            errors['password'] = 'Use at least 8 characters — 12 or more for an admin.'
        if not errors:
            db().execute('INSERT INTO users (role, email, username, password_hash, name, phone, created_at) '
                         "VALUES ('admin',?,?,?,?,?,?)",
                         (email, username, hash_password(password), name,
                          f.get('phone', '').strip() or None, ts(utcnow())))
            db().commit()
            flash(f'{name} can now log in at /login with {email}. Ask them to change the password in Settings.')
            return redirect(url_for('admin_team'))
    admins = db().execute("SELECT id, name, email, username, phone, created_at, last_login_at FROM users "
                          "WHERE role = 'admin' AND closed_at IS NULL ORDER BY id").fetchall()
    return render_template('admin/team.html', admins=admins, errors=errors, form=request.form)


@app.post('/admin/team/<int:user_id>/password')
@requires('admin')
def admin_team_password(user_id):
    """Set another admin's password — for when someone is locked out."""
    f = request.form
    person = db().execute("SELECT * FROM users WHERE id = ? AND role = 'admin' AND closed_at IS NULL",
                          (user_id,)).fetchone()
    if not person:
        abort(404)
    if not check_password_hash(current_user()['password_hash'], f.get('current_password', '')):
        flash('That isn’t your password, so nothing was changed.', 'error')
    elif len(f.get('password', '')) < 8:
        flash('Use at least 8 characters for the new password.', 'error')
    else:
        db().execute('UPDATE users SET password_hash = ? WHERE id = ?',
                     (hash_password(f['password']), user_id))
        engine.notify(db(), user_id, 'Another admin set a new password on your account.', '/settings')
        db().commit()
        flash(f'New password set for {person["name"]}. Tell them, and ask them to change it in Settings.')
    return redirect(url_for('admin_team'))


@app.post('/admin/team/<int:user_id>/remove')
@requires('admin')
def admin_team_remove(user_id):
    if user_id == current_user()['id']:
        flash('You can’t remove your own access here.', 'error')
    elif db().execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND closed_at IS NULL").fetchone()['n'] <= 1:
        flash('There has to be at least one admin.', 'error')
    else:
        person = db().execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if person:
            accounts.close(db(), person)
            flash(f'{person["name"]} no longer has access.')
    return redirect(url_for('admin_team'))


@app.route('/admin/claims')
@requires('admin')
def admin_claims():
    rows = db().execute('SELECT g.*, t.business_name FROM guarantee_claims g JOIN trades t ON t.user_id = g.trade_id '
                        "ORDER BY CASE g.status WHEN 'pending' THEN 0 ELSE 1 END, g.id DESC LIMIT 200").fetchall()
    return render_template('admin/claims.html', rows=rows)


@app.post('/admin/claims/<int:claim_id>/<action>')
@requires('admin')
def admin_claim(claim_id, action):
    try:
        if action == 'refund':
            billing.refund_claim(db(), claim_id)
            flash('Refund issued.')
        elif action == 'deny':
            billing.deny_claim(db(), claim_id, request.form.get('note', '').strip() or 'Did not meet the conditions.')
            flash('Claim declined and the trade told why.')
    except RuleError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin_claims'))


@app.route('/admin/reports')
@requires('admin')
def admin_reports():
    reports = db().execute('SELECT r.*, j.title, t.business_name FROM job_reports r JOIN jobs j ON j.id = r.job_id '
                           "JOIN trades t ON t.user_id = r.trade_id ORDER BY CASE r.status WHEN 'open' THEN 0 ELSE 1 END, "
                           'r.id DESC LIMIT 200').fetchall()
    flagged = db().execute('SELECT q.*, j.title, t.business_name FROM quotes q JOIN jobs j ON j.id = q.job_id '
                           'JOIN trades t ON t.user_id = q.trade_id WHERE q.flagged = 1 ORDER BY q.id DESC').fetchall()
    return render_template('admin/reports.html', reports=reports, flagged=flagged)


@app.post('/admin/reports/<int:report_id>/resolve')
@requires('admin')
def admin_resolve_report(report_id):
    db().execute("UPDATE job_reports SET status = 'resolved' WHERE id = ?", (report_id,))
    db().commit()
    return redirect(url_for('admin_reports'))


@app.post('/admin/quotes/<int:quote_id>/unflag')
@requires('admin')
def admin_unflag(quote_id):
    db().execute('UPDATE quotes SET flagged = 0 WHERE id = ?', (quote_id,))
    db().commit()
    return redirect(url_for('admin_reports'))


@app.post('/admin/sweep')
@requires('admin')
def admin_sweep():
    r = engine.sweep(db())
    flash(f'Sweep done: {r["offers_expired"]} offers expired, {r["slots_filled"]} new offers, '
          f'{r["jobs_expired"]} jobs closed, {r["months_settled"]} months settled.')
    return redirect(url_for('admin_home'))


@app.post('/admin/clock')
@requires('admin')
def admin_clock():
    if not DEMO_TOOLS:
        abort(404)
    hours = request.form.get('hours', '0')
    offset = 0.0 if hours == 'reset' else engine._clock['offset_hours'] + float(hours)
    db().execute("DELETE FROM settings WHERE key = 'clock_offset_hours'")
    db().execute("INSERT INTO settings (key, value) VALUES ('clock_offset_hours', ?)", (str(offset),))
    db().commit()
    engine.set_clock_offset(offset)
    r = engine.sweep(db())
    flash(f'Clock is now {offset:g} h ahead. {r["offers_expired"]} offers expired and {r["slots_filled"]} '
          f'went to new trades.')
    return redirect(url_for('admin_home'))


@app.post('/admin/seed')
@requires('admin')
def admin_seed():
    if not DEMO_TOOLS:
        abort(404)
    import seed
    flash(seed.run(db()))
    return redirect(url_for('admin_home'))


# ── Stripe ────────────────────────────────────────────────────────────────────

@app.post('/stripe/webhook')
def stripe_webhook():
    if not config.CHARGING:
        abort(404)
    try:
        billing.handle_webhook(db(), request.get_data(), request.headers.get('Stripe-Signature', ''))
    except Exception as e:                      # bad signature or payload
        app.logger.warning('Stripe webhook rejected: %s', e)
        return 'rejected', 400
    return 'ok', 200


# ── Errors ────────────────────────────────────────────────────────────────────

_ERRORS = {
    400: ('That didn’t go through', 'Your session may have expired. Go back, refresh the page and try again.'),
    403: ('Not available to your account', 'This page belongs to a different kind of account.'),
    404: ('Page not found', 'The link may be old, or the job may have closed.'),
    413: ('Photos too large', 'Keep the photos under 40 MB in total and try again.'),
}


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def _error(e):
    title, body = _ERRORS.get(e.code, ('Something went wrong', ''))
    return render_template('error.html', title=title, body=body), e.code


# ── Phone app API (api_mobile.py) ─────────────────────────────────────────────

import api_mobile  # noqa: E402
api_mobile.init_app(app, sys.modules[__name__])


# ── Background sweep ──────────────────────────────────────────────────────────

def sweep_once():
    with app.app_context():
        conn = get_db()
        try:
            if DEMO_TOOLS:
                _load_clock(conn)
            if engine.try_lock(conn, 'sweep', max(30, SWEEP_SECONDS // 2)):
                integrations.refresh(conn, force=True)
                engine.sweep(conn)
                mailer.flush(conn)
                sms.flush(conn)
                push.flush(conn)
                outreach.flush(conn)
                reporting.remind(conn)
        finally:
            if not _USE_PG:
                conn.close()


def _sweeper():
    while True:
        time.sleep(SWEEP_SECONDS)
        try:
            sweep_once()
        except Exception:
            app.logger.exception('Sweep failed')


if os.environ.get('RUN_SWEEPER', '1') == '1':
    threading.Thread(target=_sweeper, daemon=True, name='sweeper').start()


# ── Progress updates ──────────────────────────────────────────────────────────

def _progress(job):
    """Template data for the progress-updates section of a hired job."""
    if job['status'] != 'hired':
        return {}
    updates = reporting.updates_for(db(), job['id'])
    photos = defaultdict(list)
    if updates:
        marks = ','.join('?' * len(updates))
        for p in db().execute(f'SELECT * FROM progress_photos WHERE update_id IN ({marks}) ORDER BY id',
                              [u['id'] for u in updates]):
            photos[p['update_id']].append(p)
    return {'updates': updates, 'update_photos': photos, 'report_score': reporting.score(job, updates),
            'report_kinds': reporting.LABELS, 'report_plan_text': reporting.describe(job['report_plan']),
            'report_plan': reporting.parse(job['report_plan']), 'nz_today': reporting.nz_today()}


def _save_photos(files, limit):
    """Validate and store uploaded photos. Returns (names, error)."""
    photos = [p for p in files if p and p.filename]
    if len(photos) > limit:
        return [], f'Add up to {limit} photos.'
    if any(p.filename.rsplit('.', 1)[-1].lower() not in PHOTO_TYPES for p in photos):
        return [], 'Photos must be JPG, PNG, WEBP or HEIC.'
    names = []
    for p in photos:
        name = f'{uuid.uuid4().hex}.{p.filename.rsplit(".", 1)[-1].lower()}'
        p.save(os.path.join(UPLOAD_DIR, name))
        names.append(name)
    return names, None


@app.post('/trade/jobs/<int:job_id>/updates')
@requires('trade')
def trade_post_update(job_id):
    job = engine.get_job(db(), job_id)
    if not job or job['hired_trade_id'] != current_trade()['user_id']:
        abort(404)
    names, error = _save_photos(request.files.getlist('photos'), reporting.MAX_PHOTOS)
    if not error:
        try:
            reporting.post_update(db(), job, current_trade()['user_id'], request.form.get('body'),
                                  request.form.getlist('kinds'), names)
            flash('Update posted. The customer has been told.')
        except reporting.ReportError as e:
            error = str(e)
    if error:
        flash(error, 'error')
    return redirect(url_for('trade_job', job_id=job_id) + '#updates')


@app.post('/trade/jobs/<int:job_id>/start')
@requires('trade')
def trade_set_start(job_id):
    job = engine.get_job(db(), job_id)
    if not job:
        abort(404)
    try:
        reporting.set_start(db(), job, current_trade()['user_id'], request.form.get('start'))
        flash('Start date saved.')
    except reporting.ReportError as e:
        flash(str(e), 'error')
    return redirect(url_for('trade_job', job_id=job_id) + '#updates')


@app.post('/trade/jobs/<int:job_id>/finish')
@requires('trade')
def trade_finish(job_id):
    job = engine.get_job(db(), job_id)
    if not job or job['hired_trade_id'] != current_trade()['user_id']:
        abort(404)
    try:
        reporting.finish(db(), job, current_trade()['user_id'])
        flash('Marked as finished. Updates are no longer due.')
    except reporting.ReportError as e:
        flash(str(e), 'error')
    return redirect(url_for('trade_job', job_id=job_id) + '#updates')


@app.post('/me/jobs/<int:job_id>/finish')
@requires('customer')
def customer_finish(job_id):
    job = _my_job(job_id)
    try:
        reporting.finish(db(), job, current_user()['id'])
        flash('Marked as finished.')
    except reporting.ReportError as e:
        flash(str(e), 'error')
    return redirect(url_for('customer_job', job_id=job_id) + '#updates')


# ── Referrals ─────────────────────────────────────────────────────────────────

@app.route('/trade/referrals')
@requires('trade')
def trade_referrals():
    t = current_trade()
    joined = db().execute("SELECT u.created_at, tr.business_name, "
                          "(SELECT COUNT(*) FROM quotes q WHERE q.trade_id = u.id) AS quotes "
                          "FROM users u JOIN trades tr ON tr.user_id = u.id WHERE u.referred_by = ? "
                          "ORDER BY u.id DESC", (t['user_id'],)).fetchall()
    return render_template('trade/referrals.html', t=t, joined=joined, bank=referrals.banked(db(), t['user_id']),
                           invite_url=f'{integrations.site_url()}/join/{_invite_code(t)}')


@app.route('/me/share', methods=['GET', 'POST'])
@requires('customer')
def customer_share():
    me = current_user()
    errors, done = None, None
    form = request.form if request.method == 'POST' else {}
    if request.method == 'POST':
        try:
            prospect, existing = referrals.recommend(db(), me, request.form)
        except referrals.RecommendError as e:
            errors = str(e)
        else:
            if existing:
                done = {'existing': existing['id']}
            else:
                emailed = bool(request.form.get('email_them')) and referrals.send_invite(db(), prospect, me)
                done = {'link': f'{integrations.site_url()}/o/{prospect["token"]}', 'emailed': emailed,
                        'name': prospect['business_name']}
            form = {}
    mine = db().execute("SELECT p.business_name, p.status, c.name AS category_name FROM prospects p "
                        "JOIN categories c ON c.id = p.category_id WHERE p.recommended_by = ? ORDER BY p.id DESC",
                        (me['id'],)).fetchall()
    friends = db().execute("SELECT COUNT(*) AS n FROM users WHERE referred_by = ? AND role = 'customer'",
                           (me['id'],)).fetchone()['n']
    return render_template('customer/share.html', share_url=f'{integrations.site_url()}/r/{referrals.ref_code(db(), me)}',
                           categories=all_categories(), regions=areas_by_region(), form=form, error=errors,
                           done=done, mine=mine, friends=friends, email_on=mailer.enabled())


@app.route('/r/<code>')
def customer_ref(code):
    sharer = referrals.referrer_for_code(db(), code)
    if sharer and not current_user():
        session['ref'] = code.lower()
        flash(f'{(sharer["name"] or "A friend").split(" ")[0]} shared {config.BRAND} with you. '
              'Post your job free and get up to 6 quotes from local trades.')
    return redirect(url_for('post_job'))


@app.route('/admin/referrals')
@requires('admin')
def admin_referrals():
    trades, customers = referrals.leaderboard(db(), 50)
    totals = {
        'trade_invites': db().execute("SELECT COUNT(*) AS n FROM users u JOIN users r ON r.id = u.referred_by "
                                      "WHERE u.role = 'trade' AND r.role = 'trade'").fetchone()['n'],
        'months': db().execute('SELECT COALESCE(SUM(months),0) AS n FROM referral_rewards').fetchone()['n'],
        'recommended': db().execute('SELECT COUNT(*) AS n FROM prospects WHERE recommended_by IS NOT NULL').fetchone()['n'],
        'recommended_joined': db().execute("SELECT COUNT(*) AS n FROM prospects WHERE recommended_by IS NOT NULL "
                                           "AND status = 'signed_up'").fetchone()['n'],
        'friends': db().execute("SELECT COUNT(*) AS n FROM users u JOIN users r ON r.id = u.referred_by "
                                "WHERE u.role = 'customer' AND r.role = 'customer'").fetchone()['n'],
    }
    return render_template('admin/referrals.html', trades=trades, customers=customers, totals=totals,
                           cap=referrals.MAX_MONTHS)


# ── Outreach: free leads for trades who aren't on the site yet ────────────────

def _outreach_job(job_id):
    job = db().execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                       'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
                       'WHERE j.id = ?', (job_id,)).fetchone()
    if not job:
        abort(404)
    return job


def _outreach_send_now():
    """Send just-queued emails in the background rather than making the admin wait."""
    def run():
        with app.app_context():
            conn = get_db()
            try:
                outreach.flush(conn, limit=60)
            except Exception:
                app.logger.exception('Outreach send failed')
            finally:
                if not _USE_PG:
                    conn.close()
    threading.Thread(target=run, daemon=True, name='outreach').start()


@app.route('/admin/outreach')
@requires('admin')
def admin_outreach():
    show, search, trade = request.args.get('show', 'all'), request.args.get('q', '').strip(), request.args.get('trade', '')
    sql = ('SELECT p.*, c.name AS category_name FROM prospects p JOIN categories c ON c.id = p.category_id '
           'WHERE 1 = 1')
    args = []
    if show in outreach.STATUSES:
        sql += ' AND p.status = ?'
        args.append(show)
    elif show == 'dnc':
        sql += ' AND p.do_not_contact = 1'
    if trade.isdigit():
        sql += ' AND p.category_id = ?'
        args.append(int(trade))
    if search:
        sql += ' AND (LOWER(p.business_name) LIKE ? OR LOWER(p.email) LIKE ?)'
        args += [f'%{search.lower()}%'] * 2
    rows = db().execute(sql + ' ORDER BY c.name, p.business_name LIMIT 500', args).fetchall()
    places = defaultdict(list)
    for r in db().execute('SELECT pa.prospect_id, a.name FROM prospect_areas pa JOIN areas a ON a.id = pa.area_id '
                          'ORDER BY a.id'):
        places[r['prospect_id']].append(r['name'])
    jobs = []
    for j in db().execute("SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j "
                          "JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id "
                          "WHERE j.status = 'open' ORDER BY j.created_at DESC LIMIT 30").fetchall():
        sent = db().execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE job_id = ? AND status IN ('queued','sent')",
                            (j['id'],)).fetchone()['n']
        jobs.append({'job': j, 'matches': len(outreach.matches(db(), j)), 'sent': sent})
    cats = db().execute('SELECT id, name FROM categories ORDER BY name').fetchall()
    return render_template('admin/outreach.html', rows=rows, places=places, jobs=jobs, cats=cats,
                           stats=outreach.stats(db()), statuses=outreach.STATUSES, show=show, search=search,
                           trade=trade, email_on=mailer.enabled(), max_leads=outreach.MAX_LEADS)


@app.route('/admin/outreach/import', methods=['POST'])
@requires('admin')
def admin_outreach_import():
    upload = request.files.get('file')
    if not upload or not upload.filename:
        flash('Choose the outreach spreadsheet to upload.', 'error')
        return redirect(url_for('admin_outreach'))
    try:
        rows = outreach.read_upload(upload.filename, upload.read())
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('admin_outreach'))
    except Exception:
        app.logger.exception('Outreach import failed')
        flash('That file couldn’t be read. Upload the outreach spreadsheet as .xlsx, or a .csv.', 'error')
        return redirect(url_for('admin_outreach'))
    added, updated, skipped = outreach.import_rows(db(), rows)
    note = f' {skipped} row{"s" if skipped != 1 else ""} skipped (no business name or an unknown trade).' if skipped else ''
    flash(f'Imported: {added} new, {updated} updated.{note}', 'ok')
    return redirect(url_for('admin_outreach'))


@app.route('/admin/outreach/job/<int:job_id>', methods=['GET', 'POST'])
@requires('admin')
def admin_outreach_job(job_id):
    job = _outreach_job(job_id)
    me = current_user()
    candidates = outreach.matches(db(), job)
    summary = outreach.default_summary(job['description'])
    error = None
    if request.method == 'POST':
        summary = re.sub(r'\s+', ' ', request.form.get('summary', '')).strip()
        chosen = {int(x) for x in request.form.getlist('prospect') if x.isdigit()}
        if job['status'] != 'open':
            error = 'This job isn’t taking quotes any more, so there’s nothing to offer.'
        elif not 10 <= len(summary) <= 300:
            error = 'Write a one-line summary of the job (10 to 300 characters).'
        elif not chosen:
            error = 'Tick at least one business.'
        else:
            n = outreach.queue(db(), job, chosen, summary, me)
            if mailer.enabled():
                _outreach_send_now()
                flash(f'{n} email{"s" if n != 1 else ""} sending now — each business gets its own.', 'ok')
            else:
                flash(f'{n} email{"s" if n != 1 else ""} queued. They’ll go out once email is set up in Admin → Setup '
                      f'(queued emails older than {outreach.SEND_WINDOW_HOURS} hours are dropped).', 'ok')
            return redirect(url_for('admin_outreach'))
    sample = candidates[0] if candidates else None
    preview = outreach.compose(sample, job, summary, (me['name'] or 'The team').split(' ')[0]) if sample else None
    already = db().execute('SELECT p.business_name, s.status, s.sent_at, s.clicked_at FROM prospect_sends s '
                           'JOIN prospects p ON p.id = s.prospect_id WHERE s.job_id = ? ORDER BY s.id',
                           (job_id,)).fetchall()
    return render_template('admin/outreach_job.html', job=job, candidates=candidates, summary=summary,
                           preview=preview, sample=sample, already=already, error=error,
                           email_on=mailer.enabled(), reply_to=outreach.reply_address(me))


@app.route('/admin/outreach/prospect/<int:pid>', methods=['POST'])
@requires('admin')
def admin_outreach_prospect(pid):
    p = db().execute('SELECT * FROM prospects WHERE id = ?', (pid,)).fetchone()
    if not p:
        abort(404)
    status = request.form.get('status')
    if status in outreach.STATUSES:
        db().execute('UPDATE prospects SET status = ?, updated_at = ? WHERE id = ?', (status, ts(utcnow()), pid))
    if request.form.get('do_not_contact') == '1':
        db().execute('UPDATE prospects SET do_not_contact = 1, updated_at = ? WHERE id = ?', (ts(utcnow()), pid))
    db().commit()
    flash(f'{p["business_name"]} updated.', 'ok')
    return redirect(request.referrer if (request.referrer or '').startswith(request.host_url) else url_for('admin_outreach'))


@app.route('/admin/outreach/flyer')
@requires('admin')
def admin_outreach_flyer():
    import segno
    audience = 'trades' if request.args.get('for') == 'trades' else 'homeowners'
    path = url_for('signup') if audience == 'trades' else url_for('post_job')   # sign-up defaults to trades
    target = integrations.site_url() + path
    qr = segno.make(target, error='m').svg_inline(scale=6, dark='#1F2933', omitsize=True)
    return render_template('admin/flyer.html', audience=audience, target=target, qr=qr)


@app.route('/o/<token>')
def outreach_link(token):
    """The personal link in an outreach email: remember who they are, then sign up."""
    p = outreach.by_token(db(), token)
    if not p:
        return redirect(url_for('signup'))
    job_id = request.args.get('j', '')
    outreach.clicked(db(), p, int(job_id) if job_id.isdigit() else None)
    if current_user():
        return redirect(home_for(current_user()))
    session['prospect'] = token
    return redirect(url_for('signup'))


@app.route('/o/<token>/stop', methods=['GET', 'POST'])
def outreach_stop(token):
    p = outreach.by_token(db(), token)
    done = bool(p) and p['status'] == 'unsubscribed'
    if request.method == 'POST' and p:
        outreach.unsubscribe(db(), p)
        done = True
    return render_template('outreach_stop.html', p=p, done=done)


@app.cli.command('sweep')
def sweep_command():
    """Run the time-based rules once (for a cron job)."""
    sweep_once()
    print('Sweep complete.')


if __name__ == '__main__':
    # HOST=0.0.0.0 opens the site to other devices on your Wi-Fi (e.g. your phone).
    # The debugger can run code from the browser, so it's only ever on for localhost.
    host = os.environ.get('HOST', '127.0.0.1')
    app.run(host=host, port=int(os.environ.get('PORT', 5050)),
            debug=not _USE_PG and host in ('127.0.0.1', 'localhost'))
