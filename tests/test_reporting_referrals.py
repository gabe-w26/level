"""
Tests for progress updates (the reporting promise and its scoring) and referrals
(trade invites with free months, customers recommending tradies, share links).

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

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
import mailer  # noqa: E402
import referrals  # noqa: E402
import reporting  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

# Monday 14 September 2026, 9am NZ (UTC+12).
MON = datetime(2026, 9, 13, 21, 0, 0)


def nz(days, hour=9):
    """UTC datetime for `days` after Monday 14 Sept at `hour` NZ time."""
    return MON + timedelta(days=days, hours=hour - 9)


class Base(unittest.TestCase):

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

    def user(self, role='customer', referred_by=None):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, phone, created_at, referred_by) '
                              'VALUES (?,?,?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'), f'Sam Person{self.n}',
                               '021 555 0101', ts(MON), referred_by)).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(MON)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            trade = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, trade, f'u{self.n}@test.nz', 'large', '', '', at=MON)
        self.db.commit()
        return uid

    def job(self, customer, at=MON):
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='New deck',
            description='Build a 20m2 hardwood deck off the lounge.', value_band='small',
            timing='weeks', property_type='house'), at=at)[0]

    def quote(self, job_id, trade, plan=None, at=MON):
        q = dict(price_type='fixed', amount_low=4000, gst_included=1,
                 message='Happy to build this — hardwood on H4 framing, two weeks.')
        if plan is not None:
            q['report_plan'] = plan
        engine.submit_quote(self.db, job_id, trade, q, at=at)
        return self.db.execute('SELECT id FROM quotes WHERE job_id = ? AND trade_id = ?', (job_id, trade)).fetchone()['id']

    def hire(self, plan=('daily', 'weekly'), at=MON):
        customer, trade = self.user(), self.user('trade')
        job_id = self.job(customer, at)
        qid = self.quote(job_id, trade, list(plan), at)
        engine.accept_quote(self.db, engine.get_job(self.db, job_id), qid, act_ack=True, at=at)
        return customer, trade, job_id

    def get_job(self, job_id):
        return engine.get_job(self.db, job_id)

    def client(self, uid=None):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
            if uid:
                s['uid'] = uid
        return c


class ReportingTest(Base):

    def test_plan_is_stored_on_the_quote_and_copied_at_hire(self):
        customer, trade, job_id = self.hire(('weekly', 'daily'))
        job = self.get_job(job_id)
        self.assertEqual(job['report_plan'], 'daily,weekly')
        self.assertEqual(job['work_started_on'], '2026-09-14')
        self.assertEqual(reporting.describe(job['report_plan']), 'Daily and weekly updates')

    def test_no_promise_means_no_score(self):
        customer, trade, job_id = self.hire(())
        self.assertEqual(reporting.score(self.get_job(job_id), [], nz(3)), {})

    def test_daily_counts_working_days_that_are_over(self):
        customer, trade, job_id = self.hire(('daily',))
        job = self.get_job(job_id)
        for d in (0, 1, 3):                            # Mon, Tue, Thu — missed Wednesday
            reporting.post_update(self.db, job, trade, 'Framing going up, all on track.', ['daily'], at=nz(d, 16))
        # The next Monday: Mon–Fri of week one are over; the weekend doesn't count.
        s = reporting.score(job, reporting.updates_for(self.db, job_id), nz(7, 10))['daily']
        self.assertEqual((s['kept'], s['expected']), (3, 5))
        self.assertTrue(s['due'])                      # today's is due and not posted yet

    def test_today_only_counts_once_reported(self):
        customer, trade, job_id = self.hire(('daily',))
        job = self.get_job(job_id)
        s = reporting.score(job, [], nz(0, 10))['daily']
        self.assertEqual((s['kept'], s['expected']), (0, 0))
        reporting.post_update(self.db, job, trade, 'Site set up and materials delivered.', ['daily'], at=nz(0, 11))
        s = reporting.score(job, reporting.updates_for(self.db, job_id), nz(0, 12))['daily']
        self.assertEqual((s['kept'], s['expected']), (1, 1))
        self.assertFalse(s['due'])

    def test_weekly_and_one_update_covering_both(self):
        customer, trade, job_id = self.hire(('daily', 'weekly'))
        job = self.get_job(job_id)
        reporting.post_update(self.db, job, trade, 'Deck frame done; boards next week.', ['daily', 'weekly'], at=nz(4, 15))
        s = reporting.score(job, reporting.updates_for(self.db, job_id), nz(8))
        self.assertEqual((s['weekly']['kept'], s['weekly']['expected']), (1, 1))
        self.assertEqual((s['daily']['kept'], s['daily']['expected']), (1, 6))

    def test_kinds_not_promised_are_ignored(self):
        customer, trade, job_id = self.hire(('weekly',))
        uid = reporting.post_update(self.db, self.get_job(job_id), trade, 'A quick note on progress today.',
                                    ['daily', 'monthly', 'weekly'], at=nz(1))
        self.assertEqual(self.db.execute('SELECT kinds FROM progress_updates WHERE id = ?', (uid,)).fetchone()['kinds'],
                         'weekly')

    def test_only_the_hired_trade_can_post_and_the_customer_is_told(self):
        customer, trade, job_id = self.hire()
        other = self.user('trade')
        with self.assertRaises(reporting.ReportError):
            reporting.post_update(self.db, self.get_job(job_id), other, 'Not my job but here is an update.', [])
        with self.assertRaises(reporting.ReportError):
            reporting.post_update(self.db, self.get_job(job_id), trade, 'Short', [])
        reporting.post_update(self.db, self.get_job(job_id), trade, 'Footings poured and curing nicely.', ['daily'])
        n = self.db.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND body LIKE 'New daily update%'",
                            (customer,)).fetchone()['n']
        self.assertEqual(n, 1)

    def test_finishing_stops_the_clock(self):
        customer, trade, job_id = self.hire(('daily',))
        reporting.finish(self.db, self.get_job(job_id), customer, at=nz(1, 17))       # finished Tuesday
        s = reporting.score(self.get_job(job_id), [], nz(10))['daily']
        self.assertEqual(s['expected'], 2)
        self.assertFalse(s['due'])

    def test_reminder_from_3pm_once_per_period(self):
        customer, trade, job_id = self.hire(('daily',))
        self.assertEqual(reporting.remind(self.db, nz(1, 10)), 0)      # too early
        self.assertEqual(reporting.remind(self.db, nz(1, 15)), 1)
        self.assertEqual(reporting.remind(self.db, nz(1, 17)), 0)      # already reminded today
        reporting.post_update(self.db, self.get_job(job_id), trade, 'Boards are down on half the deck.', ['daily'], at=nz(2, 12))
        self.assertEqual(reporting.remind(self.db, nz(2, 15)), 0)      # already posted today
        self.assertEqual(reporting.remind(self.db, nz(5, 15)), 0)      # Saturday — no daily due

    def test_trade_record_needs_enough_history(self):
        customer, trade, job_id = self.hire(('daily',))
        job = self.get_job(job_id)
        for d in (0, 1, 2):
            reporting.post_update(self.db, job, trade, 'Steady progress, on schedule.', ['daily'], at=nz(d, 16))
        self.assertIsNone(reporting.trade_record(self.db, trade, nz(3, 10)))          # 3 periods: not enough
        rec = reporting.trade_record(self.db, trade, nz(7, 10))                         # 5 periods, 3 kept
        self.assertEqual((rec['kept'], rec['expected'], rec['pct']), (3, 5, 60))

    def test_pages_show_the_promise_and_updates(self):
        customer, trade, job_id = self.hire(('weekly',))
        c = self.client(trade)
        page = c.get(f'/trade/jobs/{job_id}')
        self.assertIn(b'Progress updates', page.data)
        r = c.post(f'/trade/jobs/{job_id}/updates', data={
            '_csrf': 't', 'body': 'Frame is up and square; decking goes on Monday.', 'kinds': 'weekly',
            'photos': (io.BytesIO(b'fake image bytes'), 'deck.jpg')}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM progress_photos').fetchone()['n'], 1)
        page = self.client(customer).get(f'/me/jobs/{job_id}')
        self.assertIn(b'Frame is up and square', page.data)
        self.assertIn(b'They promised', page.data)
        name = self.db.execute('SELECT filename FROM progress_photos').fetchone()['filename']
        os.unlink(os.path.join(A.UPLOAD_DIR, name))

    def test_quote_form_saves_the_plan_and_setup_saves_a_default(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.job(customer, at=engine.utcnow())
        c = self.client(trade)
        self.assertIn(b'name="report_plan"', c.get(f'/trade/jobs/{job_id}').data)
        c.post(f'/trade/jobs/{job_id}/quote', data={'_csrf': 't', 'price_type': 'fixed', 'amount_low': '4000',
                                                   'gst': 'incl', 'message': 'Hardwood deck, two weeks, all included.',
                                                   'report_plan': ['daily', 'monthly']})
        self.assertEqual(self.db.execute('SELECT report_plan FROM quotes WHERE job_id = ?', (job_id,)).fetchone()['report_plan'],
                         'daily,monthly')
        page = self.client(customer).get(f'/me/jobs/{job_id}')
        self.assertIn(b'Daily + Monthly updates', page.data)


class ReferralTest(Base):

    def test_trade_invite_earns_a_free_month_on_first_quote_only(self):
        inviter = self.user('trade')
        newbie = self.user('trade', referred_by=inviter)
        customer = self.user()
        self.quote(self.job(customer), newbie)
        bank = referrals.banked(self.db, inviter)
        self.assertEqual((bank['earned'], bank['waiting']), (1, 1))
        self.quote(self.job(customer), newbie)                         # second quote: nothing more
        self.assertEqual(referrals.banked(self.db, inviter)['earned'], 1)

    def test_customer_referrer_earns_nothing(self):
        customer = self.user()
        newbie = self.user('trade', referred_by=customer)
        self.quote(self.job(self.user()), newbie)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM referral_rewards').fetchone()['n'], 0)

    def test_reward_cap(self):
        inviter = self.user('trade')
        customer = self.user()
        with mock.patch.object(referrals, 'MAX_MONTHS', 2):
            for _ in range(3):
                newbie = self.user('trade', referred_by=inviter)
                self.quote(self.job(customer), newbie)
        self.assertEqual(referrals.banked(self.db, inviter)['earned'], 2)

    def test_banked_month_is_used_by_demo_billing(self):
        inviter = self.user('trade')
        newbie = self.user('trade', referred_by=inviter)
        self.quote(self.job(self.user()), newbie)
        with mock.patch.object(config, 'FREE_PILOT', False), mock.patch.object(config, 'CHARGING', False):
            billing.roll_demo_periods(self.db, MON + timedelta(days=config.DEMO_PERIOD_DAYS + 1))
        pays = self.db.execute('SELECT status, amount_cents FROM payments WHERE trade_id = ? ORDER BY id',
                               (inviter,)).fetchall()
        self.assertEqual((pays[-1]['status'], pays[-1]['amount_cents']), ('referral', 0))
        self.assertEqual(referrals.banked(self.db, inviter)['waiting'], 0)

    def test_customer_share_link_credits_the_friend(self):
        sharer = self.user()
        code = referrals.ref_code(self.db, self.db.execute('SELECT * FROM users WHERE id = ?', (sharer,)).fetchone())
        c = self.client()
        c.get(f'/r/{code}')
        with mock.patch.object(mailer, 'send', return_value=True):
            r = c.post('/post', data={
                '_csrf': 't', 'category': 'builder', 'area': 'wellington', 'suburb': 'Karori', 'title': 'New deck',
                'description': 'Build a 20m2 hardwood deck off the lounge please.', 'value_band': 'small',
                'timing': 'weeks', 'property_type': 'house', 'name': 'Friend Person', 'email': 'friend@test.nz',
                'phone': '021 555 0199', 'password': 'password123'})
        self.assertEqual(r.status_code, 302, r.data[:300])
        friend = self.db.execute("SELECT referred_by FROM users WHERE email = 'friend@test.nz'").fetchone()
        self.assertEqual(friend['referred_by'], sharer)

    def test_recommend_a_tradie_then_they_join(self):
        customer = self.user()
        c = self.client(customer)
        r = c.post('/me/share', data={'_csrf': 't', 'name': 'Dave Builder', 'business_name': 'Dave Builds Ltd',
                                      'category_id': str(self.cat), 'area_id': str(self.area),
                                      'email': 'dave@davebuilds.nz'})
        self.assertIn(b'this link', r.data)
        p = self.db.execute("SELECT * FROM prospects WHERE email = 'dave@davebuilds.nz'").fetchone()
        self.assertEqual((p['recommended_by'], p['source']), (customer, 'customer'))
        # Dave follows the link and signs up.
        d = self.client()
        d.get(f'/o/{p["token"]}')
        with mock.patch.object(mailer, 'send', return_value=True):
            d.post('/signup', data={'_csrf': 't', 'name': 'Dave Builder', 'business_name': 'Dave Builds Ltd',
                                    'email': 'dave@davebuilds.nz', 'phone': '021 555 0123', 'password': 'password123'})
        dave = self.db.execute("SELECT id, referred_by FROM users WHERE email = 'dave@davebuilds.nz'").fetchone()
        self.assertEqual(dave['referred_by'], customer)
        told = self.db.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND body LIKE '%after you recommended%'",
                               (customer,)).fetchone()['n']
        self.assertEqual(told, 1)

    def test_recommending_someone_already_here(self):
        customer, trade = self.user(), self.user('trade')
        email = self.db.execute('SELECT email FROM users WHERE id = ?', (trade,)).fetchone()['email']
        r = self.client(customer).post('/me/share', data={'_csrf': 't', 'name': 'Them', 'category_id': str(self.cat),
                                                          'area_id': str(self.area), 'email': email})
        self.assertIn(b'already on', r.data)

    def test_recommendation_email_goes_once_with_an_opt_out(self):
        integrations._cache.update(at=10 ** 12, values={'smtp_host': 'smtp.test'})
        customer = self.user()
        me = self.db.execute('SELECT * FROM users WHERE id = ?', (customer,)).fetchone()
        p, _ = referrals.recommend(self.db, me, {'name': 'Dave', 'category_id': self.cat, 'area_id': self.area,
                                                 'email': 'dave@x.nz'})
        with mock.patch.object(referrals.threading, 'Thread') as thread:
            self.assertTrue(referrals.send_invite(self.db, p, me))
            kwargs = thread.call_args[1]['kwargs']
            self.assertTrue(kwargs['unsubscribe_url'].endswith('/stop'))
            p = self.db.execute('SELECT * FROM prospects WHERE id = ?', (p['id'],)).fetchone()
            self.assertFalse(referrals.send_invite(self.db, p, me))     # never twice

    def test_pages_load(self):
        trade, customer, admin = self.user('trade'), self.user(), self.db.execute(
            "SELECT id FROM users WHERE role = 'admin'").fetchone()['id']
        self.assertEqual(self.client(trade).get('/trade/referrals').status_code, 200)
        self.assertEqual(self.client(customer).get('/me/share').status_code, 200)
        self.assertEqual(self.client(admin).get('/admin/referrals').status_code, 200)


if __name__ == '__main__':
    unittest.main()
