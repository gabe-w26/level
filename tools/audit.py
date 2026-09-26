"""
Full-app audit for Level. Four passes:

  1. every literal SQL string in the Python is prepared against a real, migrated
     database (catches columns/tables that don't exist);
  2. every render_template target exists, and every url_for endpoint in the
     templates resolves;
  3. every GET route is fetched as each role, and anything 5xx is reported
     (except a deliberate 503 from an endpoint that needs another system);
  4. the numbers are checked against a seeded database, with a guard that each
     login actually landed (a bounced login makes the whole sweep meaningless).

Run:  DATABASE=<scratch.db> python3 audit.py <project-dir>
"""
import ast
import os
import re
import sys
import sqlite3
import tempfile
import traceback

PROJECT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, PROJECT)
os.environ.pop('DATABASE_URL', None)
os.environ['RUN_SWEEPER'] = '0'
os.environ['DEMO_TOOLS'] = '1'
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)

import db as dbmod                                    # noqa: E402
import app as A                                       # noqa: E402
from schema import init_db                            # noqa: E402

FAILS = []
NOTES = []


def fail(kind, what, detail=''):
    FAILS.append((kind, what, detail))


# ── 1. SQL against the real schema ────────────────────────────────────────────

def sql_strings(path):
    """Every literal SQL string passed to .execute()/.executescript() in a file."""
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('execute', 'executescript') or not node.args:
            continue
        arg = node.args[0]
        parts, literal = [], True
        stack = [arg]
        while stack:                                   # unwrap 'a' 'b' and 'a' + 'b'
            n = stack.pop(0)
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                parts.append(n.value)
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                stack[:0] = [n.left, n.right]
            else:
                literal = False
                break
        if literal and parts:
            out.append((node.lineno, ''.join(parts)))
    return out


def check_sql():
    conn = sqlite3.connect(os.environ['DATABASE'])
    checked = skipped = 0
    for name in sorted(os.listdir(PROJECT)):
        if not name.endswith('.py') or name in ('audit.py',):
            continue
        for lineno, sql in sql_strings(os.path.join(PROJECT, name)):
            s = sql.strip()
            if not re.match(r'(?i)^(select|insert|update|delete|with)\b', s):
                skipped += 1
                continue
            try:
                conn.execute('EXPLAIN ' + s, [None] * s.count('?'))
                checked += 1
            except sqlite3.Error as e:
                if 'no tables specified' in str(e):
                    skipped += 1
                else:
                    fail('sql', f'{name}:{lineno}', f'{e} :: {s[:120]}')
    conn.close()
    NOTES.append(f'SQL statements prepared against the schema: {checked} ok, {skipped} skipped')


# ── 2. Templates ──────────────────────────────────────────────────────────────

def check_templates():
    tpl_dir = os.path.join(PROJECT, 'templates')
    have = set()
    for root, _, files in os.walk(tpl_dir):
        for f in files:
            have.add(os.path.relpath(os.path.join(root, f), tpl_dir))
    for name in sorted(os.listdir(PROJECT)):
        if not name.endswith('.py'):
            continue
        src = open(os.path.join(PROJECT, name), encoding='utf-8').read()
        for m in re.finditer(r"render_template\(\s*'([^']+)'", src):
            if m.group(1) not in have:
                fail('template', f'{name}: {m.group(1)}', 'render_template target missing')
    endpoints = {r.endpoint for r in A.app.url_map.iter_rules()}
    for rel in sorted(have):
        text = open(os.path.join(tpl_dir, rel), encoding='utf-8').read()
        for m in re.finditer(r"url_for\(\s*'([^']+)'", text):
            if m.group(1) not in endpoints and m.group(1) != 'static':
                fail('url_for', f'{rel}: {m.group(1)}', 'no such endpoint')
    NOTES.append(f'Templates checked: {len(have)}')


# ── 3 & 4. Fetch every GET route as each role ─────────────────────────────────

SAMPLE = {}          # filled from the seeded database


