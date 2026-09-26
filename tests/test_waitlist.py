"""
Shutting the front door, and the things that must survive it.

The waitlist is easy; the risk is everything around it. Two families of test
matter more than the feature itself:

  · **Nobody already in is locked out.** A trade mid-job keeps quoting, a
    customer keeps hiring, admin keeps working. Closing the door to new people
    must not touch the people who are the only reason there's anything to
    protect.
  · **It's genuinely off by default.** Every other test file in this project
    posts jobs and signs people up. If the switch defaulted on, they'd all go
    red — and worse, a fresh deploy would silently stop taking business.

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
os.environ.pop('WAITLIST', None)          # the env var would override the switch
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import app as A  # noqa: E402
import billing  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import waitlist as wl  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        A._login_failures.clear()
        self.db = dbmod.get_db()
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.spark = self.db.execute("SELECT id FROM categories WHERE slug = 'electrician'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.other_area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']
        self.n = 0
        self.shut(False)

    def tearDown(self):
        A._login_failures.clear()
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def shut(self, on=True):
        """Turn the front door off or on, the way Admin → Setup does."""
        integrations._cache.update(at=10 ** 12, values={'waitlist': '1' if on else ''})

    def form(self, **over):
        data = {'side': 'customer', 'email': f'sam{self.n}@test.nz', 'name': 'Sam',
                'area': str(self.area)}
        self.n += 1
        data.update(over)
        return data

    def user(self, role='customer', email=None):
        self.n += 1
        email = email or f'u{self.n}@test.nz'
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              (role, email, hash_password('password123'), 'Person', ts(utcnow()))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(utcnow())))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, email, 'large', '', '')
        self.db.commit()
        return uid

    def client(self, uid=None):
        # The CSRF token goes in even for an anonymous client, so a POST reaches
        # the waitlist gate instead of being turned away at the CSRF check —
        # otherwise these tests would pass on the wrong refusal.
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
            if uid:
                s['uid'] = uid
        return c


class TheSwitchTest(Base):

    def test_it_is_off_unless_somebody_turns_it_on(self):
        """A fresh Level takes business. It does not quietly stop."""
        integrations._cache.update(at=10 ** 12, values={})
        self.assertFalse(wl.is_on())

    def test_turning_it_on_is_a_setting_not_a_deploy(self):
        self.shut(True)
        self.assertTrue(wl.is_on())
        self.shut(False)
        self.assertFalse(wl.is_on())


class TheSwitchInAdminTest(Base):
    """One button, both ways.

    This started life as a text box in Admin → Setup, which was wrong: that form
    is for credentials and only saves what you type into it, so you could switch
    the waitlist on there and never switch it off. The bug reached production.
    """

    def setUp(self):
        super().setUp()
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        self.admin = row['id'] if row else self.user('admin')

    def flip(self, on):
        return self.client(self.admin).post('/admin/waitlist/switch',
                                            data={'on': '1' if on else '0', '_csrf': 't'},
                                            follow_redirects=True)

    def test_one_click_shuts_it(self):
        self.assertEqual(self.flip(True).status_code, 200)
        import integrations as ig
        ig.refresh(self.db, force=True)
        self.assertTrue(wl.is_on())
        self.assertEqual(self.client().get('/post').status_code, 302)

    def test_one_click_opens_it_again(self):
        """The half that the Setup box could not do."""
        self.flip(True)
        self.flip(False)
        import integrations as ig
        ig.refresh(self.db, force=True)
        self.assertFalse(wl.is_on())
        self.assertEqual(self.client().get('/post').status_code, 200)

    def test_it_is_not_a_get(self):
        """A link that shuts the shop is one stray crawler away from a bad day."""
        self.assertEqual(self.client(self.admin).get('/admin/waitlist/switch').status_code, 405)

    def test_only_an_admin_can_touch_it(self):
        for who in (None, self.user('customer'), self.user('trade')):
            r = self.client(who).post('/admin/waitlist/switch', data={'on': '1', '_csrf': 't'})
            self.assertIn(r.status_code, (302, 403, 404), 'not for anybody else')
        import integrations as ig
        ig.refresh(self.db, force=True)
        self.assertFalse(wl.is_on())

    def test_the_credentials_form_no_longer_pretends_to_own_it(self):
        """It couldn't turn it off, so it shouldn't offer to turn it on."""
        names = [n for _, _, fields in A.SETUP_GROUPS for n, _, _ in fields]
        self.assertNotIn('waitlist', names)


class HealthTellsTheTruthTest(Base):
    """So "is it actually on in production?" has an answer you can curl.

    Without this the only way to know was to log in as admin, and the two
    failure modes — the setting never saved, versus saved but the gate not
    firing — looked identical from outside.
    """

    def health(self):
        return self.client().get('/health').get_json()

    def test_it_reports_off_when_it_is_off(self):
        h = self.health()
        self.assertFalse(h['waitlist'])
        self.assertFalse(h['waitlist_stored'])

    def test_it_reports_on_and_stored_when_it_is_on(self):
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        admin = row['id'] if row else self.user('admin')
        self.client(admin).post('/admin/waitlist/switch', data={'on': '1', '_csrf': 't'})
        import integrations as ig
        ig.refresh(self.db, force=True)
        h = self.health()
        self.assertTrue(h['waitlist'], 'the gate agrees')
        self.assertTrue(h['waitlist_stored'], 'and it survived being written down')


class TheDoorTest(Base):

    def test_with_it_off_the_normal_doors_work(self):
        c = self.client()
        self.assertEqual(c.get('/post').status_code, 200)
        self.assertEqual(c.get('/signup').status_code, 200)

    def test_with_it_on_a_visitor_is_sent_to_the_waitlist(self):
        self.shut(True)
        c = self.client()
        for path, side in (('/post', 'customer'), ('/signup', 'trade')):
            r = c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn('/join', r.headers['Location'])
            self.assertIn(side, r.headers['Location'], 'the form should already know which they are')

    def test_posting_a_job_is_refused_not_just_hidden(self):
        """A link straight to the form, or a stale tab, must not get through."""
        self.shut(True)
        r = self.client().post('/post', data={'_csrf': 't', 'title': 'Rebuild the back steps'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/join', r.headers['Location'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM jobs').fetchone()['n'], 0)

    def test_signing_up_is_refused_not_just_hidden(self):
        self.shut(True)
        before = self.db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        r = self.client().post('/signup', data={'_csrf': 't', 'email': 'new@test.nz',
                                                'password': 'password123', 'business_name': 'New Co'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'], before)

    def test_the_front_page_asks_for_an_email_instead(self):
        self.shut(True)
        page = self.client().get('/').data.decode()
        self.assertIn('/join', page)
        self.assertNotIn('Post a job — free', page)

    def test_no_page_still_invites_a_visitor_through_a_shut_door(self):
        """A nav or footer link saying "Post a job" while the door is shut sends
        somebody to a redirect and makes us look broken."""
        self.shut(True)
        for path in ('/', '/pricing', '/and-docket', '/join'):
            page = self.client().get(path).data.decode()
            self.assertNotIn('>Post a job<', page, path)
            self.assertNotIn('>Join as a trade<', page, path)


class NobodyAlreadyInIsLockedOutTest(Base):
    """The whole risk of this feature, in one class."""

    def setUp(self):
        super().setUp()
        self.trade = self.user('trade')
        self.customer = self.user('customer')
        self.shut(True)

    def test_a_customer_with_an_account_can_still_post(self):
        self.assertEqual(self.client(self.customer).get('/post').status_code, 200)

    def test_a_trade_can_still_reach_their_dashboard(self):
        self.assertEqual(self.client(self.trade).get('/trade').status_code, 200)

    def test_a_job_and_a_quote_still_work_end_to_end(self):
        now = utcnow()
        job_id = engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori',
            title='Replace rotten deck boards', description='Twelve square metres, boards are soft.',
            value_band='medium', timing='weeks', property_type='house'), at=now)[0]
        engine.submit_quote(self.db, job_id, self.trade, dict(
            price_type='fixed', amount_low=6400, gst_included=1,
            message='Two days on site, timber supplied and the old stuff taken away.'),
            at=now + timedelta(minutes=1))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM quotes').fetchone()['n'], 1)

    def test_logging_in_still_works(self):
        self.assertEqual(self.client().get('/login').status_code, 200)

    def test_admin_still_works(self):
        admin = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if admin:
            self.assertEqual(self.client(admin['id']).get('/admin/waitlist').status_code, 200)


class JoiningTest(Base):

    def latest(self):
        return self.db.execute('SELECT * FROM waitlist ORDER BY id DESC LIMIT 1').fetchone()

    def test_a_homeowner_can_join(self):
        wl.join(self.db, self.form())
        row = self.latest()
        self.assertEqual(row['side'], 'customer')
        self.assertEqual(row['area_id'], self.area)
        self.assertIsNone(row['category_id'], 'a homeowner picking a trade would be noise')

    def test_a_tradie_has_to_say_what_they_do(self):
        with self.assertRaises(wl.WaitlistError):
            wl.join(self.db, self.form(side='trade', category=''))
        wl.join(self.db, self.form(side='trade', category=str(self.cat)))
        self.assertEqual(self.latest()['category_id'], self.cat)

    def test_joining_twice_updates_rather_than_duplicating(self):
        wl.join(self.db, self.form(email='sam@test.nz'))
        out = wl.join(self.db, self.form(email='Sam@Test.NZ', name='Sam Walker',
                                         area=str(self.other_area)))
        self.assertTrue(out['again'], 'and it says so, rather than pretending it was new')
        rows = self.db.execute('SELECT * FROM waitlist').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['area_id'], self.other_area, 'the newer answer wins')

    def test_the_same_person_can_be_on_both_sides(self):
        """A builder who also owns a house is two different waits."""
        wl.join(self.db, self.form(email='both@test.nz', side='customer'))
        wl.join(self.db, self.form(email='both@test.nz', side='trade', category=str(self.cat)))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM waitlist').fetchone()['n'], 2)

    def test_rubbish_is_refused(self):
        for bad in ({'email': 'not-an-email'}, {'email': ''}, {'side': 'wizard'},
                    {'area': ''}, {'area': '99999'}):
            with self.assertRaises(wl.WaitlistError, msg=bad):
                wl.join(self.db, self.form(**bad))
        self.assertIsNone(self.latest())

    def test_the_form_works_over_http(self):
        self.shut(True)
        r = self.client().post('/join', data=dict(self.form(), _csrf='t'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('You’re on the list', r.data.decode())
        self.assertIsNotNone(self.latest())

    def test_the_page_still_works_after_the_waitlist_is_turned_off(self):
        """A link in an email shouldn't 404 the week after you open."""
        page = self.client().get('/join').data.decode()
        self.assertIn('is open', page)
        self.assertIn('/post', page)


