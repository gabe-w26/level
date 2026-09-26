"""
Attachments, and asking to do extra work.

Two things matter more than the features themselves:

  · a photo of somebody's back door is between the two people on that job, and
    nobody else — four tests come at that from four directions;
  · extra work agreed in conversation and never written down is the commonest
    thing people fall out over, so a request has a price, an answer, and a date,
    and the person who asked can't be the one who says yes.

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
        self.trade = self.user('trade')
        self.customer = self.user('customer')
        self.job_id = self.a_job_with_a_quote()
        self.written = []

    def tearDown(self):
        for name in self.written:
            try:
                os.remove(os.path.join(A.UPLOAD_DIR, name))
            except OSError:
                pass
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer'):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'),
                               f'Person {self.n}', ts(utcnow()))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(utcnow())))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, f'u{self.n}@test.nz', 'large', '', '')
        self.db.commit()
        return uid

    def a_job_with_a_quote(self):
        """Posted now — the route uses the real clock, so a fixed date expires."""
        now = utcnow()
        job_id = engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Replace rotten deck boards',
            description='Twelve square metres, boards are soft.', value_band='medium',
            timing='weeks', property_type='house'), at=now)[0]
        engine.submit_quote(self.db, job_id, self.trade, dict(
            price_type='fixed', amount_low=6400, gst_included=1,
            message='Two days on site, timber supplied and the old stuff taken away.'),
            at=now + timedelta(minutes=1))
        return job_id

    def client(self, uid):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = uid
            s['_csrf'] = 't'
        return c

    def url(self):
        return f'/thread/{self.job_id}/{self.trade}'

    def send(self, uid, body='Here you go', files=None):
        data = {'body': body, '_csrf': 't'}
        if files:
            data['files'] = files
        return self.client(uid).post(self.url(), data=data, content_type='multipart/form-data')

    def photo(self, name='deck.png'):
        return (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 64), name)

    def remember_files(self):
        for r in self.db.execute('SELECT filename FROM message_files'):
            if r['filename'] not in self.written:
                self.written.append(r['filename'])


class AttachmentTest(Base):

    def test_a_photo_can_be_sent_in_a_message(self):
        self.send(self.trade, 'Found this under the boards', files=self.photo())
        self.remember_files()
        rows = self.db.execute('SELECT * FROM message_files').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'photo')
        self.assertEqual(rows[0]['original_name'], 'deck.png')
        self.assertTrue(os.path.exists(os.path.join(A.UPLOAD_DIR, rows[0]['filename'])))

    def test_a_file_with_no_words_still_sends(self):
        self.send(self.customer, body='', files=self.photo())
        self.remember_files()
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM message_files').fetchone()['n'], 1)
        body = self.db.execute('SELECT body FROM messages ORDER BY id DESC LIMIT 1').fetchone()['body']
        self.assertEqual(body, '(sent a photo)', 'the website says the same thing the app does')

    def test_an_executable_is_refused(self):
        self.send(self.trade, files=(io.BytesIO(b'MZ'), 'nasty.exe'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM message_files').fetchone()['n'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'], 0,
                         'and no message is left behind either')

    def test_something_enormous_is_refused(self):
        big = (io.BytesIO(b'0' * (threads.MAX_BYTES + 1024)), 'huge.pdf')
        self.send(self.trade, files=big)
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM message_files').fetchone()['n'], 0)

    def test_the_other_side_is_told_it_was_a_photo(self):
        self.send(self.trade, 'Look', files=self.photo())
        self.remember_files()
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.customer,)).fetchone()['body']
        self.assertIn('photo', note)


class WhoCanOpenItTest(Base):
    """A photo of somebody's back door is not public."""

    def setUp(self):
        super().setUp()
        self.send(self.trade, 'Here', files=self.photo())
        self.remember_files()
        self.file_id = self.db.execute('SELECT id FROM message_files ORDER BY id DESC LIMIT 1').fetchone()['id']

    def test_the_tradie_who_sent_it_can(self):
        self.assertEqual(self.client(self.trade).get(f'/messages/files/{self.file_id}').status_code, 200)

    def test_the_customer_on_that_job_can(self):
        self.assertEqual(self.client(self.customer).get(f'/messages/files/{self.file_id}').status_code, 200)

    def test_another_tradie_cannot(self):
        other = self.user('trade')
        self.assertEqual(self.client(other).get(f'/messages/files/{self.file_id}').status_code, 404)

    def test_another_customer_cannot(self):
        other = self.user('customer')
        self.assertEqual(self.client(other).get(f'/messages/files/{self.file_id}').status_code, 404)

    def test_a_stranger_is_sent_to_log_in(self):
        self.assertEqual(A.app.test_client().get(f'/messages/files/{self.file_id}').status_code, 302)