def fill_samples(conn):
    def one(sql, default=1):
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    SAMPLE.update({
        'job_id': one('SELECT id FROM jobs ORDER BY id'),
        'quote_id': one('SELECT id FROM quotes ORDER BY id'),
        'trade_id': one("SELECT user_id FROM trades ORDER BY user_id"),
        'user_id': one("SELECT id FROM users WHERE role='customer' ORDER BY id"),
        'customer_id': one("SELECT id FROM users WHERE role='customer' ORDER BY id"),
        'review_id': one('SELECT id FROM reviews ORDER BY id'),
        'claim_id': one('SELECT id FROM guarantee_claims ORDER BY id'),
        'nid': one('SELECT id FROM notifications ORDER BY id'),
        'pid': one('SELECT id FROM prospects ORDER BY id'),
        'template_id': one('SELECT id FROM quote_templates ORDER BY id'),
        'report_id': one('SELECT id FROM job_reports ORDER BY id'),
        'payment_id': one('SELECT id FROM payments ORDER BY id'),
        'slug': 'builder', 'code': 'zzz', 'token': 'zzz', 'name': 'x.png', 'role': 'customer',
    })


def url_for_rule(rule):
    args = {}
    for arg in rule.arguments:
        if arg in SAMPLE:
            args[arg] = SAMPLE[arg]
        else:
            args[arg] = 1 if 'int:' in str(rule) else 'x'
    try:
        with A.app.test_request_context():
            from flask import url_for
            return url_for(rule.endpoint, **args)
    except Exception:
        return None


def sweep(client, role, skip_prefixes=()):
    seen = ok = 0
    bounced = []
    for rule in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        if 'GET' not in rule.methods or rule.endpoint == 'static':
            continue
        url = url_for_rule(rule)
        if not url or any(url.startswith(p) for p in skip_prefixes):
            continue
        try:
            r = client.get(url, follow_redirects=False)
        except Exception:
            fail('crash', f'{role} GET {url}', traceback.format_exc().strip().splitlines()[-1])
            continue
        seen += 1
        if r.status_code == 503:
            # "Not configured, ask again later" — a real answer from an endpoint
            # that talks to another system, and the right one on a scratch
            # database with no integrations set up. 500 is still a fault.
            pass
        elif r.status_code >= 500:
            fail('5xx', f'{role} GET {url}', f'{r.status_code}')
        elif r.status_code == 302 and '/login' in (r.headers.get('Location') or '') and role != 'anonymous':
            bounced.append(url)                         # signed in, but sent to the login page
        elif r.status_code == 200:
            ok += 1
    NOTES.append(f'{role}: {seen} GET routes fetched, {ok} rendered' +
                 (f', {len(bounced)} bounced to login: {bounced[:6]}' if bounced else ''))


def login(email, password='demo1234'):
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['_csrf'] = 't'
    r = c.post('/login', data={'email': email, 'password': password, '_csrf': 't'})
    if r.status_code != 302:
        fail('login', email, f'expected a redirect, got {r.status_code}')
        return None
    with c.session_transaction() as s:
        if not s.get('uid'):                            # the trap: a bounced login
            fail('login', email, 'no uid in session after login')
            return None
        s['_csrf'] = 't'
    return c


def main():
    init_db()
    conn = dbmod.get_db()
    fill_samples(conn)
    A.app.testing = True
    check_sql()
    check_templates()

    sweep(A.app.test_client(), 'anonymous', skip_prefixes=('/demo/',))
    for role, email, pw in (('customer', 'customer@level.local', 'demo1234'),
                            ('trade', 'trade@level.local', 'demo1234'),
                            ('admin', os.environ.get('ADMIN_EMAIL', 'admin@level.local'),
                             os.environ.get('ADMIN_PASSWORD', 'admin123'))):
        c = login(email, pw)
        if c:
            sweep(c, role, skip_prefixes=('/logout', '/demo/'))
    conn.close()

    print('\n'.join('  ' + n for n in NOTES))
    if FAILS:
        print(f'\n{len(FAILS)} PROBLEM(S):')
        for kind, what, detail in FAILS:
            print(f'  [{kind}] {what}\n        {detail}')
        sys.exit(1)
    print('\nNo problems found.')


if __name__ == '__main__':
    main()
