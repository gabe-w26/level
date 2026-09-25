"""
Database schema and seed data.

Timestamps are stored as UTC 'YYYY-MM-DD HH:MM:SS' TEXT and always computed in
Python — never with SQL date functions — so the same queries behave identically
on SQLite (local) and PostgreSQL (production).
"""
import os
import re
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

import config
from db import get_db, _USE_PG

BRAND_SLUG = re.sub(r'[^a-z0-9]+', '', config.BRAND.lower()) or 'level'

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    role                TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    username            TEXT,
    password_hash       TEXT NOT NULL,
    name                TEXT NOT NULL,
    phone               TEXT,
    email_verified_at   TEXT,
    email_alerts        INTEGER NOT NULL DEFAULT 1,
    unsub_token         TEXT,
    closed_at           TEXT,
    created_at          TEXT NOT NULL,
    last_login_at       TEXT
);

CREATE TABLE IF NOT EXISTS tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    purpose         TEXT NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    used_at         TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    licence_note    TEXT
);

CREATE TABLE IF NOT EXISTS areas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    region          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    user_id                 INTEGER PRIMARY KEY,
    business_name           TEXT NOT NULL,
    about                   TEXT,
    years_trading           INTEGER,
    nzbn                    TEXT,
    nzbn_checked_at         TEXT,
    licence_type            TEXT,
    licence_number          TEXT,
    licence_checked_at      TEXT,
    insurance_insurer       TEXT,
    insurance_expiry        TEXT,
    insurance_checked_at    TEXT,
    workmanship_guarantee   TEXT,
    tier                    TEXT,
    sub_status              TEXT NOT NULL DEFAULT 'none',
    cancel_at_period_end    INTEGER NOT NULL DEFAULT 0,
    period_start            TEXT,
    period_end              TEXT,
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    paused                  INTEGER NOT NULL DEFAULT 0,
    paused_until            TEXT,
    streak_reset_at         TEXT,
    last_offered_at         TEXT,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_categories (
    trade_id        INTEGER NOT NULL,
    category_id     INTEGER NOT NULL,
    PRIMARY KEY (trade_id, category_id)
);

CREATE TABLE IF NOT EXISTS trade_areas (
    trade_id        INTEGER NOT NULL,
    area_id         INTEGER NOT NULL,
    PRIMARY KEY (trade_id, area_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    category_id     INTEGER NOT NULL,
    area_id         INTEGER NOT NULL,
    suburb          TEXT,
    address         TEXT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    value_band      TEXT NOT NULL,
    timing          TEXT,
    property_type   TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    quote_count     INTEGER NOT NULL DEFAULT 0,
    hired_trade_id  INTEGER,
    created_at      TEXT NOT NULL,
    closes_at       TEXT NOT NULL,
    closed_at       TEXT,
    close_reason    TEXT
);

CREATE TABLE IF NOT EXISTS job_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    filename        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    wave            INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'active',
    offered_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    seen_at         TEXT,
    resolved_at     TEXT,
    UNIQUE (job_id, trade_id)
);

CREATE TABLE IF NOT EXISTS quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    price_type      TEXT NOT NULL,
    amount_low      INTEGER,
    amount_high     INTEGER,
    gst_included    INTEGER NOT NULL DEFAULT 1,
    message         TEXT NOT NULL,
    inclusions      TEXT,
    exclusions      TEXT,
    warranty        TEXT,
    available_from  TEXT,
    duration        TEXT,
    status          TEXT NOT NULL DEFAULT 'sent',
    act_docs_promised INTEGER NOT NULL DEFAULT 0,
    act_docs_ack_at TEXT,
    flagged         INTEGER NOT NULL DEFAULT 0,
    flag_reason     TEXT,
    created_at      TEXT NOT NULL,
    responded_at    TEXT,
    UNIQUE (job_id, trade_id)
);

CREATE TABLE IF NOT EXISTS job_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL,
    UNIQUE (job_id, trade_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    sender_id       INTEGER NOT NULL,
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    read_at         TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    customer_id     INTEGER NOT NULL,
    rating          REAL NOT NULL,
    workmanship     INTEGER NOT NULL,
    communication   INTEGER NOT NULL,
    timeliness      INTEGER NOT NULL,
    value_for_money INTEGER NOT NULL,
    body            TEXT,
    reply           TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (job_id, trade_id)
);