class ExtraWorkTest(Base):

    def ask(self, uid, **over):
        data = {'title': 'Replace three rotten joists', 'amount': '800', 'gst': 'incl', '_csrf': 't',
                'detail': 'The joists under the worst boards have gone. They need replacing before '
                          'the new decking goes down.'}
        data.update(over)
        return self.client(uid).post(self.url() + '/ask', data=data)

    def latest(self):
        return self.db.execute('SELECT * FROM work_requests ORDER BY id DESC LIMIT 1').fetchone()

    def test_a_tradie_can_ask_and_the_customer_can_agree(self):
        self.ask(self.trade)
        row = self.latest()
        self.assertEqual(row['status'], 'asked')
        self.assertEqual(row['amount'], 800)

        self.client(self.customer).post(self.url() + f'/ask/{row["id"]}/accepted', data={'_csrf': 't'})
        self.assertEqual(self.latest()['status'], 'accepted')
        self.assertIsNotNone(self.latest()['answered_at'])

    def test_a_customer_can_ask_too(self):
        """Either side. "While you're here" is as common as "I've found something"."""
        self.ask(self.customer, title='Paint the new boards while you’re here')
        self.assertEqual(self.latest()['asked_by'], self.customer)

    def test_the_one_who_asked_cannot_answer_their_own(self):
        self.ask(self.trade)
        row = self.latest()
        self.client(self.trade).post(self.url() + f'/ask/{row["id"]}/accepted', data={'_csrf': 't'})
        self.assertEqual(self.latest()['status'], 'asked', 'agreeing with yourself is not agreement')

    def test_they_can_take_their_own_back(self):
        self.ask(self.trade)
        row = self.latest()
        self.client(self.trade).post(self.url() + f'/ask/{row["id"]}/withdrawn', data={'_csrf': 't'})
        self.assertEqual(self.latest()['status'], 'withdrawn')

    def test_an_answered_request_cannot_be_answered_again(self):
        self.ask(self.trade)
        row = self.latest()
        self.client(self.customer).post(self.url() + f'/ask/{row["id"]}/accepted', data={'_csrf': 't'})
        self.client(self.customer).post(self.url() + f'/ask/{row["id"]}/declined', data={'_csrf': 't'})
        self.assertEqual(self.latest()['status'], 'accepted', 'yes stays yes')

    def test_a_price_is_required(self):
        for bad in ('', 'about eight hundred', '0', '-50'):
            self.ask(self.trade, amount=bad)
        self.assertIsNone(self.latest(), 'no price, no request')

    def test_a_reason_is_required(self):
        self.ask(self.trade, detail='more work')
        self.assertIsNone(self.latest(), 'the other person has to decide on it')

    def test_what_has_been_agreed_adds_up_with_gst(self):
        self.ask(self.trade, title='Joists', amount='800', gst='incl')
        self.client(self.customer).post(self.url() + f'/ask/{self.latest()["id"]}/accepted', data={'_csrf': 't'})
        self.ask(self.trade, title='Rubbish', amount='100', gst='excl')
        self.client(self.customer).post(self.url() + f'/ask/{self.latest()["id"]}/accepted', data={'_csrf': 't'})
        self.ask(self.trade, title='Painting', amount='500', gst='incl')     # left unanswered

        total = threads.extra_agreed(self.db, self.job_id, self.trade)
        self.assertEqual(total['count'], 2, 'only what was actually agreed')
        self.assertEqual(total['total_incl_gst'], 800 + 115)

    def test_nothing_agreed_means_nothing_shown(self):
        self.assertIsNone(threads.extra_agreed(self.db, self.job_id, self.trade))

    def test_a_stranger_cannot_ask_for_work_on_someone_elses_job(self):
        other = self.user('customer')
        self.assertEqual(self.ask(other).status_code, 404)
        self.assertIsNone(self.latest())

    def test_both_sides_are_told(self):
        self.ask(self.trade)
        note = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.customer,)).fetchone()['body']
        self.assertIn('extra work', note)
        self.client(self.customer).post(self.url() + f'/ask/{self.latest()["id"]}/accepted', data={'_csrf': 't'})
        back = self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                               (self.trade,)).fetchone()['body']
        self.assertIn('agreed to', back)


if __name__ == '__main__':
    unittest.main()
