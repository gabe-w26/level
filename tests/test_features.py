"""
Tests for the launch features: nudging quiet customers, asking who they hired,
phone checks that hold a job back, text alerts, quote templates, invites,
price guides and the admin setup page.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import accounts  # noqa: E402
import app as A  # noqa: E402
import billing  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import sms  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 1, 0, 0, 0)
TWILIO = {'twilio_sid': 'ACtest', 'twilio_token': 'secret', 'twilio_from': '+6421000000'}


class FeatureTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})     # nothing configured, don't reload
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def user(self, role='customer', email='sam@test.nz', phone='021 555 0101'):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, phone, created_at) '
                              'VALUES (?,?,?,?,?,?)',
                              (role, email, hash_password('password123'), 'Sam Walker', phone, ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            trade = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, trade, email, 'large', '', '', at=T0)
        self.db.commit()
        return uid

    def row(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def client(self, email=None):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        if email:
            c.post('/login', data={'email': email, 'password': 'password123', '_csrf': 't'})
            with c.session_transaction() as s:
                s['_csrf'] = 't'
        return c

    def job(self, customer, at=T0, hold=False):
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Fix deck',
            description='Rotten boards on the back deck need replacing.', value_band='small',
            timing='weeks', property_type='house'), at=at, hold=hold)[0]

    def quote(self, job_id, trade, at, amount=2000):
        engine.submit_quote(self.db, job_id, trade, dict(
            price_type='fixed', amount_low=amount, gst_included=1,
            message='Happy to do this — replace the boards and re-oil the deck.'), at=at)

    def notes_for(self, user_id, like):
        return self.row('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND body LIKE ?',
                        user_id, f'%{like}%')['n']

    # ── nudges ──
    def test_customer_is_nudged_once_after_three_days_of_silence(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        job_id = self.job(customer)
        self.quote(job_id, trade, T0 + timedelta(hours=2))
        engine.sweep(self.db, at=T0 + timedelta(days=2))
        self.assertEqual(self.notes_for(customer, 'waiting to hear back'), 0, 'too early')
        engine.sweep(self.db, at=T0 + timedelta(days=3, hours=3))
        engine.sweep(self.db, at=T0 + timedelta(days=5))
        self.assertEqual(self.notes_for(customer, 'waiting to hear back'), 1, 'exactly one nudge')

    def test_no_nudge_once_the_customer_has_replied(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        job_id = self.job(customer)
        self.quote(job_id, trade, T0 + timedelta(hours=2))
        q = self.row('SELECT id FROM quotes WHERE job_id = ?', job_id)
        engine.share_contact(self.db, engine.get_job(self.db, job_id), q['id'], at=T0 + timedelta(days=1))
        engine.sweep(self.db, at=T0 + timedelta(days=4))
        self.assertEqual(self.notes_for(customer, 'waiting to hear back'), 0)

    def test_asks_who_they_hired_after_a_job_expires_and_accepts_the_answer(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        job_id = self.job(customer)
        self.quote(job_id, trade, T0 + timedelta(hours=2))
        engine.sweep(self.db, at=T0 + timedelta(days=15))
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'expired')
        self.assertEqual(self.notes_for(customer, 'Did you end up hiring'), 1)
        # They say they hired the trade — that counts as a win, even after expiry.
        q = self.row('SELECT id FROM quotes WHERE job_id = ?', job_id)
        engine.close_job(self.db, engine.get_job(self.db, job_id), str(q['id']), at=T0 + timedelta(days=16))
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'hired')
        self.assertEqual(self.row('SELECT status FROM quotes WHERE id = ?', q['id'])['status'], 'accepted')

    # ── phone checks ──
    def test_phone_code_right_wrong_and_lockout(self):
        customer = self.user()
        person = self.row('SELECT * FROM users WHERE id = ?', customer)
        code, sent = accounts.send_phone_code(self.db, person)
        self.assertFalse(sent, 'texts are not set up in tests')
        self.assertEqual(accounts.check_phone_code(self.db, person, '000000' if code != '000000' else '111111'), 'wrong')
        self.assertEqual(accounts.check_phone_code(self.db, person, code), 'ok')
        self.assertTrue(self.row('SELECT phone_verified_at FROM users WHERE id = ?', customer)['phone_verified_at'])
        code, _ = accounts.send_phone_code(self.db, person)
        for _ in range(accounts.PHONE_CODE_TRIES):
            accounts.check_phone_code(self.db, person, 'nope')
        self.assertEqual(accounts.check_phone_code(self.db, person, code), 'locked',
                         'the right code must not work after too many wrong tries')

    def test_held_job_reaches_nobody_until_the_phone_is_confirmed(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        job_id = self.job(customer, hold=True)
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'held')
        self.assertEqual(self.row('SELECT COUNT(*) AS n FROM offers WHERE job_id = ?', job_id)['n'], 0)
        released = engine.release_held(self.db, customer, at=T0 + timedelta(hours=1))
        self.assertEqual(released, 1)
        self.assertEqual(engine.get_job(self.db, job_id)['status'], 'open')
        self.assertEqual(self.row('SELECT trade_id FROM offers WHERE job_id = ?', job_id)['trade_id'], trade)

    def test_posting_holds_the_job_when_texts_are_on(self):
        self.user('trade', 't@test.nz')
        integrations._cache['values'] = dict(TWILIO)
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        with mock.patch.object(sms, 'send', return_value=True):
            r = c.post('/post', data={'category': 'builder', 'area': 'akl-central', 'suburb': 'Epsom',
                                      'title': 'Squeaky hallway floor', 'value_band': 'small', 'timing': 'weeks',
                                      'property_type': 'house', 'name': 'New Person', 'email': 'new@test.nz',
                                      'phone': '021 555 0199', 'password': 'password123', '_csrf': 't',
                                      'description': 'The hallway floor squeaks along about three metres near the door.'})
        self.assertIn('/verify-phone', r.headers['Location'])
        job = self.row("SELECT status FROM jobs WHERE title = 'Squeaky hallway floor'")
        self.assertEqual(job['status'], 'held')

    # ── texts ──
    def test_new_jobs_are_marked_for_texting_and_sent_once_texts_are_on(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        self.job(customer, at=engine.utcnow())
        self.assertEqual(self.row('SELECT sms FROM notifications WHERE user_id = ?', trade)['sms'], 1)
        integrations._cache['values'] = dict(TWILIO)
        with mock.patch.object(sms, 'send', return_value=True) as fake:
            self.assertEqual(sms.flush(self.db), 1)
            to, body = fake.call_args[0]
            self.assertEqual(to, '021 555 0101')
            self.assertIn('Fix deck', body)
            self.assertEqual(sms.flush(self.db), 0, 'each alert is texted once')

    def test_tradies_who_turn_texts_off_are_not_texted(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        self.db.execute('UPDATE users SET text_alerts = 0 WHERE id = ?', (trade,))
        self.db.commit()
        self.job(customer, at=engine.utcnow())
        integrations._cache['values'] = dict(TWILIO)
        with mock.patch.object(sms, 'send', return_value=True) as fake:
            sms.flush(self.db)
            fake.assert_not_called()

    def test_nz_phone_numbers_are_normalised(self):
        for raw, want in [('021 123 4567', '+64211234567'), ('+64 21 123 4567', '+64211234567'),
                          ('6421 123 4567', '+64211234567'), ('09 555 1234', '+6495551234'), ('hello', None)]:
            self.assertEqual(sms.nz_number(raw), want, raw)

    # ── templates ──
    def test_quote_can_be_saved_as_a_template_and_reused(self):
        trade, customer = self.user('trade', 't@test.nz'), self.user('customer', 'c@test.nz')
        job_id = self.job(customer, at=engine.utcnow())
        c = self.client('t@test.nz')
        c.post(f'/trade/jobs/{job_id}/quote', data={
            'price_type': 'fixed', 'amount_low': '2000', 'gst': 'incl', '_csrf': 't',
            'message': 'Happy to replace the boards and re-oil the whole deck.', 'warranty': '2 years',
            'save_template': '1', 'template_name': 'Deck repair'})
        tpl = self.row('SELECT * FROM quote_templates WHERE trade_id = ?', trade)
        self.assertEqual((tpl['name'], tpl['warranty']), ('Deck repair', '2 years'))
        other_job = self.job(customer, at=engine.utcnow())
        self.assertIn(b'Deck repair', c.get(f'/trade/jobs/{other_job}').data, 'offered on the next quote form')
        c.post(f'/trade/templates/{tpl["id"]}/delete', data={'_csrf': 't'})
        self.assertIsNone(self.row('SELECT id FROM quote_templates WHERE trade_id = ?', trade))

    # ── invites ──
    def test_invite_link_credits_the_tradie_who_sent_it(self):
        inviter = self.user('trade', 'mate@test.nz')
        page = self.client('mate@test.nz').get('/trade').get_data(as_text=True)
        code = self.row('SELECT invite_code FROM trades WHERE user_id = ?', inviter)['invite_code']
        self.assertTrue(code and f'/join/{code}' in page)
        c = self.client()
        c.get(f'/join/{code}')
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        c.post('/signup', data={'name': 'New Tradie', 'business_name': 'New Co', 'email': 'newco@test.nz',
                                'phone': '021 555 0202', 'password': 'password123', '_csrf': 't'})
        joined = self.row("SELECT referred_by FROM users WHERE email = 'newco@test.nz'")
        self.assertEqual(joined['referred_by'], inviter)
        self.assertEqual(self.notes_for(inviter, 'joined'), 1)

    # ── price guides ──
    def test_price_guide_only_shows_prices_with_enough_quotes(self):
        customer = self.user('customer', 'c@test.nz')
        trades = [self.user('trade', f't{i}@test.nz') for i in range(6)]
        c = self.client()
        self.assertIn(b'enough', c.get('/guides/builder').data)
        for i, trade in enumerate(trades[:A.GUIDE_MIN_QUOTES]):
            job_id = self.job(customer, at=engine.utcnow())
            self.quote(job_id, trade, engine.utcnow(), amount=1000 + i * 500)
        page = c.get('/guides/builder').get_data(as_text=True)
        self.assertIn('$2,000', page, 'middle of 1,000 / 1,500 / 2,000 / 2,500 / 3,000')
        self.assertEqual(c.get('/guides').status_code, 200)
        self.assertIn(b'/guides/builder', c.get('/sitemap.xml').data)

    # ── admin setup ──
    def test_admin_can_save_keys_without_touching_the_host(self):
        c = self.client('admin@level.local')
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        c.post('/login', data={'email': 'admin@level.local', 'password': 'admin123', '_csrf': 't'})
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        integrations._cache['at'] = 0
        r = c.post('/admin/setup', data={'action': 'save', 'smtp_host': 'smtp.gmail.com', 'smtp_pass': 'app-password-1234',
                                         '_csrf': 't'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(integrations.get('smtp_host'), 'smtp.gmail.com')
        self.assertEqual(integrations.masked('smtp_pass'), '••••••••1234', 'secrets are never shown in full')
        page = c.get('/admin/setup').get_data(as_text=True)
        self.assertNotIn('app-password-1234', page)
        # a blank field keeps what's saved
        c.post('/admin/setup', data={'action': 'save', 'smtp_host': '', '_csrf': 't'})
        self.assertEqual(integrations.get('smtp_host'), 'smtp.gmail.com')

    def test_describe_helper_is_off_without_a_key(self):
        c = self.client()
        r = c.post('/post/help', data={'description': 'deck is rotten', '_csrf': 't'})
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
