"""
Tests for the business rules in engine.py / billing.py.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)

import billing  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
from engine import RuleError, ts  # noqa: E402
from schema import init_db  # noqa: E402

T0 = datetime(2026, 9, 1, 0, 0, 0)


def hash_password(_):
    return 'test-hash'   # real hashing is deliberately slow; tests don't log in


class RulesTest(unittest.TestCase):

    def setUp(self):
        # These cover the paid model — the guarantee only means something when
        # there's money to refund, so the pilot is switched off here.
        self._pilot = config.FREE_PILOT
        config.FREE_PILOT = False
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        self.db = dbmod.get_db()
        self.n = 0
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']
        self.customer = self.user('customer')

    def tearDown(self):
        config.FREE_PILOT = self._pilot
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def user(self, role):
        self.n += 1
        cur = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('x'), f'User {self.n}', ts(T0)))
        self.db.commit()
        return cur.lastrowid

    def trade(self, tier='large', area=None, subscribe=True, at=T0):
        uid = self.user('trade')
        self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                        (uid, f'Trade {uid}', ts(at)))
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, area or self.area))
        self.db.commit()
        if subscribe:
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, 'x@test.nz', tier, '', '', at=at)
        return uid

    def trades(self, n, **kw):
        return [self.trade(**kw) for _ in range(n)]

    def job(self, band='small', at=T0, property_type='house'):
        return engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Fix deck',
            description='Rotten boards on the back deck need replacing.', value_band=band,
            timing='weeks', property_type=property_type), at=at)

    def offered(self, job_id, status=None):
        sql = 'SELECT trade_id FROM offers WHERE job_id = ?' + (' AND status = ?' if status else '')
        return [r['trade_id'] for r in self.db.execute(sql, (job_id, status) if status else (job_id,)).fetchall()]

    def quote(self, job_id, trade_id, at, amount=1200, **kw):
        q = dict(price_type='fixed', amount_low=amount, gst_included=1,
                 message='Happy to do this — replace boards and re-oil the deck.')
        q.update(kw)
        return engine.submit_quote(self.db, job_id, trade_id, q, at=at)

    # ── distribution ──
    def test_job_goes_to_exactly_15_trades(self):
        self.trades(20)
        job_id, offered = self.job()
        self.assertEqual(offered, config.TRADES_PER_JOB)
        self.assertEqual(len(self.offered(job_id)), 15)

    def test_tiers_stack(self):
        small, medium, large = self.trade('small'), self.trade('medium'), self.trade('large')
        j_small, _ = self.job('small')
        j_medium, _ = self.job('medium')
        j_large, _ = self.job('large')
        self.assertEqual(set(self.offered(j_small)), {small, medium, large})
        self.assertEqual(set(self.offered(j_medium)), {medium, large})
        self.assertEqual(set(self.offered(j_large)), {large})

    def test_unsubscribed_paused_and_other_area_trades_are_skipped(self):
        ok = self.trade()
        self.trade(subscribe=False)
        paused = self.trade()
        engine.set_pause(self.db, paused, True, at=T0)
        other = self.db.execute("SELECT id FROM areas WHERE slug = 'dunedin'").fetchone()['id']
        self.trade(area=other)
        job_id, _ = self.job()
        self.assertEqual(self.offered(job_id), [ok])

    # ── the quote window ──
    def test_window_is_the_configured_hours(self):
        offered = datetime(2026, 9, 24, 21, 0)
        self.assertEqual(engine.work_deadline(offered),
                         offered + timedelta(hours=config.OFFER_WINDOW_HOURS))

    def test_the_clock_runs_overnight_and_at_weekends(self):
        # 9pm Saturday NZ: still a straight four hours, because a customer
        # waiting on quotes doesn't stop waiting on a Sunday.
        saturday_night = datetime(2026, 9, 26, 9, 0)
        self.assertEqual(engine.work_deadline(saturday_night), datetime(2026, 9, 26, 13, 0))
        self.trades(15)
        job_id, _ = self.job(at=saturday_night)
        expires = self.db.execute('SELECT expires_at FROM offers WHERE job_id = ? LIMIT 1',
                                  (job_id,)).fetchone()['expires_at']
        self.assertEqual(expires, ts(datetime(2026, 9, 26, 13, 0)))

    def test_non_quoters_are_replaced_when_their_window_runs_out(self):
        self.trades(40)
        job_id, _ = self.job()
        first = self.offered(job_id)
        quoted = config.MAX_QUOTES - 1          # leave a slot, or the job fills and stops moving
        for t in first[:quoted]:
            self.quote(job_id, t, T0 + timedelta(hours=2))
        engine.sweep(self.db, at=engine.work_deadline(T0) + timedelta(minutes=1))
        self.assertEqual(len(self.offered(job_id, 'expired')), 15 - quoted)
        self.assertEqual(len(self.offered(job_id, 'quoted')), quoted)
        fresh = self.offered(job_id, 'active')
        self.assertEqual(len(fresh), 15 - quoted)
        self.assertFalse(set(fresh) & set(first), 'replacements must be new trades')

    def test_nothing_changes_before_the_window_runs_out(self):
        self.trades(40)
        job_id, _ = self.job()
        engine.sweep(self.db, at=engine.work_deadline(T0) - timedelta(minutes=1))
        self.assertEqual(len(self.offered(job_id, 'active')), 15)
        self.assertEqual(len(self.offered(job_id)), 15)

    def test_passing_on_a_job_hands_the_slot_on_immediately(self):
        self.trades(16)
        job_id, _ = self.job()
        first = self.offered(job_id)
        engine.decline_offer(self.db, job_id, first[0], at=T0 + timedelta(minutes=5))
        self.assertEqual(len(self.offered(job_id, 'active')), 15)
        self.assertEqual(len(self.offered(job_id)), 16)

    def test_customer_gets_at_most_max_quotes_first_in_first_served(self):
        cap = config.MAX_QUOTES
        self.trades(15)
        job_id, _ = self.job()
        ts_ = self.offered(job_id)
        for i, t in enumerate(ts_[:cap]):
            self.assertEqual(self.quote(job_id, t, T0 + timedelta(minutes=10 + i)), i + 1)
        with self.assertRaises(RuleError):
            self.quote(job_id, ts_[cap], T0 + timedelta(minutes=30))
        job = engine.get_job(self.db, job_id)
        self.assertEqual((job['status'], job['quote_count']), ('full', cap))
        self.assertEqual(len(self.offered(job_id, 'active')), 0)
        self.assertEqual(len(self.offered(job_id, 'closed')), 15 - cap)
        # A full job is never redistributed.
        engine.sweep(self.db, at=T0 + timedelta(hours=30))
        self.assertEqual(len(self.offered(job_id)), 15)

    def test_cannot_quote_after_window(self):
        t = self.trade()
        job_id, _ = self.job()
        with self.assertRaises(RuleError):
            self.quote(job_id, t, engine.work_deadline(T0) + timedelta(seconds=1))

    def test_fair_rotation_spreads_jobs_evenly(self):
        pool = self.trades(20)
        for i in range(4):
            self.job(at=T0 + timedelta(hours=i))
        counts = [self.db.execute('SELECT COUNT(*) AS n FROM offers WHERE trade_id = ?', (t,)).fetchone()['n']
                  for t in pool]
        self.assertEqual(sum(counts), 60)
        self.assertLessEqual(max(counts) - min(counts), 1, counts)

    def test_second_job_goes_to_trades_who_missed_the_first(self):
        self.trades(30)
        j1, _ = self.job()
        j2, _ = self.job(at=T0 + timedelta(minutes=1))
        self.assertFalse(set(self.offered(j1)) & set(self.offered(j2)))

    def test_reviews_do_not_affect_who_is_offered(self):
        pool = self.trades(30)
        for t in pool[:15]:   # half the trades have glowing reviews
            self.db.execute('INSERT INTO reviews (job_id, trade_id, customer_id, rating, workmanship, communication, '
                            'timeliness, value_for_money, created_at) VALUES (0,?,?,5,5,5,5,5,?)',
                            (t, self.customer, ts(T0)))
        self.db.commit()
        reviewed_share = 0
        for i in range(10):
            job_id, _ = self.job(at=T0 + timedelta(minutes=i))
            reviewed_share += len(set(self.offered(job_id)) & set(pool[:15]))
        # Rotation gives both halves the same exposure: 150 offers, 75 each.
        self.assertEqual(reviewed_share, 75)

    def test_trades_who_ignore_5_jobs_in_a_row_are_paused(self):
        t = self.trade()
        for i in range(config.AUTO_PAUSE_AFTER):
            self.job(at=T0 + timedelta(hours=i))
        engine.sweep(self.db, at=T0 + timedelta(hours=40))
        self.assertEqual(self.db.execute('SELECT paused FROM trades WHERE user_id = ?', (t,)).fetchone()['paused'], 1)

    # ── building act ──
    def test_30k_residential_quote_needs_contract_docs(self):
        t = self.trade()
        job_id, _ = self.job('medium')
        with self.assertRaises(RuleError):
            self.quote(job_id, t, T0 + timedelta(hours=1), amount=40000)
        self.quote(job_id, t, T0 + timedelta(hours=1), amount=40000, act_docs_promised=1)
        job = engine.get_job(self.db, job_id)
        qid = self.db.execute('SELECT id FROM quotes WHERE job_id = ?', (job_id,)).fetchone()['id']
        with self.assertRaises(RuleError):
            engine.accept_quote(self.db, job, qid, act_ack=False, at=T0 + timedelta(hours=2))
        engine.accept_quote(self.db, job, qid, act_ack=True, at=T0 + timedelta(hours=2))
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'hired')

    def test_commercial_jobs_skip_contract_docs(self):
        t = self.trade()
        job_id, _ = self.job('medium', property_type='commercial')
        self.quote(job_id, t, T0 + timedelta(hours=1), amount=40000)

    # ── guarantee ──
    def _month_with(self, quotes, offers=10, win=False, pause=False):
        t = self.trade()
        jobs = [self.job(at=T0 + timedelta(hours=i))[0] for i in range(offers)]
        if pause:
            engine.set_pause(self.db, t, True, at=T0 + timedelta(days=20))
        for i, job_id in enumerate(jobs[:quotes]):
            self.quote(job_id, t, T0 + timedelta(hours=i, minutes=30))
        if win:
            job = engine.get_job(self.db, jobs[0])
            qid = self.db.execute('SELECT id FROM quotes WHERE job_id = ?', (jobs[0],)).fetchone()['id']
            engine.accept_quote(self.db, job, qid, at=T0 + timedelta(days=3))
        engine.sweep(self.db, at=T0 + timedelta(days=config.DEMO_PERIOD_DAYS, minutes=1))
        return self.db.execute('SELECT * FROM guarantee_claims WHERE trade_id = ?', (t,)).fetchone()

    def test_refund_when_5_quotes_and_no_win(self):
        claim = self._month_with(quotes=5)
        self.assertEqual(claim['status'], 'refunded')      # automatic: nobody has to ask
        self.assertEqual(claim['amount_cents'], 9000)

    def test_no_refund_with_only_4_quotes(self):
        self.assertEqual(self._month_with(quotes=4)['status'], 'not_eligible')

    def test_no_refund_after_winning(self):
        self.assertEqual(self._month_with(quotes=6, win=True)['status'], 'not_eligible')

    def test_requirement_drops_when_few_jobs_offered(self):
        claim = self._month_with(quotes=2, offers=2)
        self.assertEqual((claim['quotes_required'], claim['status']), (2, 'refunded'))

    def test_pausing_keeps_full_requirement(self):
        claim = self._month_with(quotes=2, offers=2, pause=True)
        self.assertEqual((claim['quotes_required'], claim['status']), (5, 'not_eligible'))

    def test_settled_claim_cannot_be_refunded_twice(self):
        claim = self._month_with(quotes=5)                 # already refunded by the sweep
        self.assertEqual(claim['status'], 'refunded')
        with self.assertRaises(RuleError):
            billing.refund_claim(self.db, claim['id'])

    def test_refund_still_works_when_review_is_switched_back_on(self):
        with mock.patch.object(config, 'GUARANTEE_AUTO_APPROVE', False):
            claim = self._month_with(quotes=5)
        self.assertEqual(claim['status'], 'pending')
        billing.refund_claim(self.db, claim['id'])
        self.assertEqual(self.db.execute('SELECT status FROM guarantee_claims WHERE id = ?',
                                         (claim['id'],)).fetchone()['status'], 'refunded')


if __name__ == '__main__':
    unittest.main()
