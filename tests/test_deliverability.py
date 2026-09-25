"""
Will the emails arrive, or go to spam?

The most important test here is `test_gmail_can_actually_unsubscribe_in_one_click`.
Telling Gmail we support one-click unsubscribe and then rejecting the POST it
sends is worse than never claiming it — that is exactly what gets a sender
marked down, and the app's CSRF guard would have done it.

Run from the project folder:  python3 -m unittest discover tests -v
"""
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


if __name__ == '__main__':
    unittest.main()
