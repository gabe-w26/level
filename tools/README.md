# Checking the whole app

Two scripts. Neither is part of the running site; both are for before a deploy.

## `audit.py` — does every page still work?

Four passes, and the static ones have found bugs the dynamic ones didn't:

1. every literal SQL string is prepared against a real, migrated database, which
   catches a column that doesn't exist;
2. every `render_template` target exists and every `url_for` in a template
   resolves;
3. every GET route is fetched as anonymous, customer, trade and admin, and
   anything 5xx is reported;
4. each login is asserted to have landed — a bounced login makes the whole
   sweep meaningless, and it looks exactly like a pass.

**It does not seed.** Give it a database that has been through `seed.run`, or
three quarters of it silently checks nothing:

```
DATABASE=/tmp/audit.db python3 -c "import db, seed; from schema import init_db; init_db(); seed.run(db.get_db())"
DATABASE=/tmp/audit.db python3 tools/audit.py .
```

## `pgcheck.py` — will the SQL work in production?

Production is PostgreSQL and everything local is SQLite. That gap is where this
project's worst bugs have come from: a statement that passes every test and 500s
only for real customers.

With no Postgres on the machine, this takes every literal SQL string, puts it
through `db._adapt_sql` — the exact translation production applies — and parses
the result with libpg_query, which is PostgreSQL's own parser.

```
pip3 install --user pglast
python3 tools/pgcheck.py .
```

It also flags `GROUP BY` statements worth reading (Postgres rejects a selected
column that is neither grouped nor aggregated; SQLite quietly picks one), and
names any statement assembled in Python so someone can check the computed part
is never user input.

What neither can see: whether the numbers are right. That is what `tests/` is for.
