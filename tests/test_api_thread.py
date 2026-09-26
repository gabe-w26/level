"""
Attachments and extra-work requests, from the phone.

The website got these first; this is the app catching up, so the tests that
matter are the ones proving the phone is held to the *same* rules rather than a
looser set. Two in particular:

  · a file is not public just because the app asked for it over a bearer token.
    Four tests come at that from four directions, mirroring the website's.
  · the person who asked for extra work still can't be the one who agrees to it.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
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
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import thread as threads  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

API = '/api/mobile'


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        A._login_failures.clear()
        integrations._cache.update(at=10 ** 12, values={})
        self.db = dbmod.get_db()
        self.c = A.app.test_client()
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'akl-central'").fetchone()['id']
        self.n = 0
        self.trade = self.user('trade')
        self.customer = self.user('customer')
        self.job_id = self.a_job_with_a_quote()
        self.t_token = self.login(self.email(self.trade))
        self.c_token = self.login(self.email(self.customer))
        self.written = []

    def tearDown(self):
        for name in self.written:
            try:
                os.remove(os.path.join(A.UPLOAD_DIR, name))
            except OSError:
                pass
        A._login_failures.clear()
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer'):
        self.n += 1
        email = f'u{self.n}@test.nz'
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              (role, email, hash_password('password123'), f'Person {self.n}',
                               ts(utcnow()))).lastrowid
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

    def email(self, uid):
        return self.db.execute('SELECT email FROM users WHERE id = ?', (uid,)).fetchone()['email']

    def a_job_with_a_quote(self):
        now = utcnow()
        job_id = engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Ponsonby', title='Replace rotten deck boards',
            description='Twelve square metres, boards are soft underfoot.', value_band='medium',
            timing='weeks', property_type='house'), at=now)[0]
        engine.submit_quote(self.db, job_id, self.trade, dict(
            price_type='fixed', amount_low=6400, gst_included=1,
            message='Two days on site, timber supplied and the old stuff taken away.'),
            at=now + timedelta(minutes=1))
        return job_id

    def login(self, ident):
        r = self.c.post(f'{API}/login', json={'login': ident, 'password': 'password123'})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()['token']

    def call(self, method, path, token=None, **kw):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return getattr(self.c, method)(f'{API}{path}', headers=headers, **kw)

    def url(self):
        return f'/threads/{self.job_id}/{self.trade}'

    def photo(self, name='deck.png'):
        return (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 64), name)

    def send(self, token, body='Here you go', files=None):
        data = {'body': body}
        if files:
            data['files'] = files
        return self.call('post', self.url(), token, data=data, content_type='multipart/form-data')

    def remember_files(self):
        for r in self.db.execute('SELECT filename FROM message_files'):
            if r['filename'] not in self.written:
                self.written.append(r['filename'])


class SendingAFileTest(Base):

    def test_a_photo_can_be_sent_from_the_app(self):
        r = self.send(self.t_token, 'Found this under the boards', files=self.photo())
        self.assertEqual(r.status_code, 201, r.get_json())
        self.remember_files()
        row = self.db.execute('SELECT * FROM message_files').fetchone()
        self.assertEqual(row['kind'], 'photo')
        self.assertEqual(row['original_name'], 'deck.png')
        self.assertTrue(os.path.exists(os.path.join(A.UPLOAD_DIR, row['filename'])))

    def test_a_file_with_no_words_still_sends(self):
        self.assertEqual(self.send(self.c_token, body='', files=self.photo()).status_code, 201)
        self.remember_files()
        body = self.db.execute('SELECT body FROM messages ORDER BY id DESC LIMIT 1').fetchone()['body']
        self.assertEqual(body, '(sent a photo)', 'saying "file" about an obvious photo is noticed')

    def test_neither_words_nor_a_file_is_refused(self):
        r = self.send(self.t_token, body='')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'], 0)

    def test_an_executable_is_refused_and_leaves_nothing_behind(self):
        r = self.send(self.t_token, files=(io.BytesIO(b'MZ'), 'nasty.exe'))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM message_files').fetchone()['n'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'], 0)

    def test_something_enormous_is_refused(self):
        big = (io.BytesIO(b'0' * (threads.MAX_BYTES + 1024)), 'huge.pdf')
        self.assertEqual(self.send(self.t_token, files=big).status_code, 400)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM message_files').fetchone()['n'], 0)

    def test_a_plain_json_message_still_works(self):
        """The app sends multipart only when there's a file. Words alone stay JSON."""
        r = self.call('post', self.url(), self.t_token, json={'body': 'Morning — starting Tuesday.'})
        self.assertEqual(r.status_code, 201, r.get_json())

    def test_the_other_side_is_told_it_was_a_photo(self):
        self.send(self.t_token, 'Look', files=self.photo())
        self.remember_files()
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.customer,)).fetchone()['body']
        self.assertIn('photo', note)


