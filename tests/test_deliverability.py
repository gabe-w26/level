"""
Will the emails arrive, or go to spam?

The most important test here is `test_gmail_can_actually_unsubscribe_in_one_click`.
Telling Gmail we support one-click unsubscribe and then rejecting the POST it
sends is worse than never claiming it — that is exactly what gets a sender
marked down, and the app's CSRF guard would have done it.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
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
import db as dbmod  # noqa: E402
import deliverability as dl  # noqa: E402
import integrations  # noqa: E402
import mailer  # noqa: E402
import outreach  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402


class HeaderTest(unittest.TestCase):
    """What actually goes out on the wire."""

    def build(self, **over):
        return mailer._build(over.get('to', 'someone@example.com'), 'Someone',
                             over.get('subject', 'Builder job in Karori'), 'Body text',
                             unsubscribe_url=over.get('unsub', 'https://level.co.nz/o/abc/stop'))

    def test_one_click_unsubscribe_is_declared_properly(self):
        msg = self.build()
        self.assertEqual(msg['List-Unsubscribe'], '<https://level.co.nz/o/abc/stop>')
        self.assertEqual(msg['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click',
                         'Gmail and Yahoo have expected this from bulk senders since Feb 2024')

    def test_a_message_carries_a_date_and_an_id(self):
        """Not all servers add these, and a message without them looks generated."""
        msg = self.build()
        self.assertTrue(msg['Date'])
        self.assertTrue(msg['Message-ID'].startswith('<') and '@' in msg['Message-ID'])

    def test_the_unsubscribe_link_is_in_the_body_as_well(self):
        """The header is for the mail app; the line is for the person reading it."""
        self.assertIn('Stop these emails', self.build().get_content())

    def test_no_unsubscribe_headers_on_a_password_reset(self):
        """A transactional email isn't something you unsubscribe from."""
        msg = mailer._build('a@b.nz', None, 'Reset your password', 'Link')
        self.assertIsNone(msg['List-Unsubscribe'])


class ChecksTest(unittest.TestCase):

    def test_it_catches_sending_as_one_domain_while_logging_in_as_another(self):
        """The commonest own-goal, and close to a guaranteed spam folder."""
        sender = {'shown': 'Level <hello@leveltrades.co.nz>', 'login': 'someone@gmail.com',
                  'domain': 'leveltrades.co.nz', 'login_domain': 'gmail.com'}
        check = dl.check_alignment(sender)
        self.assertEqual(check['level'], 'fail')
        self.assertIn('leveltrades.co.nz', check['headline'])
        self.assertIn('gmail.com', check['headline'])

    def test_matching_domains_pass(self):
        sender = {'shown': 'Level <hi@leveltrades.co.nz>', 'login': 'hi@leveltrades.co.nz',
                  'domain': 'leveltrades.co.nz', 'login_domain': 'leveltrades.co.nz'}
        self.assertEqual(dl.check_alignment(sender)['level'], 'ok')

    def test_a_personal_gmail_is_flagged_for_bulk_sending(self):
        check = dl.check_gmail_for_bulk({'login_domain': 'gmail.com'})
        self.assertEqual(check['level'], 'warn')
        self.assertIn('suspend', check['detail'])

    def test_a_subject_opening_on_free_is_flagged(self):
        self.assertEqual(dl.check_subject('Free builder lead in Karori')['level'], 'warn')
        self.assertEqual(dl.check_subject('Builder job in Karori: deck repair')['level'], 'ok')

    def test_shouting_is_flagged(self):
        self.assertEqual(dl.check_subject('URGENT BUILDER JOB NOW')['level'], 'fail')

    def test_the_real_outreach_subject_passes_its_own_check(self):
        """It used to open with "Free". This is the test that keeps it fixed."""
        job = {'category_name': 'Builder', 'suburb': 'Thorndon', 'area_name': 'Wellington City',
               'title': 'Reclad a small lean-to', 'value_band': 'medium', 'timing': 'weeks',
               'description': 'Three by four metres of weatherboard.', 'id': 1,
               'quote_count': 0, 'created_at': ts(utcnow())}
        prospect = {'first_name': 'John', 'business_name': 'X', 'token': 't',
                    'website': 'x.co.nz', 'email': 'j@x.co.nz'}
        subject, _body, _stop = outreach.compose(prospect, job, 'A summary.', 'Sam')
        self.assertEqual(dl.check_subject(subject)['level'], 'ok', subject)

    def test_dns_failures_never_blow_up_the_page(self):
        with mock.patch.object(dl, '_txt', side_effect=Exception('no network')):
            with self.assertRaises(Exception):
                dl._txt('x')                       # the mock itself raises
        with mock.patch.object(dl, '_txt', return_value=[]):
            self.assertEqual(dl.check_spf('nothing.invalid')['level'], 'fail')
            self.assertEqual(dl.check_dkim('nothing.invalid')['level'], 'warn')
            self.assertEqual(dl.check_dmarc('nothing.invalid')['level'], 'warn')

    def test_two_spf_records_is_worse_than_one(self):
        with mock.patch.object(dl, '_txt', return_value=['v=spf1 include:a ~all', 'v=spf1 include:b ~all']):
            check = dl.check_spf('x.nz')
        self.assertEqual(check['level'], 'fail')
        self.assertIn('Merge', check['detail'])

    def test_the_report_is_worst_case(self):
        with mock.patch.object(dl, '_txt', return_value=[]):
            with mock.patch.object(dl, 'from_address', return_value={
                    'shown': 'a@x.nz', 'login': 'b@y.nz', 'domain': 'x.nz', 'login_domain': 'y.nz'}):
                report = dl.report('Free stuff')
        self.assertEqual(report['worst'], 'fail')
        self.assertGreaterEqual(report['fails'], 1)


