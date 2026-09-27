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
os.environ['WAITLIST_DEFAULT'] = '0'   # these tests exercise the open site

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

    def test_a_write_that_did_not_stick_says_so_instead_of_claiming_success(self):
        """The failure that actually happened: the button reported success and
        changed nothing. If the read-back disagrees with the intent, the page
        has to say that rather than congratulate itself."""
        import integrations as ig
        real = ig.save
        ig.save = lambda db, values: None          # a save that quietly does nothing
        try:
            page = self.flip(True).data.decode()
        finally:
            ig.save = real
        self.assertIn('didn’t stick', page)
        self.assertNotIn('front door is shut', page)

    def test_a_save_that_raises_is_shown_not_swallowed(self):
        import integrations as ig
        real = ig.save

        def boom(db, values):
            raise RuntimeError('connection went away')
        ig.save = boom
        try:
            page = self.flip(True).data.decode()
        finally:
            ig.save = real
        self.assertIn('Saving that failed', page)
        self.assertIn('connection went away', page)

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


class ItFailsShutTest(Base):
    """The default is closed, and that is the whole point.

    It used to be open unless a saved setting said otherwise, so deploying the
    waitlist did nothing until somebody clicked a button — and when that write
    silently failed, the site carried on taking sign-ups it could not serve. A
    launch gate that needs a successful database write in order to engage is a
    gate that fails open.
    """

    def test_with_nothing_configured_the_door_is_shut(self):
        import integrations as ig
        was = os.environ.pop('WAITLIST_DEFAULT', None)
        try:
            ig._cache.update(at=10 ** 12, values={})
            self.assertTrue(wl.is_on(), 'a site nobody has configured must not take sign-ups')
        finally:
            if was is not None:
                os.environ['WAITLIST_DEFAULT'] = was

    def test_opening_up_is_stored_not_just_absent(self):
        """"Somebody opened it" has to be distinguishable from "nobody said"."""
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        admin = row['id'] if row else self.user('admin')
        self.client(admin).post('/admin/waitlist/switch', data={'on': '0', '_csrf': 't'})
        saved = self.db.execute(
            "SELECT value FROM settings WHERE key = 'integration.waitlist'").fetchone()
        self.assertIsNotNone(saved, 'off must be written down, not represented by a missing row')
        self.assertEqual(saved['value'], '0')

    def test_an_explicit_off_beats_the_default(self):
        import integrations as ig
        was = os.environ.pop('WAITLIST_DEFAULT', None)
        try:
            ig._cache.update(at=10 ** 12, values={'waitlist': '0'})
            self.assertFalse(wl.is_on())
        finally:
            if was is not None:
                os.environ['WAITLIST_DEFAULT'] = was


class TheAppHasTheSameDoorTest(Base):
    """The website's gate didn't cover the phone app, so the app could still
    create accounts while the site said we weren't open. A gate with a second
    way in isn't a gate."""

    API = '/api/mobile'

    def signup(self):
        return self.client().post(f'{self.API}/signup', json={
            'role': 'trade', 'email': 'newtradie@test.nz', 'password': 'password123',
            'name': 'New Tradie', 'business_name': 'New Co'})

    def test_the_app_cannot_create_an_account_while_the_door_is_shut(self):
        self.shut(True)
        before = self.db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        r = self.signup()
        self.assertEqual(r.status_code, 403)
        self.assertIn('waitlist', r.get_json().get('error', '').lower())
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'], before)

    def test_the_app_can_create_an_account_once_we_are_open(self):
        self.shut(False)
        self.assertNotEqual(self.signup().status_code, 403)

    def test_an_existing_account_still_works_from_the_app(self):
        """Shutting the door must not lock out somebody already inside."""
        uid = self.user('customer', 'sam@test.nz')
        self.shut(True)
        r = self.client().post(f'{self.API}/login',
                               json={'login': 'sam@test.nz', 'password': 'password123'})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()['token'])


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
        """Every public page, not a list I remembered to keep up to date.

        The hardcoded four missed /find, which kept a "Post a job — free" button
        pointing at a door that redirects. Walking the URL map means a page added
        next month is covered without anyone thinking about it.
        """
        self.shut(True)
        checked = 0
        for rule in A.app.url_map.iter_rules():
            if 'GET' not in rule.methods or rule.arguments or rule.endpoint == 'static':
                continue
            path = str(rule.rule)
            if path.startswith(('/admin', '/api', '/trade', '/customer', '/hooks')):
                continue
            r = self.client().get(path)
            if r.status_code != 200:
                continue                      # gated or a redirect — not a public page
            page = r.data.decode()
            checked += 1
            for invitation in ('>Post a job<', '>Join as a trade<', 'Post a job — free',
                               '>Start with ', 'Start on Level'):
                self.assertNotIn(invitation, page,
                                 f'{path} still invites a visitor through a shut door: {invitation}')
        self.assertGreater(checked, 5, 'the sweep should be reaching real pages')

    def test_the_pages_google_lands_people_on_are_swept_too(self):
        """The routes with a slug in them — the local SEO pages and trade
        profiles — are most of the public surface and the first thing a stranger
        sees. The argument-free sweep above skipped every one of them, and all
        four were still offering a door that redirects."""
        self.shut(True)
        trade = self.user('trade')
        self.db.execute("UPDATE trades SET business_name = 'Karori Building' WHERE user_id = ?",
                        (trade,))
        self.db.commit()
        area = self.db.execute('SELECT slug FROM areas WHERE id = ?', (self.area,)).fetchone()['slug']
        cat = self.db.execute('SELECT slug FROM categories WHERE id = ?', (self.cat,)).fetchone()['slug']
        for path in (f'/find/{area}', f'/find/{cat}/{area}', f'/pros/{trade}'):
            r = self.client().get(path)
            if r.status_code != 200:
                continue
            page = r.data.decode()
            for door in ('href="/post', 'href="/signup', '/ask"'):
                self.assertNotIn(door, page, f'{path} still links to a door that redirects')


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