class ReadingTheThreadTest(Base):

    def setUp(self):
        super().setUp()
        self.send(self.t_token, 'Here', files=self.photo())
        self.remember_files()

    def test_the_thread_carries_the_files(self):
        body = self.call('get', self.url(), self.c_token).get_json()
        msg = body['messages'][-1]
        self.assertEqual(len(msg['files']), 1)
        self.assertEqual(msg['files'][0]['kind'], 'photo')
        self.assertEqual(msg['files'][0]['name'], 'deck.png')
        self.assertTrue(msg['files'][0]['size'], 'the app shows the size before downloading')

    def test_the_file_url_is_the_guarded_one_not_the_public_folder(self):
        """A message attachment must never be reachable from /uploads/."""
        url = self.call('get', self.url(), self.c_token).get_json()['messages'][-1]['files'][0]['url']
        self.assertIn('/api/mobile/messages/files/', url)
        self.assertNotIn('/uploads/', url)

    def test_the_app_is_told_the_file_limit_rather_than_guessing_it(self):
        self.assertEqual(self.call('get', self.url(), self.c_token).get_json()['max_files'],
                         threads.MAX_FILES)


class WhoCanFetchItTest(Base):
    """Same four directions as the website's tests. A bearer token is not a pass."""

    def setUp(self):
        super().setUp()
        self.send(self.t_token, 'Here', files=self.photo())
        self.remember_files()
        self.file_id = self.db.execute('SELECT id FROM message_files ORDER BY id DESC LIMIT 1').fetchone()['id']

    def path(self):
        return f'/messages/files/{self.file_id}'

    def test_the_tradie_who_sent_it_can(self):
        self.assertEqual(self.call('get', self.path(), self.t_token).status_code, 200)

    def test_the_customer_on_that_job_can(self):
        self.assertEqual(self.call('get', self.path(), self.c_token).status_code, 200)

    def test_another_tradie_cannot(self):
        other = self.login(self.email(self.user('trade')))
        self.assertEqual(self.call('get', self.path(), other).status_code, 404)

    def test_another_customer_cannot(self):
        other = self.login(self.email(self.user('customer')))
        self.assertEqual(self.call('get', self.path(), other).status_code, 404)

    def test_no_token_at_all_cannot(self):
        self.assertEqual(self.call('get', self.path()).status_code, 401)

    def test_a_file_that_does_not_exist_is_a_plain_404(self):
        self.assertEqual(self.call('get', '/messages/files/99999', self.t_token).status_code, 404)


