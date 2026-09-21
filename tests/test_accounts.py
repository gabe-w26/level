"""
Tests for the parts real users need: password reset, closing an account,
the free pilot, editing a job, and revising a quote.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import accounts  # noqa: E402
import app as A  # noqa: E402
import billing  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
from engine import RuleError, ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 1, 0, 0, 0)


class AppTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def user(self, role='customer', email='sam@test.nz', password='password123'):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, phone, created_at) '
                              'VALUES (?,?,?,?,?,?)',
                              (role, email, hash_password(password), 'Sam Walker', '021 123 4567', ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, 'Test Builders', ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
        self.db.commit()
        return uid

    def client(self, email=None, password='password123'):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        if email:
            c.post('/login', data={'email': email, 'password': password, '_csrf': 't'})
            with c.session_transaction() as s:
                s['_csrf'] = 't'
        return c

    def subscribe(self, uid, tier='large', at=T0):
        trade = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
        billing.choose_plan(self.db, trade, 'x@test.nz', tier, '', '', at=at)

    def job(self, customer, band='small', at=T0):
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Fix deck',
            description='Rotten boards on the back deck need replacing.', value_band=band,
            timing='weeks', property_type='house'), at=at)[0]

    def test_cursors_can_be_looped_over(self):
        """Postgres and SQLite cursors must behave the same. `for row in
        db.execute(...)` is used all over the app; on Postgres it needs the
        wrapper's __iter__, and without it trade sign-up broke in production."""
        import db as dbmod_
        self.user('customer', 'loop@test.nz')
        rows = [r for r in self.db.execute('SELECT id FROM users')]
        self.assertTrue(rows)
        self.assertTrue(hasattr(dbmod_._PgCursor, '__iter__'),
                        'the PostgreSQL cursor wrapper must be iterable')

    # ── password reset ──
    def test_reset_link_works_once(self):
        uid = self.user()
        token = accounts.issue(self.db, uid, 'reset', accounts.RESET_HOURS)
        row = accounts.find(self.db, token, 'reset')
        self.assertIsNotNone(row)
        accounts.spend(self.db, row['id'])
        self.assertIsNone(accounts.find(self.db, token, 'reset'), 'a used link must not work twice')

    def test_reset_link_expires(self):
        uid = self.user()
        token = accounts.issue(self.db, uid, 'reset', 2, at=engine.utcnow() - timedelta(hours=3))
        self.assertIsNone(accounts.find(self.db, token, 'reset'))

    def test_reset_sets_new_password_and_logs_in(self):
        uid = self.user()
        token = accounts.issue(self.db, uid, 'reset', accounts.RESET_HOURS)
        c = self.client()
        r = c.post(f'/reset/{token}', data={'password': 'brand-new-password', '_csrf': 't'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client('sam@test.nz', 'brand-new-password').get('/me').status_code, 200)

    def test_wrong_token_shows_expired_page(self):
        self.assertIn(b'run out', self.client().get('/reset/not-a-real-token').data)

    # ── closing an account ──
    def test_closing_scrubs_details_and_blocks_login(self):
        uid = self.user()
        job_id = self.job(uid)
        accounts.close(self.db, self.db.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone())
        row = self.db.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
        self.assertEqual(row['name'], 'Closed account')
        self.assertIsNone(row['phone'])
        self.assertTrue(row['closed_at'])
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'closed')
        r = self.client().post('/login', data={'email': 'sam@test.nz', 'password': 'password123', '_csrf': 't'})
        self.assertNotEqual(r.status_code, 302, 'a closed account must not log in')

    def test_unsubscribe_link_turns_alerts_off(self):
        uid = self.user()
        self.db.execute("UPDATE users SET unsub_token = 'abc123' WHERE id = ?", (uid,))
        self.db.commit()
        self.client().get('/unsubscribe/abc123')
        self.assertEqual(self.db.execute('SELECT email_alerts FROM users WHERE id = ?', (uid,)).fetchone()['email_alerts'], 0)

    # ── free pilot ──
    def test_pilot_plan_costs_nothing_and_still_gets_jobs(self):
        self.assertTrue(config.FREE_PILOT, 'these tests assume the pilot default')
        trade = self.user('trade', 'trade@test.nz')
        self.subscribe(trade)
        payment = self.db.execute('SELECT * FROM payments WHERE trade_id = ?', (trade,)).fetchone()
        self.assertEqual((payment['amount_cents'], payment['status']), (0, 'free'))
        customer = self.user('customer', 'cust@test.nz')
        job_id = self.job(customer)
        self.assertEqual([r['trade_id'] for r in
                          self.db.execute('SELECT trade_id FROM offers WHERE job_id = ?', (job_id,)).fetchall()],
                         [trade])

    def test_pilot_months_raise_no_refund_claims(self):
        trade = self.user('trade', 'trade@test.nz')
        self.subscribe(trade, at=T0)
        engine.sweep(self.db, at=T0 + timedelta(days=config.DEMO_PERIOD_DAYS, minutes=1))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM guarantee_claims').fetchone()['n'], 0)

    # ── editing a job ──
    def test_budget_locks_once_trades_have_the_job(self):
        trade = self.user('trade', 'trade@test.nz')
        self.subscribe(trade)
        customer = self.user('customer', 'cust@test.nz')
        job_id = self.job(customer)
        c = self.client('cust@test.nz')
        c.post(f'/me/jobs/{job_id}/edit', data={
            'title': 'Fix deck and rails', 'description': 'Rotten boards plus the balustrade needs replacing too.',
            'timing': 'asap', 'suburb': 'Ponsonby', 'value_band': 'large', '_csrf': 't'})
        job = engine.get_job(self.db, job_id)
        self.assertEqual(job['title'], 'Fix deck and rails')
        self.assertEqual(job['value_band'], 'small', 'budget must not change after trades were offered it')

    def test_editing_tells_the_trades_holding_it(self):
        trade = self.user('trade', 'trade@test.nz')
        self.subscribe(trade)
        customer = self.user('customer', 'cust@test.nz')
        job_id = self.job(customer)
        before = self.db.execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ?', (trade,)).fetchone()['n']
        self.client('cust@test.nz').post(f'/me/jobs/{job_id}/edit', data={
            'title': 'Fix deck', 'description': 'Rotten boards on the back deck need replacing, about 18 square metres.',
            'timing': 'weeks', 'suburb': 'Ponsonby', '_csrf': 't'})
        after = self.db.execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ?', (trade,)).fetchone()['n']
        self.assertEqual(after, before + 1)

    def test_closed_job_can_be_posted_again(self):
        customer = self.user('customer', 'cust@test.nz')
        job_id = self.job(customer)
        engine.close_job(self.db, engine.get_job(self.db, job_id), 'not_going_ahead')
        self.client('cust@test.nz').post(f'/me/jobs/{job_id}/repost', data={'_csrf': 't'})
        jobs = self.db.execute('SELECT * FROM jobs WHERE customer_id = ? ORDER BY id', (customer,)).fetchall()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1]['status'], 'open')

    # ── revising a quote ──
    def test_quote_can_be_revised_until_the_customer_replies(self):
        trade = self.user('trade', 'trade@test.nz')
        self.subscribe(trade)
        customer = self.user('customer', 'cust@test.nz')
        job_id = self.job(customer)
        job = engine.get_job(self.db, job_id)
        base = dict(price_type='fixed', amount_low=1200, gst_included=1,
                    message='Happy to do this — replace boards and re-oil the deck.')
        engine.submit_quote(self.db, job_id, trade, base, at=T0 + timedelta(hours=1))
        engine.revise_quote(self.db, job, trade, dict(base, amount_low=1400), at=T0 + timedelta(hours=2))
        q = self.db.execute('SELECT * FROM quotes WHERE job_id = ?', (job_id,)).fetchone()
        self.assertEqual(q['amount_low'], 1400)
        engine.accept_quote(self.db, job, q['id'], at=T0 + timedelta(hours=3))
        with self.assertRaises(RuleError):
            engine.revise_quote(self.db, job, trade, dict(base, amount_low=9999), at=T0 + timedelta(hours=4))


if __name__ == '__main__':
    unittest.main()