CREATE TABLE IF NOT EXISTS payments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            INTEGER NOT NULL,
    tier                TEXT NOT NULL,
    amount_cents        INTEGER NOT NULL,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    status              TEXT NOT NULL,
    stripe_invoice_id   TEXT UNIQUE,
    paused_during       INTEGER NOT NULL DEFAULT 0,
    claim_checked       INTEGER NOT NULL DEFAULT 0,
    reminder_sent       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guarantee_claims (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            INTEGER NOT NULL,
    payment_id          INTEGER NOT NULL UNIQUE,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    amount_cents        INTEGER NOT NULL,
    offers_received     INTEGER NOT NULL,
    quotes_sent         INTEGER NOT NULL,
    jobs_won            INTEGER NOT NULL,
    quotes_required     INTEGER NOT NULL,
    status              TEXT NOT NULL,
    stripe_refund_id    TEXT,
    note                TEXT,
    created_at          TEXT NOT NULL,
    decided_at          TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    body            TEXT NOT NULL,
    link            TEXT,
    created_at      TEXT NOT NULL,
    read_at         TEXT,
    emailed_at      TEXT
);

CREATE TABLE IF NOT EXISTS quote_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    price_type      TEXT,
    message         TEXT,
    inclusions      TEXT,
    exclusions      TEXT,
    warranty        TEXT,
    duration        TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS locks (
    name            TEXT PRIMARY KEY,
    holder          TEXT,
    expires_at      TEXT NOT NULL
);

-- The phone app: sign-in tokens (only a hash is kept) and Expo push tokens.
CREATE TABLE IF NOT EXISTS api_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    device          TEXT,
    created_at      TEXT NOT NULL,
    last_used_at    TEXT
);

CREATE TABLE IF NOT EXISTS push_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    token           TEXT NOT NULL UNIQUE,
    platform        TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user  ON api_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_push_tokens_user ON push_tokens (user_id);

-- Outreach: local businesses not on the site yet, who we email free leads to.
CREATE TABLE IF NOT EXISTS prospects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id     INTEGER NOT NULL,
    business_name   TEXT NOT NULL,
    first_name      TEXT,
    email           TEXT,
    phone           TEXT,
    website         TEXT,
    based_in        TEXT,
    source_url      TEXT,
    notes           TEXT,
    do_not_contact  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'new',
    leads_sent      INTEGER NOT NULL DEFAULT 0,
    last_sent_at    TEXT,
    token           TEXT NOT NULL UNIQUE,
    user_id         INTEGER,
    source          TEXT,
    recommended_by  INTEGER,
    unsubscribed_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS prospect_areas (
    prospect_id     INTEGER NOT NULL,
    area_id         INTEGER NOT NULL,
    PRIMARY KEY (prospect_id, area_id)
);

CREATE TABLE IF NOT EXISTS prospect_sends (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id     INTEGER NOT NULL,
    job_id          INTEGER NOT NULL,
    summary         TEXT,
    sender_id       INTEGER,
    sender_name     TEXT,
    reply_to        TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    created_at      TEXT NOT NULL,
    sent_at         TEXT,
    clicked_at      TEXT
);

-- Progress updates the hired trade posts, scored against what they promised when quoting.
CREATE TABLE IF NOT EXISTS progress_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    kinds           TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL,
    local_date      TEXT NOT NULL,
    period_daily    TEXT NOT NULL,
    period_weekly   TEXT NOT NULL,
    period_monthly  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id       INTEGER NOT NULL,
    filename        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_reminders (
    job_id          INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    period          TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    PRIMARY KEY (job_id, kind, period)
);

-- Free months earned by trades whose invite brought in a trade who went on to quote.
CREATE TABLE IF NOT EXISTS referral_rewards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id     INTEGER NOT NULL,
    referred_id     INTEGER NOT NULL UNIQUE,
    months          INTEGER NOT NULL DEFAULT 1,
    earned_at       TEXT NOT NULL,
    applied_at      TEXT,
    applied_as      TEXT
);