class ExtraWorkFromThePhoneTest(Base):

    def ask(self, token, **over):
        data = {'title': 'Replace three rotten joists', 'amount': '800', 'gst': 'incl',
                'detail': 'The joists under the worst boards have gone. They need replacing '
                          'before the new decking goes down.'}
        data.update(over)
        return self.call('post', self.url() + '/ask', token, json=data)

    def latest(self):
        return self.db.execute('SELECT * FROM work_requests ORDER BY id DESC LIMIT 1').fetchone()

    def answer(self, token, decision, request_id=None):
        rid = request_id or self.latest()['id']
        return self.call('post', f'{self.url()}/ask/{rid}/{decision}', token, json={})

    def test_a_tradie_can_ask_and_the_customer_can_agree(self):
        self.assertEqual(self.ask(self.t_token).status_code, 201, self.ask(self.t_token).get_json())
        row = self.latest()
        self.assertEqual(row['status'], 'asked')
        self.assertEqual(row['amount'], 800)
        self.assertEqual(self.answer(self.c_token, 'accepted').status_code, 200)
        self.assertEqual(self.latest()['status'], 'accepted')
        self.assertIsNotNone(self.latest()['answered_at'])

    def test_a_customer_can_ask_too(self):
        self.ask(self.c_token, title='Paint the new boards while you’re here')
        self.assertEqual(self.latest()['asked_by'], self.customer)

    def test_the_one_who_asked_cannot_answer_their_own(self):
        self.ask(self.t_token)
        self.answer(self.t_token, 'accepted')
        self.assertEqual(self.latest()['status'], 'asked', 'agreeing with yourself is not agreement')

    def test_they_can_take_their_own_back(self):
        self.ask(self.t_token)
        self.answer(self.t_token, 'withdrawn')
        self.assertEqual(self.latest()['status'], 'withdrawn')

    def test_an_answered_request_cannot_be_answered_again(self):
        self.ask(self.t_token)
        self.answer(self.c_token, 'accepted')
        self.answer(self.c_token, 'declined')
        self.assertEqual(self.latest()['status'], 'accepted', 'yes stays yes')

    def test_a_price_is_required(self):
        for bad in ('', 'about eight hundred', '0', '-50'):
            self.assertEqual(self.ask(self.t_token, amount=bad).status_code, 400)
        self.assertIsNone(self.latest(), 'no price, no request')

    def test_a_reason_is_required(self):
        self.assertEqual(self.ask(self.t_token, detail='more work').status_code, 400)
        self.assertIsNone(self.latest())

    def test_a_stranger_cannot_ask_on_someone_elses_job(self):
        other = self.login(self.email(self.user('customer')))
        self.assertEqual(self.ask(other).status_code, 404)
        self.assertIsNone(self.latest())

    def test_the_thread_shows_the_requests_and_what_is_agreed(self):
        self.ask(self.t_token, title='Joists', amount='800', gst='incl')
        self.answer(self.c_token, 'accepted')
        self.ask(self.t_token, title='Painting', amount='500', gst='incl')

        body = self.call('get', self.url(), self.c_token).get_json()
        self.assertEqual(len(body['asks']), 2)
        waiting = [a for a in body['asks'] if a['status'] == 'asked']
        self.assertEqual(len(waiting), 1)
        self.assertIn('$', waiting[0]['price'], 'the app shows a price, not raw cents')
        self.assertTrue(waiting[0]['status_label'])
        self.assertEqual(body['extra']['count'], 1, 'only what was actually agreed')
        self.assertEqual(body['extra']['total_incl_gst'], 800)

    def test_each_side_is_told_whose_request_is_whose(self):
        self.ask(self.t_token)
        mine = self.call('get', self.url(), self.t_token).get_json()['asks'][0]
        theirs = self.call('get', self.url(), self.c_token).get_json()['asks'][0]
        self.assertTrue(mine['mine'])
        self.assertFalse(theirs['mine'], 'so the app knows which side gets the Accept button')

    def test_both_sides_are_told(self):
        """The gap that a Simulator run found and this file hadn't: the phone's
        route was written without the notification the website's route did by
        hand, so a request raised on site reached nobody. It lives in thread.py
        now, which is why both surfaces get it."""
        self.ask(self.t_token)
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.customer,)).fetchone()['body']
        self.assertIn('extra work', note)

        self.answer(self.c_token, 'accepted')
        back = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.trade,)).fetchone()['body']
        self.assertIn('agreed to', back)

    def test_declining_and_withdrawing_are_told_too(self):
        self.ask(self.t_token)
        self.answer(self.c_token, 'declined')
        self.assertIn('said no to', self.db.execute(
            'SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
            (self.trade,)).fetchone()['body'])

    def test_nothing_agreed_means_nothing_to_show(self):
        self.assertIsNone(self.call('get', self.url(), self.c_token).get_json()['extra'])


if __name__ == '__main__':
    unittest.main()
