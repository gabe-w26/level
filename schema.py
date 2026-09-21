"""
Database schema and seed data.

Timestamps are stored as UTC 'YYYY-MM-DD HH:MM:SS' TEXT and always computed in
Python — never with SQL date functions — so the same queries behave identically
on SQLite (local) and PostgreSQL (production).
"""
import os
import re
import sqlite3

from werkzeug.security import generate_password_hash

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

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS locks (
    name            TEXT PRIMARY KEY,
    holder          TEXT,
    expires_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_offers_job      ON offers (job_id, status);
CREATE INDEX IF NOT EXISTS idx_offers_trade    ON offers (trade_id, offered_at);
CREATE INDEX IF NOT EXISTS idx_offers_expiry   ON offers (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_quotes_job      ON quotes (job_id);
CREATE INDEX IF NOT EXISTS idx_quotes_trade    ON quotes (trade_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs (status, closes_at);
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
    db.commit()
    db.close()


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