CREATE INDEX IF NOT EXISTS idx_progress_job     ON progress_updates (job_id, id);
CREATE INDEX IF NOT EXISTS idx_progress_photos  ON progress_photos (update_id);
CREATE INDEX IF NOT EXISTS idx_rewards_referrer ON referral_rewards (referrer_id);
CREATE INDEX IF NOT EXISTS idx_prospects_match  ON prospects (category_id, status);
CREATE INDEX IF NOT EXISTS idx_prospects_email  ON prospects (email);
CREATE INDEX IF NOT EXISTS idx_prospect_sends   ON prospect_sends (prospect_id, job_id);
CREATE INDEX IF NOT EXISTS idx_prospect_queue   ON prospect_sends (status, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_offers_job      ON offers (job_id, status);
CREATE INDEX IF NOT EXISTS idx_offers_trade    ON offers (trade_id, offered_at);
CREATE INDEX IF NOT EXISTS idx_offers_expiry   ON offers (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_quotes_job      ON quotes (job_id);
CREATE INDEX IF NOT EXISTS idx_quotes_trade    ON quotes (trade_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs (status, closes_at);
CREATE TABLE IF NOT EXISTS trade_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    name            TEXT,
    issuer          TEXT,
    reference       TEXT,
    expires_on      TEXT,
    filename        TEXT,
    checked_at      TEXT,
    checked_note    TEXT,
    warned_at       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_referees (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    relationship    TEXT,
    phone           TEXT,
    email           TEXT,
    note            TEXT,
    checked_at      TEXT,
    checked_note    TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_sites (
    job_id          INTEGER PRIMARY KEY,
    address         TEXT,
    access          TEXT,
    parking         TEXT,
    pets            TEXT,
    hazards         TEXT,
    power_water     TEXT,
    notes           TEXT,
    updated_by      INTEGER,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS site_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    author_id       INTEGER NOT NULL,
    body            TEXT NOT NULL,
    shared          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_note_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id         INTEGER NOT NULL,
    filename        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,
    answers         TEXT NOT NULL,
    hazards         TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id        INTEGER NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    description     TEXT NOT NULL,
    qty             REAL,
    unit            TEXT,
    unit_price      REAL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_customer   ON jobs (customer_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (job_id, trade_id);
CREATE INDEX IF NOT EXISTS idx_notes_user      ON notifications (user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_payments_due    ON payments (claim_checked, period_end)
"""


# Columns added after the first release. Each runs once; "already there" is fine.
MIGRATIONS = [
    'ALTER TABLE users ADD COLUMN email_verified_at TEXT',
    'ALTER TABLE users ADD COLUMN email_alerts INTEGER NOT NULL DEFAULT 1',
    'ALTER TABLE users ADD COLUMN unsub_token TEXT',
    'ALTER TABLE users ADD COLUMN closed_at TEXT',
    'ALTER TABLE users ADD COLUMN username TEXT',
    'CREATE UNIQUE INDEX idx_users_username ON users (username)',
    'ALTER TABLE users ADD COLUMN text_alerts INTEGER NOT NULL DEFAULT 1',
    'ALTER TABLE users ADD COLUMN phone_verified_at TEXT',
    'ALTER TABLE users ADD COLUMN referred_by INTEGER',
    'ALTER TABLE trades ADD COLUMN invite_code TEXT',
    'CREATE UNIQUE INDEX idx_trades_invite ON trades (invite_code)',
    'ALTER TABLE notifications ADD COLUMN sms INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE notifications ADD COLUMN texted_at TEXT',
    'ALTER TABLE jobs ADD COLUMN nudged_at TEXT',
    'ALTER TABLE jobs ADD COLUMN followup_at TEXT',
    'ALTER TABLE tokens ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE prospects ADD COLUMN source TEXT',
    'ALTER TABLE prospects ADD COLUMN recommended_by INTEGER',
    'ALTER TABLE users ADD COLUMN ref_code TEXT',
    'CREATE UNIQUE INDEX idx_users_ref_code ON users (ref_code)',
    'ALTER TABLE trades ADD COLUMN report_plan TEXT',
    'ALTER TABLE quotes ADD COLUMN report_plan TEXT',
    'ALTER TABLE jobs ADD COLUMN report_plan TEXT',
    'ALTER TABLE jobs ADD COLUMN work_started_on TEXT',
    'ALTER TABLE jobs ADD COLUMN work_done_on TEXT',
    'ALTER TABLE notifications ADD COLUMN pushed_at TEXT',

    # Profile photo (a face, the way Uber does it) and what we've confirmed about the person.
    'ALTER TABLE trades ADD COLUMN photo TEXT',
    'ALTER TABLE trades ADD COLUMN photo_at TEXT',
    'ALTER TABLE trades ADD COLUMN photo_checked_at TEXT',
    'ALTER TABLE trades ADD COLUMN id_checked_at TEXT',
    'ALTER TABLE trades ADD COLUMN vetting_status TEXT',
    'ALTER TABLE trades ADD COLUMN vetting_at TEXT',
    'ALTER TABLE trades ADD COLUMN vetting_note TEXT',

    # What the public business register showed, and when we looked.
    'ALTER TABLE trades ADD COLUMN nzbn_status TEXT',
    'ALTER TABLE trades ADD COLUMN nzbn_registered_on TEXT',
    'ALTER TABLE trades ADD COLUMN business_note TEXT',
    'ALTER TABLE trades ADD COLUMN business_checked_at TEXT',

    # The trust score, recomputed in the sweep so a page load never waits on it.
    'ALTER TABLE trades ADD COLUMN trust_score INTEGER',
    'ALTER TABLE trades ADD COLUMN trust_at TEXT',

    # Routing: what the matcher made of the job, and when we topped the slots up.
    'ALTER TABLE jobs ADD COLUMN routed_note TEXT',
    'ALTER TABLE jobs ADD COLUMN routed_category_id INTEGER',
    'ALTER TABLE jobs ADD COLUMN routed_at TEXT',
    'ALTER TABLE jobs ADD COLUMN topup_at TEXT',
    'ALTER TABLE jobs ADD COLUMN topup_sent INTEGER NOT NULL DEFAULT 0',

    # Quote line items are optional; a quote with none behaves exactly as before.
    'ALTER TABLE quote_templates ADD COLUMN items TEXT',

    # Filling empty slots from businesses not on Level yet: how many rounds we've
    # done, and how wide we've had to cast.
    'ALTER TABLE jobs ADD COLUMN topup_rounds INTEGER NOT NULL DEFAULT 0',

    # The parts of the trade check that aren't a sentence: the other trades this
    # job may need, and whether the work legally needs a registered person.
    'ALTER TABLE jobs ADD COLUMN routed_extra TEXT',

    # When we last told the customer their job is proving hard to fill.
    'ALTER TABLE jobs ADD COLUMN quiet_told_at TEXT',

    # Sending a won job to the trade's own Docket. The key only ever lets us
    # create a job over there — see docket.py.
    'ALTER TABLE trades ADD COLUMN docket_url TEXT',
    'ALTER TABLE trades ADD COLUMN docket_key TEXT',
    'ALTER TABLE trades ADD COLUMN docket_name TEXT',
    'ALTER TABLE jobs ADD COLUMN docket_at TEXT',
    'ALTER TABLE jobs ADD COLUMN docket_ref TEXT',
    'ALTER TABLE jobs ADD COLUMN docket_error TEXT',
    'ALTER TABLE jobs ADD COLUMN docket_tries INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE trades ADD COLUMN docket_hidden INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE trades ADD COLUMN docket_off INTEGER NOT NULL DEFAULT 0',
]


def hash_password(pw):
    # pbkdf2 explicitly: the default (scrypt) is missing from some Python builds.
    return generate_password_hash(pw, method='pbkdf2:sha256')


def init_db():
    from engine import ts, utcnow
    db = get_db()
    db.executescript(SCHEMA)
    for statement in MIGRATIONS:
        try:
            db.execute(statement)
            db.commit()
        except sqlite3.OperationalError:
            pass                      # the column is already there
    for slug, name, note in config.CATEGORIES:
        db.execute('INSERT OR IGNORE INTO categories (slug, name, licence_note) VALUES (?,?,?)',
                   (slug, name, note))
    for slug, name, region in config.AREAS:
        db.execute('INSERT OR IGNORE INTO areas (slug, name, region) VALUES (?,?,?)',
                   (slug, name, region))

    # Admin accounts, set from the hosting environment. This is the way in when
    # nobody can log in yet — no email delivery needed.
    _ensure_admin(db, 'ADMIN', default_email=None if _USE_PG else 'admin@level.local',
                  default_password=None if _USE_PG else 'admin123')
    _ensure_admin(db, 'ADMIN2', default_password=config.BOOTSTRAP_ADMIN['password'],
                  default_username=config.BOOTSTRAP_ADMIN['username'],
                  default_name=config.BOOTSTRAP_ADMIN['name'])
    _warn_about_leaked_passwords(db)
    db.commit()
    db.close()


def _warn_about_leaked_passwords(db):
    """Shout on every start about any account still using a password that was
    once a default in this source file.

    The repository is public, so those passwords are published. We can't change
    somebody's password for them — that is theirs to do — but silence here would
    let a live admin account sit open indefinitely.
    """
    leaked = getattr(config, 'LEAKED_PASSWORDS', [])
    if not leaked:
        return
    for row in db.execute("SELECT id, email, username, password_hash FROM users "
                          "WHERE role = 'admin' AND closed_at IS NULL").fetchall():
        if any(check_password_hash(row['password_hash'], pw) for pw in leaked):
            who = row['username'] or row['email']
            print('', flush=True)
            print('  ' + '!' * 68, flush=True)
            print(f'  !!  ADMIN "{who}" IS USING A PASSWORD PUBLISHED IN THIS SOURCE CODE.', flush=True)
            print('  !!  Anyone who can read the repository can sign in as an admin.', flush=True)
            print('  !!  Change it in Settings, or remove the account in Admin -> Team.', flush=True)
            print('  ' + '!' * 68, flush=True)
            print('', flush=True)


def _ensure_admin(db, prefix, default_email=None, default_password=None,
                  default_username=None, default_name=None):
    """Create (or update) an admin from environment variables.

    ADMIN_EMAIL / ADMIN_PASSWORD, and the same with an ADMIN2_ prefix, so a
    second person can be given access from the hosting dashboard. Add
    ADMIN_USERNAME to let them log in with a username instead of an email.
    Existing accounts are left alone unless ADMIN_PASSWORD_RESET=1 is set,
    which is the escape hatch for a forgotten password.
    """
    from engine import ts, utcnow
    email = (os.environ.get(f'{prefix}_EMAIL') or default_email or '').strip().lower()
    password = os.environ.get(f'{prefix}_PASSWORD') or default_password
    username = re.sub(r'\s+', ' ', (os.environ.get(f'{prefix}_USERNAME') or default_username or '').strip()).lower() or None
    name = (os.environ.get(f'{prefix}_NAME') or default_name or '').strip() or (username or 'Admin').title()
    reset = os.environ.get(f'{prefix}_PASSWORD_RESET', '') == '1'
    if not password or (not email and not username):
        return
    if not email:                       # a username is enough; the address is just a placeholder
        email = re.sub(r'[^a-z0-9]+', '', username) + '@' + BRAND_SLUG + '.local'

    # Two plain lookups: PostgreSQL rejects a NUL byte used as a placeholder,
    # which crashed startup and left the old version running.
    row = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if not row and username:
        row = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if row and reset:
        db.execute('UPDATE users SET password_hash = ?, role = ?, closed_at = NULL WHERE id = ?',
                   (hash_password(password), 'admin', row['id']))
        print(f'[admin] password reset for {email} (unset {prefix}_PASSWORD_RESET now)', flush=True)
    elif not row:
        db.execute('INSERT INTO users (role, email, username, password_hash, name, created_at) '
                   "VALUES ('admin',?,?,?,?,?)",
                   (email, username, hash_password(password), name, ts(utcnow())))
        print(f'[admin] created admin {username or email}', flush=True)