class OneClickTest(unittest.TestCase):
    """Gmail sends one-click unsubscribe as a POST from its own servers: no
    cookie, no form token. The CSRF guard would have returned 400."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def test_gmail_can_actually_unsubscribe_in_one_click(self):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, unsub_token, '
                              "email_alerts, created_at) VALUES ('trade','t@x.nz',?,'T','tok-1',1,?)",
                              (hash_password('x'), ts(utcnow()))).lastrowid
        self.db.commit()
        # No session, no cookie, no _csrf — exactly what arrives from Google.
        r = A.app.test_client().post('/unsubscribe/tok-1')
        self.assertEqual(r.status_code, 200, 'a 400 here tells Gmail we ignore unsubscribes')
        self.assertEqual(self.db.execute('SELECT email_alerts FROM users WHERE id = ?',
                                         (uid,)).fetchone()['email_alerts'], 0)

    def test_a_prospect_can_be_unsubscribed_in_one_click_too(self):
        self.db.execute('INSERT INTO prospects (business_name, email, category_id, token, status, '
                        "leads_sent, do_not_contact, created_at) VALUES ('X','x@y.nz',1,'ptok','sent',1,0,?)",
                        (ts(utcnow()),))
        self.db.commit()
        r = A.app.test_client().post('/o/ptok/stop')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.execute("SELECT status FROM prospects WHERE token = 'ptok'")
                         .fetchone()['status'], 'unsubscribed')

    def test_everything_else_still_needs_a_token(self):
        """The exemption must be those two endpoints and nothing else."""
        r = A.app.test_client().post('/login', data={'email': 'a@b.nz', 'password': 'x'})
        self.assertEqual(r.status_code, 400)



class ResendTest(unittest.TestCase):
    """The other way out. Same message, different transport."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def use_resend(self, secret='whsec_' + __import__('base64').b64encode(b'k' * 24).decode()):
        integrations.save(self.db, {'resend_key': 're_test_key', 'resend_secret': secret})
        integrations.refresh(self.db, force=True)
        return secret

    def test_a_key_switches_the_transport_without_smtp(self):
        self.assertIsNone(mailer.how())
        self.use_resend()
        self.assertEqual(mailer.how(), 'resend')
        self.assertTrue(mailer.enabled(), 'no SMTP host, but it can still send')

    def test_the_message_sent_to_resend_keeps_its_unsubscribe_headers(self):
        self.use_resend()
        captured = {}

        class FakeResponse:
            def read(self):
                return b'{"id":"abc"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(request, timeout=None):
            captured['url'] = request.full_url
            captured['auth'] = request.headers.get('Authorization')
            captured['body'] = __import__('json').loads(request.data.decode())
            return FakeResponse()

        with mock.patch.object(mailer.urllib.request, 'urlopen', fake_open):
            ok = mailer.send('t@example.com', 'T', 'Builder job in Karori', 'Body',
                             unsubscribe_url='https://level.co.nz/o/abc/stop')
        self.assertTrue(ok)
        self.assertEqual(captured['url'], 'https://api.resend.com/emails')
        self.assertEqual(captured['auth'], 'Bearer re_test_key')
        body = captured['body']
        self.assertEqual(body['to'], ['t@example.com'])
        self.assertIn('Body', body['text'])
        self.assertNotIn('html', body, 'plain text is deliberate')
        self.assertEqual(body['headers']['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')

    def test_the_daily_cap_follows_whichever_provider_is_sending(self):
        """Getting this wrong is quiet: the sends just start failing."""
        import config
        self.assertEqual(mailer.daily_cap(), config.MAIL_CAP_SMTP)
        self.use_resend()
        self.assertEqual(mailer.daily_cap(), config.MAIL_CAP_RESEND)
        self.assertLess(config.MAIL_CAP_RESEND, config.MAIL_CAP_SMTP,
                        'Resend’s free plan is the tighter of the two')

    def test_an_explicit_cap_beats_both_defaults(self):
        import config
        self.use_resend()
        with mock.patch.object(config, 'MAIL_DAILY_CAP', 5000):
            self.assertEqual(mailer.daily_cap(), 5000)

    def test_a_refusal_is_reported_not_raised(self):
        self.use_resend()
        err = mailer.urllib.error.HTTPError('u', 422, 'no', {}, io.BytesIO(b'{"message":"bad domain"}'))
        with mock.patch.object(mailer.urllib.request, 'urlopen', side_effect=err):
            self.assertFalse(mailer.send('t@example.com', None, 'S', 'B'))


class BounceHookTest(unittest.TestCase):
    """A public endpoint that stops emails. It had better be signed."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        import base64
        self.key = b'k' * 24
        self.secret = 'whsec_' + base64.b64encode(self.key).decode()
        integrations.save(self.db, {'resend_secret': self.secret})
        integrations.refresh(self.db, force=True)
        self.db.execute('INSERT INTO prospects (business_name, email, category_id, token, status, '
                        "leads_sent, do_not_contact, created_at) VALUES ('X','dead@x.co.nz',1,'tk','sent',1,0,?)",
                        (ts(utcnow()),))
        self.db.commit()

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def post(self, kind='email.bounced', sign=True, email='dead@x.co.nz', stamp=None):
        import base64, hashlib, hmac, json as js, time as t
        raw = js.dumps({'type': kind, 'data': {'to': [email]}}).encode()
        msg_id, stamp = 'msg_1', str(stamp or int(t.time()))
        headers = {'Content-Type': 'application/json'}
        if sign:
            signed = f'{msg_id}.{stamp}.'.encode() + raw
            sig = base64.b64encode(hmac.new(self.key, signed, hashlib.sha256).digest()).decode()
            headers.update({'svix-id': msg_id, 'svix-timestamp': stamp, 'svix-signature': f'v1,{sig}'})
        return A.app.test_client().post('/hooks/resend', data=raw, headers=headers)

    def status(self):
        return self.db.execute("SELECT status, do_not_contact FROM prospects WHERE email = 'dead@x.co.nz'"
                               ).fetchone()

    def test_a_signed_bounce_stops_that_address_for_good(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        row = self.status()
        self.assertEqual(row['status'], 'bounced')
        self.assertEqual(row['do_not_contact'], 1)

    def test_a_spam_complaint_counts_too(self):
        self.post(kind='email.complained')
        self.assertEqual(self.status()['status'], 'bounced')

    def test_an_unsigned_post_changes_nothing(self):
        """Otherwise anyone could stop us emailing a rival."""
        self.assertEqual(self.post(sign=False).status_code, 403)
        self.assertEqual(self.status()['do_not_contact'], 0)

    def test_a_wrong_signature_changes_nothing(self):
        import base64, hashlib, hmac, json as js, time as t
        raw = js.dumps({'type': 'email.bounced', 'data': {'to': ['dead@x.co.nz']}}).encode()
        stamp = str(int(t.time()))
        bad = base64.b64encode(hmac.new(b'wrong' * 5, b'x', hashlib.sha256).digest()).decode()
        r = A.app.test_client().post('/hooks/resend', data=raw, headers={
            'svix-id': 'msg_1', 'svix-timestamp': stamp, 'svix-signature': f'v1,{bad}'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.status()['do_not_contact'], 0)

    def test_an_old_replayed_event_is_refused(self):
        self.assertEqual(self.post(stamp=1).status_code, 403)

    def test_an_event_we_do_not_care_about_is_shrugged_off(self):
        r = self.post(kind='email.delivered')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.status()['do_not_contact'], 0)

    def test_a_bounced_address_is_never_emailed_again(self):
        self.post()
        job = self.db.execute('SELECT * FROM jobs LIMIT 1').fetchone()
        self.assertIsNone(job, 'no jobs here; the list filter is what matters')
        self.assertEqual(self.db.execute("SELECT COUNT(*) AS n FROM prospects WHERE do_not_contact = 0 "
                                         "AND email = 'dead@x.co.nz'").fetchone()['n'], 0)

if __name__ == '__main__':
    unittest.main()
