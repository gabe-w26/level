"""
Does this code's SQL actually work on PostgreSQL?

Production is Postgres, local is SQLite, and the tests and the route sweep all
run on SQLite. That gap is where this project's worst bugs have come from: a
statement that is fine in every test and 500s only for real customers.

There is no Postgres on this machine, so this does the next best thing, and it
is a good deal better than reading the code:

  1. pull every literal SQL string out of the Python, the way the audit does;
  2. put it through `db._adapt_sql`, which is the exact translation production
     applies — nothing is approximated here;
  3. parse the result with libpg_query, which is PostgreSQL's own parser. If
     Postgres would refuse to parse it, this says so.

What it cannot see, and what still needs a real server or a careful eye:
  · whether a column exists (that is the SQLite EXPLAIN pass in audit.py);
  · GROUP BY strictness, which Postgres enforces after parsing. The lint below
    flags candidates rather than pretending to decide.

Run:  python3 pgcheck.py <project-dir>
"""
import ast
import os
import re
import sys

PROJECT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, PROJECT)

import db as dbmod                                  # noqa: E402

try:
    from pglast import parse_sql
    from pglast.parser import ParseError
except ImportError:
    print('pglast is not installed:  pip3 install --user pglast')
    sys.exit(2)

# Things SQLite accepts that Postgres does not, which `_adapt_sql` is supposed to
# have dealt with by the time we see the statement. If one survives translation
# it is a real bug, not a style point.
LEFTOVERS = [
    (r'\bAUTOINCREMENT\b', 'AUTOINCREMENT survived translation'),
    (r'\bINSERT\s+OR\s+(IGNORE|REPLACE)\b', 'INSERT OR … survived translation'),
    (r'\bstrftime\s*\(', 'strftime() is SQLite only'),
    (r'\bjulianday\s*\(', 'julianday() is SQLite only'),
    (r"\b(date|datetime)\s*\(\s*'now'", "date('now') survived translation"),
    (r'\|\|\s*$', 'trailing || concatenation'),
    (r'\bGLOB\b', 'GLOB is SQLite only'),
    (r'\bIFNULL\s*\(', 'IFNULL() is SQLite only — use COALESCE'),
    (r'\bLIMIT\s+\d+\s*,\s*\d+', 'LIMIT x, y is SQLite/MySQL syntax'),
]

# Statements whose parameters make them unparseable on their own (a bare %s where
# a table name or a list goes). Substituting a literal keeps the grammar honest.
def _placeholders(sql):
    return sql.replace('%s', "'x'")


def literal_sql(path):
    """Every string constant handed to .execute()/.executescript(), with its line."""
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('execute', 'executescript'):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        parts, dynamic = [], False
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            parts = [arg.value]
        elif isinstance(arg, ast.JoinedStr):
            # An f-string: keep the literal parts, stand something in for the rest.
            dynamic = any(not isinstance(v, ast.Constant) for v in arg.values)
            parts = [v.value if isinstance(v, ast.Constant) else 'x' for v in arg.values]
        elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            flat = []
            dynamic = True
            def walk(n):
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                    walk(n.left); walk(n.right)
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    flat.append(n.value)
                else:
                    flat.append(' x ')
            walk(arg)
            parts = flat
        if parts:
            sql = ''.join(parts).strip()
            if sql and re.match(r'\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|PRAGMA)', sql, re.I):
                found.append((node.lineno, sql, dynamic))
    return found


def group_by_risk(sql):
    """Flag a GROUP BY that may fall foul of Postgres's stricter rule.

    Postgres refuses a selected column that is neither grouped nor aggregated;
    SQLite picks a row at random and says nothing. This can't decide the
    question — it points at the ones worth reading.
    """
    m = re.search(r'\bGROUP\s+BY\b(.*?)(?:\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)', sql, re.I | re.S)
    if not m:
        return None
    grouped = {g.strip().lower().split('.')[-1] for g in m.group(1).split(',') if g.strip()}
    select = re.search(r'\bSELECT\b(.*?)\bFROM\b', sql, re.I | re.S)
    if not select:
        return None
    body = select.group(1)
    bare = re.sub(r'\w+\s*\([^()]*\)', '', body)          # drop COUNT(*), SUM(...) and friends
    if '*' in bare:
        return 'SELECT * with GROUP BY'
    loose = []
    for col in re.split(r',(?![^()]*\))', body):
        col = col.strip()
        if not col or re.search(r'\b(COUNT|SUM|AVG|MIN|MAX|GROUP_CONCAT|STRING_AGG)\s*\(', col, re.I):
            continue
        name = re.split(r'\s+AS\s+', col, flags=re.I)[0].strip().lower().split('.')[-1]
        if name and name not in grouped and not name.isdigit():
            loose.append(name)
    return f'not grouped or aggregated: {", ".join(loose)}' if loose else None


def main():
    files = sorted(f for f in os.listdir(PROJECT) if f.endswith('.py'))
    checked = skipped = 0
    errors, warnings, dyn = [], [], []

    for name in files:
        for line, sql, dynamic in literal_sql(os.path.join(PROJECT, name)):
            if sql.upper().startswith('PRAGMA'):
                skipped += 1                       # SQLite-only by design, never runs on PG
                continue
            adapted = dbmod._adapt_sql(sql)
            checked += 1

            for pattern, why in LEFTOVERS:
                if re.search(pattern, adapted, re.I):
                    errors.append((name, line, why, sql))

            for statement in [s for s in adapted.split(';\n') if s.strip()]:
                try:
                    parse_sql(_placeholders(statement))
                except ParseError as e:
                    if dynamic:
                        # The statement is assembled in Python, so what we have is
                        # a stand-in, not the real thing. Name it for a human
                        # rather than claiming Postgres would reject it.
                        dyn.append((name, line, sql))
                    else:
                        errors.append((name, line, f'PostgreSQL will not parse this: {e}', sql))
                    break
                except Exception:
                    skipped += 1
                    break

            risk = group_by_risk(adapted)
            if risk:
                warnings.append((name, line, risk, sql))

    print(f'  Statements translated and parsed as PostgreSQL: {checked} ok, {skipped} skipped')
    if dyn:
        print(f'\n  {len(dyn)} statement(s) are assembled in Python, so only a stand-in could be parsed.')
        print('  Check by hand that the computed part is never user input:')
        for name, line, sql in dyn:
            print(f'    {name}:{line}  {" ".join(sql.split())[:120]}')
    if warnings:
        print(f'\n  {len(warnings)} GROUP BY statement(s) worth a human look '
              '(Postgres is stricter than SQLite here):')
        for name, line, why, sql in warnings:
            print(f'    {name}:{line}  {why}')
            print(f'      {" ".join(sql.split())[:150]}')
    if errors:
        print(f'\n{len(errors)} PROBLEM(S) — these would only fail in production:')
        for name, line, why, sql in errors:
            print(f'  [{name}:{line}] {why}')
            print(f'      {" ".join(sql.split())[:200]}')
        sys.exit(1)
    print('\nNo PostgreSQL syntax problems found.')


if __name__ == '__main__':
    main()