class WhatPeopleAreToldTest(Base):

    def test_it_counts_the_other_side_not_just_their_own(self):
        for i in range(3):
            wl.join(self.db, self.form(side='trade', email=f't{i}@test.nz', category=str(self.cat)))
        out = wl.join(self.db, self.form(side='customer', email='sam@test.nz'))
        self.assertEqual(out['nearby']['trades'], 3, 'a homeowner wants to know about tradies')
        self.assertEqual(out['nearby']['customers'], 1)

    def test_trades_who_already_pay_count_as_supply(self):
        """They're cover whether they came through the waitlist or before it."""
        self.user('trade')
        out = wl.join(self.db, self.form(side='customer'))
        self.assertEqual(out['nearby']['trades'], 1)

    def test_another_area_does_not_count(self):
        wl.join(self.db, self.form(side='trade', email='t@test.nz', category=str(self.cat),
                                   area=str(self.other_area)))
        out = wl.join(self.db, self.form(side='customer'))
        self.assertEqual(out['nearby']['trades'], 0, 'a plumber in Auckland is no use in Karori')

    def test_it_says_how_far_off_the_area_is(self):
        out = wl.join(self.db, self.form(side='customer'))
        n = out['nearby']
        self.assertFalse(n['ready'])
        self.assertEqual(n['needs_trades'], wl.READY_TRADES)
        self.assertEqual(n['needs_customers'], wl.READY_CUSTOMERS - 1)

    def test_an_area_with_both_sides_reads_as_ready(self):
        for i in range(wl.READY_TRADES):
            wl.join(self.db, self.form(side='trade', email=f't{i}@test.nz', category=str(self.cat)))
        for i in range(wl.READY_CUSTOMERS - 1):
            wl.join(self.db, self.form(side='customer', email=f'c{i}@test.nz'))
        out = wl.join(self.db, self.form(side='customer', email='last@test.nz'))
        self.assertTrue(out['nearby']['ready'])
        self.assertEqual(out['nearby']['needs_trades'], 0)


