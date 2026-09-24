"""
Tests for the phone app's JSON API (api_mobile.py) and push sending (push.py).

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import app as A  # noqa: E402
import billing  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import push  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

API = '/api/mobile'
JOB = dict(category='builder', area='akl-central', suburb='Ponsonby', title='Replace rotten deck boards',
           description='About ten boards on the back deck are soft and need replacing, plus a wobbly rail.',
           value_band='small', timing='weeks', property_type='house')
QUOTE = dict(price_type='fixed', amount_low='1800', gst='incl',
             message='Happy to do this. Replace the soft boards, fix the rail and re-oil the deck.')


class ApiTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        A._login_failures.clear()
        integrations._cache.update(at=10 ** 12, values={})     # nothing configured, don't reload
        self.db = dbmod.get_db()
        self.c = A.app.test_client()
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']

    def tearDown(self):
        A._login_failures.clear()                               # don't lock out the other test files
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def user(self, role='customer', email='sam@test.nz', username=None):
        uid = self.db.execute('INSERT INTO users (role, email, username, password_hash, name, phone, created_at) '
                              'VALUES (?,?,?,?,?,?,?)', (role, email, username, hash_password('password123'),
                                                         'Sam Walker', '021 555 0101', ts(utcnow()))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(utcnow())))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            trade = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, trade, email, 'large', '', '')
        self.db.commit()
        return uid

    def login(self, ident, password='password123'):
        r = self.c.post(f'{API}/login', json={'login': ident, 'password': password})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()['token']

    def call(self, method, path, token=None, **kw):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return getattr(self.c, method)(f'{API}{path}', headers=headers, **kw)

    # ── sign in ──
    def test_login_with_email_or_username(self):
        self.user('customer', 'sam@test.nz', username='sam w')
        self.assertTrue(self.login('Sam@Test.nz'))
        token = self.login('SAM W')
        me = self.call('get', '/me', token).get_json()
        self.assertEqual(me['user']['email'], 'sam@test.nz')
        self.assertEqual(me['user']['role'], 'customer')
        stored = self.db.execute('SELECT token_hash FROM api_tokens').fetchall()
        self.assertEqual(len(stored), 2)
        self.assertNotIn(token, [r['token_hash'] for r in stored])        # only the hash is kept

    def test_wrong_password_and_lockout(self):
        self.user('customer', 'sam@test.nz')
        r = self.c.post(f'{API}/login', json={'login': 'sam@test.nz', 'password': 'nope'})
        self.assertEqual(r.status_code, 401)
        for _ in range(10):
            self.c.post(f'{API}/login', json={'login': 'sam@test.nz', 'password': 'nope'})
        r = self.c.post(f'{API}/login', json={'login': 'sam@test.nz', 'password': 'password123'})
        self.assertEqual(r.status_code, 429)

    def test_token_is_required_and_logout_revokes_it(self):
        self.assertEqual(self.call('get', '/me').status_code, 401)
        self.assertEqual(self.call('get', '/me', 'made-up-token').status_code, 401)
        self.assertEqual(self.call('get', '/customer/jobs').status_code, 401)
        self.user('customer', 'sam@test.nz')
        token = self.login('sam@test.nz')
        self.assertEqual(self.call('get', '/me', token).status_code, 200)
        self.assertEqual(self.call('post', '/logout', token).status_code, 200)
        self.assertEqual(self.call('get', '/me', token).status_code, 401)

    def test_session_cookie_is_not_accepted(self):
        """The CSRF exemption is only safe because the API never reads the cookie."""
        uid = self.user('customer', 'sam@test.nz')
        with self.c.session_transaction() as s:
            s['uid'] = uid
        self.assertEqual(self.call('get', '/me').status_code, 401)
        self.assertEqual(self.call('post', '/customer/jobs', json=JOB).status_code, 401)

    def test_roles_are_kept_apart(self):
        self.user('customer', 'c@test.nz')
        self.user('trade', 't@test.nz')
        customer, trade = self.login('c@test.nz'), self.login('t@test.nz')
        self.assertEqual(self.call('get', '/trade/offers', customer).status_code, 403)
        self.assertEqual(self.call('post', '/customer/jobs', trade, json=JOB).status_code, 403)

    # ── the whole loop ──
    def test_customer_posts_and_trade_quotes_through_the_api(self):
        self.user('customer', 'c@test.nz')
        trade_id = self.user('trade', 't@test.nz')
        customer, trade = self.login('c@test.nz'), self.login('t@test.nz')

        bad = self.call('post', '/customer/jobs', customer, json=dict(JOB, description='too short'))
        self.assertEqual(bad.status_code, 400)
        self.assertIn('description', bad.get_json()['errors'])

        r = self.call('post', '/customer/jobs', customer, data=dict(
            JOB, photos=(io.BytesIO(b'fake image'), 'deck.jpg')), content_type='multipart/form-data')
        self.assertEqual(r.status_code, 201, r.get_json())
        job_id = r.get_json()['job_id']
        self.assertEqual(r.get_json()['offered'], 1)
        self.assertEqual(len(self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['photos']), 1)

        offers = self.call('get', '/trade/offers', trade).get_json()
        self.assertEqual([o['id'] for o in offers['offers']], [job_id])
        self.assertGreater(offers['offers'][0]['seconds_left'], 0)   # the working-hours window is still running
        self.assertFalse(offers['trade']['needs_web_setup'])

        detail = self.call('get', f'/trade/jobs/{job_id}', trade).get_json()
        self.assertTrue(detail['can_quote'])
        self.assertNotIn('address', detail['job'])

        bad = self.call('post', f'/trade/jobs/{job_id}/quote', trade, json=dict(QUOTE, message='cheap'))
        self.assertEqual(bad.status_code, 400)                      # engine's quote rules apply
        r = self.call('post', f'/trade/jobs/{job_id}/quote', trade, json=QUOTE)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()['quote_number'], 1)
        self.assertEqual(self.call('post', f'/trade/jobs/{job_id}/quote', trade, json=QUOTE).status_code, 400)

        job = self.call('get', f'/customer/jobs/{job_id}', customer).get_json()
        self.assertEqual(len(job['quotes']), 1)
        quote = job['quotes'][0]
        self.assertEqual(quote['price_text'], '$1,800')
        self.assertNotIn('contact', quote)                          # not until they share
        r = self.call('post', f'/customer/jobs/{job_id}/quotes/{quote["id"]}/share', customer)
        self.assertEqual(r.status_code, 200)
        quote = self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['quotes'][0]
        self.assertEqual(quote['contact']['phone'], '021 555 0101')

        r = self.call('post', f'/threads/{job_id}/{trade_id}', customer, json={'body': 'When could you start?'})
        self.assertEqual(r.status_code, 201)
        thread = self.call('get', f'/threads/{job_id}/{trade_id}', trade).get_json()
        self.assertEqual([m['body'] for m in thread['messages']], ['When could you start?'])
        self.assertFalse(thread['messages'][0]['mine'])
        self.assertEqual(len(self.call('get', '/threads', trade).get_json()['threads']), 1)

        r = self.call('post', f'/customer/jobs/{job_id}/quotes/{quote["id"]}/accept', customer)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.row('SELECT status FROM jobs WHERE id = ?', job_id)['status'], 'hired')
        r = self.call('post', f'/customer/jobs/{job_id}/review', customer, json=dict(
            workmanship=5, communication=4, timeliness=5, value_for_money=4, body='Great job.'))
        self.assertEqual(r.status_code, 201, r.get_json())
        notes = self.call('get', '/notifications', trade).get_json()
        self.assertTrue(any('won' in n['body'] for n in notes['notifications']))

    def test_trade_can_pass_and_the_slot_moves_on(self):
        customer_id = self.user('customer', 'c@test.nz')
        self.user('trade', 't@test.nz')
        job_id = engine.post_job(self.db, customer_id, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Fix deck',
            description='Rotten boards on the back deck need replacing.', value_band='small',
            timing='weeks', property_type='house'))[0]
        trade = self.login('t@test.nz')
        self.assertEqual(self.call('post', f'/trade/jobs/{job_id}/pass', trade).status_code, 200)
        self.assertEqual(self.call('get', '/trade/offers', trade).get_json()['offers'], [])

    # ── sign up ──
    def test_signup_customer_and_trade(self):
        r = self.c.post(f'{API}/signup', json=dict(role='customer', name='Aroha', email='aroha@test.nz',
                                                    phone='021 222 3333', password='longenough'))
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()['user']['role'], 'customer')
        r = self.c.post(f'{API}/signup', json=dict(role='customer', name='Aroha', email='aroha@test.nz',
                                                    phone='021 222 3333', password='longenough'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('email', r.get_json()['errors'])

        r = self.c.post(f'{API}/signup', json=dict(role='trade', name='Mike', email='mike@test.nz',
                                                    phone='021 444 5555', password='longenough',
                                                    business_name='Mike’s Plumbing'))
        self.assertEqual(r.status_code, 201, r.get_json())
        trade = r.get_json()['user']['trade']
        self.assertTrue(trade['needs_web_setup'])                   # plans and areas are picked on the web
        self.assertIn('/trade/setup', trade['setup_url'])

    # ── account deletion (App Store 5.1.1(v)) ──
    def test_account_deletion_needs_the_password_and_scrubs_details(self):
        uid = self.user('customer', 'sam@test.nz')
        token = self.login('sam@test.nz')
        self.call('post', '/push-tokens', token, json={'token': 'ExponentPushToken[abcdefghijklmnop]', 'platform': 'ios'})
        r = self.call('delete', '/me', token, json={'password': 'wrong'})
        self.assertEqual(r.status_code, 400)
        r = self.call('delete', '/me', token, json={'password': 'password123'})
        self.assertEqual(r.status_code, 200)
        u = self.row('SELECT * FROM users WHERE id = ?', uid)
        self.assertIsNotNone(u['closed_at'])
        self.assertEqual(u['name'], 'Closed account')
        self.assertEqual(self.row('SELECT COUNT(*) AS n FROM api_tokens')['n'], 0)
        self.assertEqual(self.row('SELECT COUNT(*) AS n FROM push_tokens')['n'], 0)
        self.assertEqual(self.call('get', '/me', token).status_code, 401)
        r = self.c.post(f'{API}/login', json={'login': 'sam@test.nz', 'password': 'password123'})
        self.assertEqual(r.status_code, 401)

    # ── push ──
    def test_push_token_register_and_flush(self):
        uid = self.user('trade', 't@test.nz')
        token = self.login('t@test.nz')
        self.assertEqual(self.call('post', '/push-tokens', token, json={'token': 'not a token'}).status_code, 400)
        good, dead = 'ExponentPushToken[good-token-1234]', 'ExponentPushToken[dead-token-5678]'
        for t in (good, dead):
            r = self.call('post', '/push-tokens', token, json={'token': t, 'platform': 'ios'})
            self.assertEqual(r.status_code, 200)
        self.db.execute("UPDATE push_tokens SET created_at = '2000-01-01 00:00:00'")
        engine.notify(self.db, uid, 'New job in Auckland', '/trade/jobs/1')
        self.db.commit()

        sent = []

        class Resp(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            messages = json.loads(req.data.decode())
            sent.extend(messages)
            return Resp(json.dumps({'data': [
                {'status': 'ok'} if m['to'] == good else
                {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}} for m in messages]}).encode())

        with mock.patch('push.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(push.flush(self.db), 1)
            self.assertEqual(push.flush(self.db), 0)                 # marked, not sent twice
        self.assertEqual(sorted(m['to'] for m in sent), sorted([good, dead]))
        self.assertEqual(sent[0]['data']['link'], '/trade/jobs/1')
        tokens = [r['token'] for r in self.db.execute('SELECT token FROM push_tokens').fetchall()]
        self.assertEqual(tokens, [good])                             # the dead one is removed

    def test_push_flush_is_a_no_op_without_phones(self):
        uid = self.user('customer', 'c@test.nz')
        engine.notify(self.db, uid, 'Hello', '/me')
        self.db.commit()
        with mock.patch('push.urllib.request.urlopen') as urlopen:
            self.assertEqual(push.flush(self.db), 0)
            urlopen.assert_not_called()

    def test_config_is_public(self):
        r = self.c.get(f'{API}/config')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(any(c['slug'] == 'builder' for c in body['categories']))
        self.assertNotIn('price', json.dumps(body).lower())          # nothing about paying in the app

    # ── progress updates ──
    def _hired(self, plan):
        """A customer job posted and quoted through the API, then accepted. Returns tokens and ids."""
        self.user('customer', 'c@test.nz')
        trade_id = self.user('trade', 't@test.nz')
        other_id = self.user('trade', 'o@test.nz')
        customer, trade, other = self.login('c@test.nz'), self.login('t@test.nz'), self.login('o@test.nz')
        job_id = self.call('post', '/customer/jobs', customer, json=JOB).get_json()['job_id']
        r = self.call('post', f'/trade/jobs/{job_id}/quote', trade, json=dict(QUOTE, report_plan=plan))
        self.assertEqual(r.status_code, 201, r.get_json())
        self.call('post', f'/trade/jobs/{job_id}/quote', other, json=QUOTE)
        return customer, trade, other, job_id, trade_id, other_id

    def test_quote_promise_is_shown_and_carried_onto_the_hired_job(self):
        customer, trade, other, job_id, trade_id, _ = self._hired(['weekly', 'daily'])
        quotes = {q['trade_id']: q for q in self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['quotes']}
        self.assertEqual(quotes[trade_id]['report_plan'], ['daily', 'weekly'])
        self.assertEqual(quotes[trade_id]['report_plan_text'], 'Daily and weekly updates')
        self.assertIn('report_record', quotes[trade_id])
        # Editing the quote can change the promise (comma string works too)
        r = self.call('post', f'/trade/jobs/{job_id}/quote/edit', trade, json=dict(QUOTE, report_plan='weekly'))
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.row('SELECT report_plan FROM quotes WHERE trade_id = ?', trade_id)['report_plan'], 'weekly')
        self.assertIsNone(self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['progress'])

        self.call('post', f'/customer/jobs/{job_id}/quotes/{quotes[trade_id]["id"]}/accept', customer)
        progress = self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['progress']
        self.assertEqual(progress['report_plan'], ['weekly'])
        self.assertTrue(progress['work_started_on'])
        self.assertIn('weekly', progress['score'])
        self.assertEqual(self.call('get', f'/trade/jobs/{job_id}', trade).get_json()['progress']['report_plan'], ['weekly'])
        self.assertIsNone(self.call('get', f'/trade/jobs/{job_id}', other).get_json()['progress'])

    def test_hired_trade_posts_updates_with_photos_and_either_side_can_finish(self):
        customer, trade, other, job_id, trade_id, _ = self._hired('daily,weekly')
        quotes = {q['trade_id']: q for q in self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['quotes']}
        self.call('post', f'/customer/jobs/{job_id}/quotes/{quotes[trade_id]["id"]}/accept', customer)

        update = lambda **kw: dict(dict(body='Framing is up and the roof is on. Cladding starts Monday.', kinds='daily',
                                        photos=(io.BytesIO(b'fake image'), 'site.jpg')), **kw)
        r = self.call('post', f'/trade/jobs/{job_id}/updates', other, data=update(),
                      content_type='multipart/form-data')
        self.assertEqual(r.status_code, 404)                            # only the hired trade
        self.assertEqual(self.call('post', f'/trade/jobs/{job_id}/updates', customer, data={}).status_code, 403)
        r = self.call('post', f'/trade/jobs/{job_id}/updates', trade, data=update(body='short'),
                      content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)                            # reporting's rules apply
        r = self.call('post', f'/trade/jobs/{job_id}/updates', trade, data=update(), content_type='multipart/form-data')
        self.assertEqual(r.status_code, 201, r.get_json())

        progress = self.call('get', f'/customer/jobs/{job_id}', customer).get_json()['progress']
        self.assertEqual(len(progress['updates']), 1)
        self.assertEqual(progress['updates'][0]['kinds'], ['daily'])
        self.assertEqual(len(progress['updates'][0]['photos']), 1)
        self.assertTrue(progress['updates'][0]['photos'][0].startswith('http'))

        r = self.call('post', f'/trade/jobs/{job_id}/start', trade, json={'start': 'not a date'})
        self.assertEqual(r.status_code, 400)
        r = self.call('post', f'/trade/jobs/{job_id}/start', trade, json={'start': progress['today']})
        self.assertEqual(r.status_code, 200, r.get_json())

        self.assertEqual(self.call('post', f'/customer/jobs/{job_id}/finish', customer).status_code, 200)
        done = self.call('get', f'/trade/jobs/{job_id}', trade).get_json()['progress']
        self.assertTrue(done['work_done_on'])
        self.assertEqual(done['due'], [])
        self.assertEqual(self.call('post', f'/trade/jobs/{job_id}/finish', trade).status_code, 200)

    def test_trade_profile_has_the_default_promise(self):
        uid = self.user('trade', 't@test.nz')
        self.db.execute("UPDATE trades SET report_plan = 'weekly' WHERE user_id = ?", (uid,))
        self.db.commit()
        profile = self.call('get', '/trade/profile', self.login('t@test.nz')).get_json()['profile']
        self.assertEqual(profile['report_plan'], ['weekly'])

    # ── referrals ──
    def test_trade_referrals_show_the_invite_link_and_free_months(self):
        inviter = self.user('trade', 't@test.nz')
        token = self.login('t@test.nz')
        r = self.call('get', '/trade/referrals', token)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn('/join/', body['invite_url'])
        self.assertEqual(body['months']['earned'], 0)
        self.assertNotIn('$', json.dumps(body))                          # months, never money

        newbie = self.user('trade', 'new@test.nz')
        self.db.execute('UPDATE users SET referred_by = ? WHERE id = ?', (inviter, newbie))
        self.db.commit()
        customer_id = self.user('customer', 'c@test.nz')
        job_id = engine.post_job(self.db, customer_id, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Fix deck',
            description='Rotten boards on the back deck need replacing.', value_band='small',
            timing='weeks', property_type='house'))[0]
        r = self.call('post', f'/trade/jobs/{job_id}/quote', self.login('new@test.nz'), json=QUOTE)
        self.assertEqual(r.status_code, 201, r.get_json())
        body = self.call('get', '/trade/referrals', token).get_json()
        self.assertEqual(body['months']['earned'], 1)
        self.assertEqual([j['quoted'] for j in body['joined']], [True])
        self.assertEqual(self.call('get', '/trade/referrals', self.login('c@test.nz')).status_code, 403)

    def test_customer_share_link_and_recommending_a_tradie(self):
        self.user('customer', 'c@test.nz')
        existing = self.user('trade', 'known@test.nz')
        token = self.login('c@test.nz')
        share = self.call('get', '/customer/share', token).get_json()
        self.assertIn('/r/', share['share_url'])
        self.assertEqual(share['share_url'], self.call('get', '/customer/share', token).get_json()['share_url'])

        r = self.call('post', '/customer/recommend', token, json={'name': 'Hemi', 'category_id': self.cat,
                                                                  'area_id': self.area})
        self.assertEqual(r.status_code, 400)                            # needs an email or mobile
        with mock.patch('referrals.threading.Thread') as thread, mock.patch('mailer.enabled', return_value=True):
            r = self.call('post', '/customer/recommend', token, json={
                'name': 'Hemi Walker', 'business_name': 'Walker Plumbing', 'email': 'hemi@test.nz',
                'category_id': self.cat, 'area_id': self.area, 'email_them': True})
            self.assertEqual(r.status_code, 201, r.get_json())
            self.assertTrue(r.get_json()['emailed'])
            thread.assert_called_once()
        self.assertIn('/o/', r.get_json()['join_url'])
        self.assertEqual(self.call('get', '/customer/share', token).get_json()['recommended'][0]['business_name'],
                         'Walker Plumbing')
        r = self.call('post', '/customer/recommend', token, json={
            'name': 'Known', 'email': 'known@test.nz', 'category_id': self.cat, 'area_id': self.area})
        self.assertTrue(r.get_json()['already_on_level'])
        self.assertIn(f'/pros/{existing}', r.get_json()['profile_url'])

    def row(self, sql, *args):
        return self.db.execute(sql, args).fetchone()


if __name__ == '__main__':
    unittest.main()
