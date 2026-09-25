"""
Tests for the admin Numbers page: the funnel maths, and the rule that a rate is
only shown when there's something to divide by.

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

import app as A  # noqa: E402
import billing  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import metrics  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 24, 0, 0, 0)


class MetricsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.n = 0

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def user(self, role='customer'):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'), f'Person {self.n}',
                               ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, f'u{self.n}@test.nz', 'large', '', '', at=T0)
        self.db.commit()
        return uid

    def job(self, customer, at=T0):
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Deck repair',
            description='Replace rotten boards on the back deck.', value_band='small',
            timing='weeks', property_type='house'), at=at)[0]

    def quote(self, job_id, trade, at):
        engine.submit_quote(self.db, job_id, trade, dict(
            price_type='fixed', amount_low=2000, gst_included=1,
            message='Happy to take this on, two days on site.'), at=at)
        return self.db.execute('SELECT id FROM quotes WHERE job_id = ? AND trade_id = ?',
                               (job_id, trade)).fetchone()['id']

    # ── the funnel ──
    def test_counts_and_rates_across_the_funnel(self):
        customer, trade = self.user(), self.user('trade')
        hired_job = self.job(customer)
        qid = self.quote(hired_job, trade, T0 + timedelta(hours=2))
        engine.accept_quote(self.db, engine.get_job(self.db, hired_job), qid, act_ack=True, at=T0 + timedelta(hours=3))

        dead_job = self.job(customer, at=T0 + timedelta(minutes=1))
        engine.close_job(self.db, engine.get_job(self.db, dead_job), 'not_going_ahead', at=T0 + timedelta(hours=4))

        self.job(customer, at=T0 + timedelta(minutes=2))                      # still open

        m = metrics.jobs(self.db, days=30, at=T0 + timedelta(days=1))
        self.assertEqual(m['posted'], 3)
        self.assertEqual(m['quoted'], 1)
        self.assertEqual(m['hired'], 1)
        self.assertEqual(m['settled'], 2)                                      # hired + closed
        self.assertEqual(m['hire_rate'], 0.5)                                  # 1 of 2 that ended
        self.assertEqual(m['no_quotes'], 1)
        self.assertAlmostEqual(m['hours_to_first_quote'], 2.0, places=1)

    def test_rates_are_none_when_there_is_nothing_to_divide_by(self):
        m = metrics.everything(self.db, days=30, at=T0)
        self.assertIsNone(m['jobs']['hire_rate'])
        self.assertIsNone(m['offers']['quote_rate'])
        self.assertIsNone(m['outreach']['click_rate'])
        self.assertIsNone(m['reporting']['on_time'])
        self.assertEqual(m['jobs']['posted'], 0)

    def test_window_excludes_older_jobs(self):
        customer = self.user()
        self.job(customer, at=T0 - timedelta(days=60))
        self.job(customer, at=T0)
        self.assertEqual(metrics.jobs(self.db, days=30, at=T0)['posted'], 1)
        self.assertEqual(metrics.jobs(self.db, days=None, at=T0)['posted'], 2)

    # ── the tradie side ──
    def test_offer_rates_count_only_finished_offers(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.job(customer)
        self.quote(job_id, trade, T0 + timedelta(hours=1))
        m = metrics.offers(self.db, days=30, at=T0 + timedelta(hours=2))
        self.assertEqual((m['sent'], m['quoted']), (1, 1))
        self.assertEqual(m['quote_rate'], 1.0)

        other = self.user('trade')
        job2 = self.job(customer, at=T0 + timedelta(minutes=5))
        engine.sweep(self.db, at=engine.work_deadline(T0 + timedelta(minutes=5)) + timedelta(minutes=1))
        m = metrics.offers(self.db, days=30, at=T0 + timedelta(days=1))
        self.assertGreater(m['expired'], 0)
        self.assertGreater(m['lapse_rate'], 0)
        self.assertIsNotNone(other and job2)

    def test_trade_counts_track_pausing(self):
        trade = self.user('trade')
        self.assertEqual(metrics.trades(self.db, at=T0)['taking_jobs'], 1)
        engine.set_pause(self.db, trade, True, at=T0)
        m = metrics.trades(self.db, at=T0)
        self.assertEqual((m['taking_jobs'], m['paused']), (0, 1))

    # ── the new sections ──
    def test_trust_counts_are_grouped_not_averaged(self):
        trade = self.user('trade')
        self.db.execute('UPDATE trades SET trust_score = 80 WHERE user_id = ?', (trade,))
        other = self.user('trade')
        self.db.execute('UPDATE trades SET trust_score = 20 WHERE user_id = ?', (other,))
        self.db.commit()
        m = metrics.trust_scores(self.db, at=T0)
        self.assertEqual(m['scored'], 2)
        self.assertEqual(m['median'], 50)
        self.assertEqual(m['bands']['Well checked'], 1)
        self.assertEqual(m['bands']['Just getting started'], 1)

    def test_trust_is_none_rather_than_zero_when_nothing_is_scored(self):
        self.assertIsNone(metrics.trust_scores(self.db, at=T0)['median'])

    def test_routing_counts_only_what_was_actually_checked(self):
        customer = self.user()
        job_id = self.job(customer)
        m = metrics.routing(self.db, at=T0)
        self.assertEqual((m['checked'], m['disagreed']), (0, 0))
        self.assertIsNone(m['disagree_rate'])

        plumber = self.db.execute("SELECT id FROM categories WHERE slug = 'plumber'").fetchone()['id']
        self.db.execute('UPDATE jobs SET routed_at = ?, routed_category_id = ? WHERE id = ?',
                        (ts(T0), plumber, job_id))
        self.db.commit()
        m = metrics.routing(self.db, at=T0)
        self.assertEqual((m['checked'], m['disagreed']), (1, 1))
        self.assertEqual(m['disagree_rate'], 1.0)

    # ── coverage ──
    def test_coverage_counts_only_trades_who_could_actually_be_offered_a_job(self):
        """A trade with no plan, or who paused, wouldn't be there on the day."""
        live = self.user('trade')
        paused = self.user('trade')
        engine.set_pause(self.db, paused, True, at=T0)
        noplan = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                                 "VALUES ('trade','np@test.nz','x','NP',?)", (ts(T0),)).lastrowid
        self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                        (noplan, 'No Plan', ts(T0)))
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (noplan, self.cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (noplan, self.area))
        self.db.commit()

        data = metrics.coverage(self.db, region='Wellington')
        cell = next(c for row in data['rows'] if row['area'] == 'Wellington City'
                    for c in row['cells'] if c['category'] == 'Builder')
        self.assertEqual(cell['n'], 1, 'only the one who could take a job today')
        self.assertIsNotNone(live)

    def test_a_square_is_full_only_at_the_full_slot_count(self):
        for _ in range(config.TRADES_PER_JOB):
            self.user('trade')
        data = metrics.coverage(self.db, region='Wellington')
        cell = next(c for row in data['rows'] if row['area'] == 'Wellington City'
                    for c in row['cells'] if c['category'] == 'Builder')
        self.assertEqual(cell['state'], 'full')
        self.assertGreaterEqual(data['full'], 1)

    def test_the_gaps_are_the_ones_where_jobs_have_actually_come_in(self):
        """An empty square nobody posts in isn't a problem worth a line."""
        customer = self.user()
        self.job(customer)                       # a Builder job in Wellington, no trades
        data = metrics.coverage(self.db, region='Wellington')
        self.assertTrue(data['gaps'], 'a job with nobody to send it to should show up')
        top = data['gaps'][0]
        self.assertEqual((top['area'], top['category'], top['n']), ('Wellington City', 'Builder', 0))
        # And nothing for the 24 trades nobody has posted in.
        self.assertTrue(all(g['jobs'] for g in data['gaps']))

    def test_filtering_by_region_only_shows_that_region(self):
        data = metrics.coverage(self.db, region='Otago')
        self.assertTrue(all(r['region'] == 'Otago' for r in data['rows']))
        self.assertIn('Wellington', data['regions'], 'the switcher still lists them all')

    # ── the page ──
    def test_page_loads_for_admin_only(self):
        admin = self.db.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id").fetchone()
        self.db.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                        (hash_password('password123'), admin['id']))
        self.db.commit()
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        self.assertEqual(c.get('/admin/numbers').status_code, 302)          # signed out
        c.post('/login', data={'email': admin['email'], 'password': 'password123', '_csrf': 't'})
        r = c.get('/admin/numbers')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'hire rate', r.data)
        self.assertEqual(c.get('/admin/numbers?days=all').status_code, 200)


if __name__ == '__main__':
    unittest.main()