class WhereToOpenTest(Base):

    def test_the_closest_area_comes_first(self):
        """Sorted by how close to openable, not by how many names."""
        for i in range(20):                       # lots of homeowners, no trades
            wl.join(self.db, self.form(side='customer', email=f'c{i}@test.nz',
                                       area=str(self.other_area)))
        for i in range(wl.READY_TRADES):          # both sides, smaller
            wl.join(self.db, self.form(side='trade', email=f't{i}@test.nz', category=str(self.cat)))
        for i in range(wl.READY_CUSTOMERS):
            wl.join(self.db, self.form(side='customer', email=f'w{i}@test.nz'))

        areas = wl.by_area(self.db)
        self.assertEqual(areas[0]['id'], self.area, 'the one you could actually open')
        self.assertTrue(areas[0]['ready'])
        self.assertFalse(areas[1]['ready'], 'twenty homeowners and no trades is not a market')

    def test_it_counts_trades_who_already_pay(self):
        self.user('trade')
        wl.join(self.db, self.form(side='customer'))
        area = next(a for a in wl.by_area(self.db) if a['id'] == self.area)
        self.assertEqual(area['live_trades'], 1)
        self.assertEqual(area['trades'], 1)

    def test_it_says_which_trades_have_come_forward(self):
        wl.join(self.db, self.form(side='trade', email='b@test.nz', category=str(self.cat)))
        wl.join(self.db, self.form(side='trade', email='s@test.nz', category=str(self.spark)))
        wl.join(self.db, self.form(side='trade', email='s2@test.nz', category=str(self.spark)))
        wanted = wl.trades_wanted(self.db)
        self.assertEqual(wanted[0]['n'], 2, 'commonest first, so outreach knows who is covered')

    def test_totals_add_up(self):
        wl.join(self.db, self.form(side='customer'))
        wl.join(self.db, self.form(side='trade', email='t@test.nz', category=str(self.cat)))
        self.assertEqual(wl.totals(self.db), {'customers': 1, 'trades': 1, 'total': 2})


if __name__ == '__main__':
    unittest.main()
