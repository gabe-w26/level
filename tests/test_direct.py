"""
Asking one business by name.

A homeowner who already likes a tradie shouldn't have to put the job out to
fifteen strangers to reach them. The rules that matter: nobody else sees it, the
chosen business gets longer because there's no race, and what happens when they
don't answer is the customer's choice, not ours.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import timedelta

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
from engine import RuleError, ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402


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
        self.favourite = self.user('trade', 'Karori Building')
        self.others = [self.user('trade', f'Other {i}') for i in range(4)]
        self.customer = self.user('customer', 'Sam')

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer', name='Person'):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'), name,
                               ts(utcnow()))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, name, ts(utcnow())))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, f'u{self.n}@test.nz', 'large', '', '')
        self.db.commit()
        return uid

    def ask(self, trade_id=None, fallback=True, at=None):
        return engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Rebuild the back steps',
            description='Four treads, treated pine, handrail one side.', value_band='medium',
            timing='weeks', property_type='house'), at=at or utcnow(),
            direct_trade_id=trade_id if trade_id is not None else self.favourite,
            direct_fallback=fallback)[0]

    def holders(self, job_id):
        return sorted(r['business_name'] for r in self.db.execute(
            "SELECT t.business_name FROM offers o JOIN trades t ON t.user_id = o.trade_id "
            "WHERE o.job_id = ? AND o.status = 'active'", (job_id,)))

    def client(self, uid):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = uid
            s['_csrf'] = 't'
        return c


class AskingTest(Base):

    def test_only_the_business_they_asked_for_gets_it(self):
        job_id = self.ask()
        self.assertEqual(self.holders(job_id), ['Karori Building'])

    def test_they_get_longer_because_nobody_is_racing_them(self):
        job_id = self.ask()
        offer = self.db.execute('SELECT offered_at, expires_at FROM offers WHERE job_id = ?',
                                (job_id,)).fetchone()
        hours = (engine.parse_ts(offer['expires_at']) - engine.parse_ts(offer['offered_at'])
                 ).total_seconds() / 3600
        self.assertAlmostEqual(hours, config.DIRECT_WINDOW_HOURS, places=1)
        self.assertGreater(config.DIRECT_WINDOW_HOURS, config.OFFER_WINDOW_HOURS)

    def test_the_sweep_does_not_quietly_hand_it_to_anyone_else(self):
        job_id = self.ask()
        engine.sweep(self.db, at=utcnow() + timedelta(minutes=5))
        self.assertEqual(self.holders(job_id), ['Karori Building'])

    def test_they_are_told_they_were_asked_for_by_name(self):
        self.ask()
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.favourite,)).fetchone()['body']
        self.assertIn('asked for you by name', note)
        self.assertIn('nobody else', note)

    def test_a_paused_business_cannot_be_asked_and_says_why(self):
        engine.set_pause(self.db, self.favourite, True)
        self.db.commit()
        ok, why = engine.can_be_asked(self.db, self.favourite)
        self.assertFalse(ok)
        self.assertIn('paused', why)
        with self.assertRaises(RuleError):
            self.ask()

    def test_a_business_with_no_plan_cannot_be_asked(self):
        self.db.execute("UPDATE trades SET sub_status = 'cancelled' WHERE user_id = ?", (self.favourite,))
        self.db.commit()
        ok, why = engine.can_be_asked(self.db, self.favourite)
        self.assertFalse(ok)
        self.assertIn('take jobs', why)

    def test_a_closed_account_cannot_be_asked(self):
        self.db.execute('UPDATE users SET closed_at = ? WHERE id = ?', (ts(utcnow()), self.favourite))
        self.db.commit()
        self.assertFalse(engine.can_be_asked(self.db, self.favourite)[0])


class WhenTheyDoNotAnswerTest(Base):

    def later(self):
        return utcnow() + timedelta(hours=config.DIRECT_WINDOW_HOURS + 1)

    def test_it_opens_to_everyone_if_that_is_what_the_customer_chose(self):
        job_id = self.ask(fallback=True)
        engine.sweep(self.db, at=self.later())
        self.assertEqual(len(self.holders(job_id)), 4, 'the other four builders')
        self.assertNotIn('Karori Building', self.holders(job_id))

    def test_the_customer_is_told_it_moved_on(self):
        self.ask(fallback=True)
        engine.sweep(self.db, at=self.later())
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.customer,)).fetchone()['body']
        self.assertIn('Karori Building', note)
        self.assertIn('other local trades', note)

    def test_only_them_means_only_them(self):
        """Offering it round would be the opposite of what they asked for."""
        job_id = self.ask(fallback=False)
        engine.sweep(self.db, at=self.later())
        self.assertEqual(self.holders(job_id), [])
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'closed')

    def test_it_is_only_opened_once(self):
        job_id = self.ask(fallback=True)
        for i in range(4):
            engine.sweep(self.db, at=self.later() + timedelta(hours=i))
        self.assertIsNotNone(engine.get_job(self.db, job_id)['direct_opened_at'])
        self.assertLessEqual(len(self.holders(job_id)), 4)

    def test_a_job_they_did_quote_on_is_left_alone(self):
        job_id = self.ask(fallback=True)
        engine.submit_quote(self.db, job_id, self.favourite, dict(
            price_type='fixed', amount_low=2400, gst_included=1,
            message='Half a day, treated pine, handrail included.'))
        engine.sweep(self.db, at=self.later())
        self.assertIsNone(engine.get_job(self.db, job_id)['direct_opened_at'],
                          'they answered, so there is nothing to fall back from')


class FromTheProfileTest(Base):

    def test_the_button_remembers_who_and_the_form_says_so(self):
        c = self.client(self.customer)
        r = c.get(f'/pros/{self.favourite}/ask')
        self.assertEqual(r.status_code, 302)
        page = c.get('/post').data.decode()
        self.assertIn('You’re asking Karori Building to quote', page)
        self.assertIn('nobody else', page)

    def test_asking_an_unavailable_business_says_so_and_still_lets_them_post(self):
        engine.set_pause(self.db, self.favourite, True)
        self.db.commit()          # set_pause leaves the commit to its caller
        c = self.client(self.customer)
        r = c.get(f'/pros/{self.favourite}/ask', follow_redirects=True)
        body = r.data.decode()
        self.assertIn('paused', body)
        self.assertNotIn('You’re asking', body, 'it should not pretend the request went through')

    def test_the_profile_offers_the_button(self):
        page = A.app.test_client().get(f'/pros/{self.favourite}').data.decode()
        self.assertIn('Ask Karori Building to quote', page)


if __name__ == '__main__':
    unittest.main()
