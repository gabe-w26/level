"""
Database abstraction layer.

Returns a connection from get_db() that works identically whether the backend
is SQLite (default / local dev) or PostgreSQL (Render DATABASE_URL env var).

Callers always use SQLite-style ? placeholders and column-name row access —
this module handles the translation transparently.
"""
import datetime
import os
import re
import sqlite3

from flask import g, has_app_context

_DATABASE_URL = os.environ.get('DATABASE_URL', '')
_DATABASE = os.environ.get(
    'DATABASE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
)
_USE_PG = bool(_DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    import psycopg2.pool

    def _build_pool(dsn):
        # Pooled per gunicorn worker process (each worker/thread pool is separate —
        # total connections to Postgres = gunicorn `--workers` x DB_POOL_MAX_CONN).
        # Keep DB_POOL_MAX_CONN modest so `workers x max` stays under Postgres's
        # connection limit; see DEPLOY.md for sizing guidance.
        return psycopg2.pool.ThreadedConnectionPool(
            int(os.environ.get('DB_POOL_MIN_CONN', 1)),
            int(os.environ.get('DB_POOL_MAX_CONN', 8)),
            dsn,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def _pg_startup_help(err, dsn):
        host = re.sub(r'//[^@]*@', '//***@', dsn)  # never print credentials
        print(f'''
[DB] FATAL: could not connect to Postgres at startup.
[DB] URL (redacted): {host}
[DB] Error: {err}
[DB] Checklist:
[DB]  1. On Render, use the *Internal* Database URL for DATABASE_URL
[DB]     (External URLs need ?sslmode=require — tried automatically, still failed).
[DB]  2. Check the Postgres instance page shows status "Available"
[DB]     (free-tier databases expire and get suspended).
[DB]  3. Web service and database must be in the same region for internal URLs.
[DB] Failing fast so the previous working deploy stays live.
''', flush=True)

    _dsn = _DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    try:
        _PG_POOL = _build_pool(_dsn)
    except psycopg2.OperationalError as _e:
        if 'sslmode' not in _dsn:
            # External Render URLs refuse non-SSL connections — retry with SSL
            # before giving up, so a bare external URL still works.
            _dsn_ssl = _dsn + ('&' if '?' in _dsn else '?') + 'sslmode=require'
            try:
                _PG_POOL = _build_pool(_dsn_ssl)
                print('[DB] Connected with sslmode=require (DATABASE_URL had no sslmode set).', flush=True)
            except psycopg2.OperationalError as _e2:
                _pg_startup_help(_e2, _dsn)
                raise
        else:
            _pg_startup_help(_e, _dsn)
            raise


def _sqlite_date_fns_to_pg(sql: str) -> str:
    """Rewrite SQLite's date()/datetime() modifier syntax into PostgreSQL.

    SQLite spells relative dates as extra string arguments — date('now','-7 days').
    PostgreSQL has no such function and raises, so anything using the modifier
    form fails there while working locally on SQLite. Translating centrally keeps
    the call sites in one dialect.

    Bare date('now') would survive as a PostgreSQL cast, but it is normalised to
    CURRENT_DATE too so the emitted SQL reads the same way everywhere. Anything
    whose modifiers are not recognised is returned untouched rather than guessed
    at, so an unhandled form fails loudly instead of silently shifting a date.

    'localtime' maps to CURRENT_DATE rather than a fixed zone: SQLite's
    'localtime' resolves against the server clock, and CURRENT_DATE does too, so
    behaviour is preserved. Both are UTC on Render — see TIMEZONE note in
    DEPLOY.md before changing this.
    """
    unit = r"(year|month|day|hour|minute|second)s?"

    def _shift(base, mods):
        """Fold a list of SQLite modifier literals into PostgreSQL interval maths."""
        expr = base
        for m in mods:
            m = m.strip().strip('\'"')
            if m == 'localtime' or m == 'utc':
                continue
            g = re.fullmatch(r'([+-]?\d+)\s+' + unit, m, re.I)
            if not g:
                return None                      # unrecognised — leave the SQL alone
            expr = "(%s + INTERVAL '%s %s')" % (expr, g.group(1), g.group(2))
        return expr

    def _repl(match):
        fn   = match.group(1).lower()
        args = [a.strip() for a in match.group(2).split(',')]
        if not args or args[0].strip('\'"').lower() != 'now':
            return match.group(0)
        base = 'CURRENT_DATE' if fn == 'date' else 'NOW()'
        rest = args[1:]

        # A placeholder modifier — date('now','localtime',?) — becomes interval maths
        # on the bound value, so the caller can keep passing '-14 days'.
        if any(a == '?' for a in rest):
            fixed = [a for a in rest if a != '?']
            expr  = _shift(base, fixed)
            if expr is None:
                return match.group(0)
            expr = '(%s + (?)::interval)' % expr
        else:
            expr = _shift(base, rest)
            if expr is None:
                return match.group(0)

        # SQLite's date()/datetime() return STRINGS, and this schema stores dates
        # in TEXT columns to match. Handing back a real PostgreSQL date here
        # would compare date against text and fail with a type mismatch, so the
        # result is formatted back to the same string shape SQLite produces.
        # datetime() is the exception: its callers compare against TIMESTAMP
        # columns, where a timestamp is what's wanted.
        if fn == 'date':
            expr = "TO_CHAR(%s, 'YYYY-MM-DD')" % expr
        return expr

    return re.sub(r"\b(date|datetime)\s*\(\s*('now'[^()]*)\)", _repl, sql, flags=re.I)


def _adapt_sql(sql: str) -> str:
    """Convert SQLite SQL dialect to PostgreSQL-compatible SQL."""
    # Date modifiers first — this runs while ? placeholders are still ?
    sql = _sqlite_date_fns_to_pg(sql)
    # ? placeholders → %s
    sql = sql.replace('?', '%s')
    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    if re.match(r'\s*INSERT\s+OR\s+IGNORE\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bINSERT\s+OR\s+IGNORE\b', 'INSERT', sql, count=1, flags=re.IGNORECASE)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
    sql = re.sub(
        r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
        'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE
    )
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    # DEFAULT "value" (SQLite allows this) → DEFAULT 'value' (PostgreSQL requires single quotes)
    sql = re.sub(r'\bDEFAULT\s+"([^"]*)"', r"DEFAULT '\1'", sql)
    return sql


class _Row(dict):
    """dict subclass that also supports positional integer access — matches sqlite3.Row behaviour."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_normalise(row: dict) -> _Row:
    """Convert PostgreSQL datetime/date values to strings and wrap in _Row."""
    out = _Row()
    for k, v in row.items():
        if isinstance(v, datetime.datetime):
            out[k] = v.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(v, datetime.date):
            out[k] = v.strftime('%Y-%m-%d')
        else:
            out[k] = v
    return out


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid
        # sqlite3 cursors expose rowcount; conditional UPDATEs (e.g. the quote
        # cap check) rely on it to know whether the row actually changed.
        self.rowcount = cur.rowcount

    def fetchone(self):
        try:
            row = self._cur.fetchone()
        except psycopg2.ProgrammingError:
            return None
        return _pg_normalise(dict(row)) if row is not None else None

    def fetchall(self):
        try:
            rows = self._cur.fetchall()
        except psycopg2.ProgrammingError:
            return []
        return [_pg_normalise(dict(r)) for r in rows]


class _PgConn:
    """PostgreSQL connection with a SQLite-compatible surface area.

    Normally wraps a connection checked out from the pool and cached on
    flask.g for the duration of a request — `close()` is a no-op in that
    case, since the real release happens once via the teardown_appcontext
    hook registered in app.py (release_db()).

    Code that runs outside any Flask app context (e.g. init_db() at import
    time, before the app has served a single request) has no `g` to cache
    on, so get_db() hands back a `standalone` connection instead — for
    those, `close()` immediately returns the connection to the pool, since
    there's no teardown hook to do it later.
    """

    def __init__(self, conn, standalone=False):
        self._conn = conn
        self._standalone = standalone

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor()
        try:
            # None, not (), when there are no parameters. psycopg2 only applies %
            # interpolation when vars is non-None, so passing an empty tuple made
            # any literal % in the SQL — a LIKE 'Total%' pattern, a date format —
            # raise "tuple index out of range" on PostgreSQL while working on
            # SQLite. Passing None skips interpolation entirely.
            cur.execute(_adapt_sql(sql), params if params else None)
        except (
            psycopg2.errors.DuplicateColumn,
            psycopg2.errors.DuplicateTable,
            psycopg2.errors.UndefinedTable,
        ) as e:
            self._conn.rollback()
            raise sqlite3.OperationalError(str(e))
        except psycopg2.errors.IntegrityError as e:
            # A UNIQUE/FOREIGN KEY/NOT NULL/CHECK violation — real SQLite raises
            # sqlite3.IntegrityError for exactly these, and callers throughout
            # app.py catch that specific type to turn a duplicate username,
            # duplicate vote, etc. into a friendly message instead of a 500.
            # This used to come through as OperationalError like everything
            # else, so every one of those `except sqlite3.IntegrityError`
            # call sites silently never caught anything on Postgres — a
            # duplicate registration crashed with a 500 instead of "already
            # in use" (found via a duplicate-username test, and separately
            # via a concurrent-vote race that hit the same gap).
            self._conn.rollback()
            raise sqlite3.IntegrityError(str(e))
        except psycopg2.Error as e:
            self._conn.rollback()
            raise sqlite3.OperationalError(str(e))

        # Capture the last inserted serial ID so callers can use .lastrowid.
        # Only for INSERTs, and guarded by a savepoint: SELECT lastval() raises
        # when the statement touched no sequence, and in Postgres any error
        # aborts the surrounding transaction — without the savepoint that
        # poisoned the connection and made every following statement fail with
        # "current transaction is aborted".
        lastrowid = None
        if re.match(r'\s*INSERT\b', sql, re.IGNORECASE):
            c2 = self._conn.cursor()
            try:
                c2.execute('SAVEPOINT _lastrowid')
                c2.execute('SELECT lastval()')
                row = c2.fetchone()
                if row:
                    lastrowid = list(row.values())[0]
                c2.execute('RELEASE SAVEPOINT _lastrowid')
            except psycopg2.Error:
                try:
                    c2.execute('ROLLBACK TO SAVEPOINT _lastrowid')
                except psycopg2.Error:
                    pass

        return _PgCursor(cur, lastrowid)

    def executescript(self, sql: str):
        """Execute a multi-statement SQL string, skipping PRAGMA lines."""
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if not stmt or re.match(r'^\s*PRAGMA\b', stmt, re.IGNORECASE):
                continue
            try:
                self.execute(stmt)
                self._conn.commit()
            except sqlite3.OperationalError as e:
                print(f'[DB migration] statement skipped: {e} | SQL: {stmt[:120]}')

    def commit(self):
        self._conn.commit()

    def rollback(self):
        """Present because sqlite3.Connection has it, and code written against
        the SQLite path calls it to abandon a half-finished unit of work."""
        self._conn.rollback()

    def close(self):
        if self._standalone:
            self.release()

    def release(self):
        """Actually return the connection to the pool. Only release_db() (the
        teardown_appcontext hook) or a standalone connection's close() should
        call this."""
        try:
            # A connection that died mid-request is closed rather than pooled,
            # so the next request doesn't pick up the corpse.
            _PG_POOL.putconn(self._conn, close=bool(self._conn.closed))
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass


def _checkout(tries=3):
    """Take a connection from the pool that is actually alive.

    Postgres (and anything between us and it) closes connections that have been
    idle for a while. The pool doesn't notice, so without this check the next
    request gets a dead connection and fails with "connection already closed".
    """
    last = None
    for _ in range(tries):
        conn = _PG_POOL.getconn()
        try:
            if conn.closed:
                raise psycopg2.InterfaceError('connection already closed')
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.close()
            conn.rollback()          # start clean, whatever the last user left behind
            return conn
        except psycopg2.Error as e:
            last = e
            try:
                _PG_POOL.putconn(conn, close=True)   # bin it; the pool opens a fresh one
            except Exception:
                pass
    raise last or psycopg2.OperationalError('could not get a working database connection')


def get_db():
    if _USE_PG:
        if not has_app_context():
            # No request/app context to cache on (e.g. init_db() running at
            # import time, before the app has served anything) — hand back a
            # standalone connection; the caller's own close() releases it.
            return _PgConn(_checkout(), standalone=True)

        cached = getattr(g, '_db_conn', None)
        if cached is not None:
            return cached
        conn = _checkout()
        wrapped = _PgConn(conn)
        g._db_conn = wrapped
        return wrapped

    conn = sqlite3.connect(_DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')    # concurrent readers + one writer
    conn.execute('PRAGMA busy_timeout=5000')   # wait up to 5 s instead of failing
    return conn


def release_db(exception=None):
    """Return this request's pooled Postgres connection to the pool.

    Registered as app.teardown_appcontext(release_db) in app.py so it always
    runs exactly once per request/app-context, even if a handler raised or
    forgot to call db.close(). No-op for SQLite — each call site there opens
    and closes its own short-lived connection, unchanged from before.
    """
    if not _USE_PG:
        return
    conn = g.pop('_db_conn', None)
    if conn is not None:
        conn.release()