class ReferralsSurviveTheShutDoorTest(Base):
    """Closing the door must not quietly cancel the thing that brings people to it.

    /r/<code> used to flash "post your job free" and send them to a door that
    redirects, and a tradie's invite link dropped the invite on the way past. A
    referral that lands on a broken promise is worse than no referral.
    """

    def a_trade_with_an_invite_code(self):
        uid = self.user('trade')
        self.db.execute("UPDATE trades SET invite_code = 'abc123', business_name = 'Karori Building' "
                        'WHERE user_id = ?', (uid,))
        self.db.commit()
        return uid

    def test_an_invite_link_carries_through_to_the_waitlist(self):
        self.a_trade_with_an_invite_code()
        self.shut(True)
        c = self.client()
        c.get('/join/abc123', follow_redirects=True)
        page = c.get('/join').data.decode()
        self.assertIn('Karori Building pointed you here', page)

    def test_the_referral_is_recorded_against_the_name(self):
        self.a_trade_with_an_invite_code()
        self.shut(True)
        c = self.client()
        c.get('/join/abc123', follow_redirects=True)
        c.post('/join', data=self.form(side='trade', category=str(self.cat), _csrf='t'))
        row = self.db.execute('SELECT invited_by FROM waitlist ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(row['invited_by'], 'abc123', 'the credit has to survive until we open')

    def test_a_second_visit_without_the_link_does_not_wipe_the_credit(self):
        self.a_trade_with_an_invite_code()
        self.shut(True)
        c = self.client()
        c.get('/join/abc123', follow_redirects=True)
        c.post('/join', data=self.form(side='customer', email='sam@test.nz', _csrf='t'))
        # Comes back later, no link, different browser session.
        self.client().post('/join', data=self.form(side='customer', email='sam@test.nz', _csrf='t'))
        row = self.db.execute('SELECT invited_by FROM waitlist WHERE email = ?',
                              ('sam@test.nz',)).fetchone()
        self.assertEqual(row['invited_by'], 'abc123')

    def test_a_shared_link_does_not_promise_a_job_post_we_cannot_take(self):
        uid = self.user('customer')
        self.db.execute("UPDATE users SET ref_code = 'share99' WHERE id = ?", (uid,))
        self.db.commit()
        self.shut(True)
        page = self.client().get('/r/share99', follow_redirects=True).data.decode()
        self.assertNotIn('Post your job free', page)
        self.assertIn('not open in your area yet', page)

    def test_with_the_door_open_the_old_promise_is_back(self):
        uid = self.user('customer')
        self.db.execute("UPDATE users SET ref_code = 'share99' WHERE id = ?", (uid,))
        self.db.commit()
        self.shut(False)
        page = self.client().get('/r/share99', follow_redirects=True).data.decode()
        self.assertIn('Post your job free', page)

    def test_an_unknown_code_is_nobody_not_a_crash(self):
        self.shut(True)
        self.assertIsNone(wl.who_invited(self.db, 'nosuchcode'))
        self.assertIsNone(wl.who_invited(self.db, ''))
        self.assertEqual(self.client().get('/join/nosuchcode').status_code, 302)


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


class TellingThemItIsOpenTest(Base):
    """The one email these people agreed to — and the ways it could become two.

    Everything here is about not breaking the single promise on the join page.
    Someone who gets told twice, or who gets marked as told when nothing was
    sent, is worse off than someone who was never on the list.
    """

    def setUp(self):
        super().setUp()
        self.sent = []
        import mailer
        self.real_send = mailer.send
        mailer.send = self.fake_send

    def tearDown(self):
        import mailer
        mailer.send = self.real_send
        super().tearDown()

    def fake_send(self, to, name, subject, body, unsubscribe_url=None, reply_to=None):
        self.sent.append({'to': to, 'subject': subject, 'body': body, 'unsub': unsubscribe_url})
        return True

    def waiting(self, n=3, side='customer'):
        for i in range(n):
            wl.join(self.db, self.form(side=side, email=f'{side}{i}@test.nz',
                                       category=str(self.cat) if side == 'trade' else None))

    def test_everyone_waiting_is_told_once(self):
        self.waiting(3)
        out = wl.open_area(self.db, self.area)
        self.assertEqual(out['sent'], 3)
        self.assertEqual(len(self.sent), 3)

    def test_running_it_again_tells_nobody_twice(self):
        self.waiting(3)
        wl.open_area(self.db, self.area)
        again = wl.open_area(self.db, self.area)
        self.assertEqual(again['sent'], 0)
        self.assertEqual(len(self.sent), 3, 'the single email stays single')

    def test_a_send_that_fails_leaves_them_to_be_told_later(self):
        """Marking somebody told when nothing went out loses them silently."""
        import mailer
        mailer.send = lambda *a, **k: False
        self.waiting(2)
        out = wl.open_area(self.db, self.area)
        self.assertEqual(out['sent'], 0)
        self.assertEqual(out['failed'], 2)
        self.assertEqual(out['left'], 2)
        told = self.db.execute('SELECT COUNT(*) AS n FROM waitlist WHERE told_at IS NOT NULL').fetchone()['n']
        self.assertEqual(told, 0, 'nobody may be marked told when nothing was sent')

    def test_a_half_finished_run_can_simply_be_run_again(self):
        self.waiting(4)
        first = wl.open_area(self.db, self.area, limit=2)
        self.assertEqual(first['sent'], 2)
        self.assertEqual(first['left'], 2)
        second = wl.open_area(self.db, self.area)
        self.assertEqual(second['sent'], 2)
        self.assertEqual(second['left'], 0)

    def test_only_that_area_is_told(self):
        self.waiting(2)
        wl.join(self.db, self.form(side='customer', email='elsewhere@test.nz',
                                   area=str(self.other_area)))
        wl.open_area(self.db, self.area)
        self.assertNotIn('elsewhere@test.nz', [m['to'] for m in self.sent])

    def test_each_side_is_told_the_thing_that_matters_to_them(self):
        self.waiting(1, side='customer')
        self.waiting(1, side='trade')
        wl.open_area(self.db, self.area)
        to_customer = next(m for m in self.sent if m['to'].startswith('customer'))
        to_trade = next(m for m in self.sent if m['to'].startswith('trade'))
        self.assertIn('/post', to_customer['body'])
        self.assertIn('/signup', to_trade['body'])
        self.assertIn('refunded', to_trade['body'], 'the guarantee is the tradie’s reason to bother')

    def test_every_message_carries_a_real_way_out(self):
        self.waiting(2)
        wl.open_area(self.db, self.area)
        for m in self.sent:
            self.assertTrue(m['unsub'], 'no unsubscribe link')
            self.assertIn('/waitlist/stop/', m['unsub'])

    def test_that_link_actually_removes_them(self):
        self.waiting(1)
        wl.open_area(self.db, self.area)
        token = self.sent[0]['unsub'].rsplit('/', 1)[-1]
        page = self.client().get(f'/waitlist/stop/{token}').data.decode()
        self.assertIn('off the list', page)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM waitlist').fetchone()['n'], 0)

    def test_a_used_link_says_so_rather_than_erroring(self):
        self.waiting(1)
        wl.open_area(self.db, self.area)
        token = self.sent[0]['unsub'].rsplit('/', 1)[-1]
        self.client().get(f'/waitlist/stop/{token}')
        r = self.client().get(f'/waitlist/stop/{token}')
        self.assertEqual(r.status_code, 200)
        self.assertIn('already been used', r.data.decode())

    def test_one_click_unsubscribe_works_without_a_form_token(self):
        """Mail clients POST to that header's URL with no CSRF token."""
        self.waiting(1)
        wl.open_area(self.db, self.area)
        token = self.sent[0]['unsub'].rsplit('/', 1)[-1]
        r = A.app.test_client().post(f'/waitlist/stop/{token}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM waitlist').fetchone()['n'], 0)

    def test_an_admin_can_find_the_page_without_being_told_the_url(self):
        """It wasn't in the admin nav, so the only way to reach the one control
        that opens a region was to already know the address."""
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        admin = row['id'] if row else self.user('admin')
        page = self.client(admin).get('/admin').data.decode()
        self.assertIn('/admin/waitlist', page)

    def test_an_admin_can_do_it_from_the_page(self):
        self.waiting(2)
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        admin = row['id'] if row else self.user('admin')
        r = self.client(admin).post('/admin/waitlist/open',
                                    data={'area': self.area, '_csrf': 't'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.sent), 2)

    def test_nobody_else_can(self):
        self.waiting(1)
        for who in (None, self.user('customer'), self.user('trade')):
            self.client(who).post('/admin/waitlist/open', data={'area': self.area, '_csrf': 't'})
        self.assertEqual(self.sent, [], 'only an admin opens an area')


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
