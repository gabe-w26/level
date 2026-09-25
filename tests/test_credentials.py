"""
Tickets, cards and memberships.

The rule that makes this worth having rather than a folder of photos is that an
expired ticket stops counting on its own, the day it expires. Most of what's
here is about that, and about the file itself staying private.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import app as A  # noqa: E402
import credentials  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import trust  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 25, 9, 0, 0)
TODAY = T0.date()


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
        self.trade = self.user('trade')

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer', n=1):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              (role, f'{role}{n}@test.nz', hash_password('password123'),
                               f'Person {n}', ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, 'Karori Building', ts(T0)))
        self.db.commit()
        return uid

    def doc(self, expires=None, checked=True, kind='site_safe', name='Site Safe passport'):
        doc_id = self.db.execute(
            'INSERT INTO trade_documents (trade_id, kind, name, expires_on, checked_at, created_at) '
            'VALUES (?,?,?,?,?,?)',
            (self.trade, kind, name, expires.isoformat() if expires else None,
             ts(T0) if checked else None, ts(T0))).lastrowid
        self.db.commit()
        return doc_id

    def client(self, uid):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = uid
            s['_csrf'] = 't'
        return c


class ExpiryTest(Base):
    """The whole reason this isn't just file storage."""

    def test_an_expired_ticket_stops_counting_on_its_own(self):
        self.doc(expires=TODAY - timedelta(days=1))
        self.assertEqual(credentials.counting(self.db, self.trade, T0), [])
        [row] = credentials.for_trade(self.db, self.trade, T0)
        self.assertEqual(row['status']['key'], 'expired')
        self.assertIn('Ran out', row['status']['detail'])

    def test_a_ticket_expiring_today_still_counts(self):
        self.doc(expires=TODAY)
        self.assertEqual(len(credentials.counting(self.db, self.trade, T0)), 1)

    def test_one_running_out_soon_counts_but_says_so(self):
        self.doc(expires=TODAY + timedelta(days=10))
        [row] = credentials.for_trade(self.db, self.trade, T0)
        self.assertEqual(row['status']['key'], 'expiring')
        self.assertTrue(row['status']['counts'])

    def test_expiry_beats_being_checked(self):
        """A confirmed ticket that ran out last week is expired, not confirmed."""
        self.doc(expires=TODAY - timedelta(days=7), checked=True)
        [row] = credentials.for_trade(self.db, self.trade, T0)
        self.assertEqual(row['status']['key'], 'expired')
        self.assertFalse(row['status']['counts'])

    def test_nothing_counts_until_we_have_seen_it(self):
        self.doc(expires=TODAY + timedelta(days=365), checked=False)
        self.assertEqual(credentials.counting(self.db, self.trade, T0), [])
        [row] = credentials.for_trade(self.db, self.trade, T0)
        self.assertEqual(row['status']['key'], 'waiting')

    def test_a_date_already_past_is_refused_at_the_door(self):
        with self.assertRaises(credentials.CredentialError) as e:
            credentials.add(self.db, self.trade,
                            {'kind': 'first_aid', 'expires_on': (TODAY - timedelta(days=2)).isoformat()},
                            None, at=T0)
        self.assertIn('already passed', str(e.exception))


class WarningTest(Base):

    def notes(self):
        return [r['body'] for r in self.db.execute(
            'SELECT body FROM notifications WHERE user_id = ? ORDER BY id', (self.trade,))]

    def test_they_are_warned_a_month_out_while_it_can_still_be_renewed(self):
        self.doc(expires=TODAY + timedelta(days=20))
        credentials.warn_about_expiries(self.db, T0)
        said = [n for n in self.notes() if 'runs out' in n]
        self.assertEqual(len(said), 1)
        self.assertIn('20 days', said[0])

    def test_the_warning_is_not_repeated_every_sweep(self):
        self.doc(expires=TODAY + timedelta(days=20))
        for _ in range(5):
            credentials.warn_about_expiries(self.db, T0)
        self.assertEqual(len([n for n in self.notes() if 'runs out' in n]), 1)

    def test_they_are_told_again_once_it_has_actually_gone(self):
        doc_id = self.doc(expires=TODAY + timedelta(days=20))
        credentials.warn_about_expiries(self.db, T0)
        self.db.execute('UPDATE trade_documents SET expires_on = ? WHERE id = ?',
                        ((TODAY - timedelta(days=1)).isoformat(), doc_id))
        self.db.commit()
        credentials.warn_about_expiries(self.db, T0)
        self.assertTrue([n for n in self.notes() if 'stopped counting' in n])

    def test_a_ticket_with_no_expiry_is_never_warned_about(self):
        self.doc(expires=None)
        credentials.warn_about_expiries(self.db, T0)
        self.assertEqual(self.notes(), [])


class ScoreTest(Base):

    def trade_row(self):
        return self.db.execute('SELECT * FROM trades WHERE user_id = ?', (self.trade,)).fetchone()

    def test_each_checked_ticket_is_worth_points_up_to_the_cap(self):
        for i in range(trust.TICKET_CAP + 2):
            self.doc(expires=TODAY + timedelta(days=200), name=f'Ticket {i}')
        rows = trust.tickets(self.db, self.trade, T0)
        self.assertEqual(len(rows), trust.TICKET_CAP, 'more than the cap must not keep adding')
        self.assertEqual(sum(r['got'] for r in rows), trust.TICKET_EACH * trust.TICKET_CAP)

    def test_an_expired_ticket_takes_its_points_away_again(self):
        doc_id = self.doc(expires=TODAY + timedelta(days=200))
        before = trust.explain(self.db, self.trade_row())['score']
        self.db.execute('UPDATE trade_documents SET expires_on = ? WHERE id = ?',
                        ((TODAY - timedelta(days=1)).isoformat(), doc_id))
        self.db.commit()
        self.assertLess(trust.explain(self.db, self.trade_row())['score'], before)

    def test_the_customer_is_told_what_was_seen_but_never_shown_the_file(self):
        self.doc(expires=TODAY + timedelta(days=200))
        self.db.execute("UPDATE trade_documents SET reference = 'SS-99887', filename = 'secret.pdf'")
        self.db.commit()
        [shown] = credentials.public_list(self.db, self.trade, T0)
        self.assertEqual(shown['label'], 'Site Safe passport')
        self.assertNotIn('reference', shown)
        self.assertNotIn('filename', shown)
        self.assertNotIn('SS-99887', str(shown))


class PrivacyTest(Base):
    """A certificate has somebody's full name and registration number on it."""

    def setUp(self):
        super().setUp()
        self.doc_id = self.doc()
        self.db.execute("UPDATE trade_documents SET filename = 'ticket.pdf' WHERE id = ?", (self.doc_id,))
        self.db.commit()
        open(os.path.join(A.UPLOAD_DIR, 'ticket.pdf'), 'wb').write(b'%PDF-1.4 pretend')

    def tearDown(self):
        try:
            os.remove(os.path.join(A.UPLOAD_DIR, 'ticket.pdf'))
        except OSError:
            pass
        super().tearDown()

    def test_the_owner_can_open_it(self):
        self.assertEqual(self.client(self.trade).get(f'/documents/{self.doc_id}/file').status_code, 200)

    def test_another_trade_cannot(self):
        other = self.user('trade', n=2)
        self.assertEqual(self.client(other).get(f'/documents/{self.doc_id}/file').status_code, 404)

    def test_a_customer_cannot(self):
        customer = self.user('customer', n=3)
        self.assertEqual(self.client(customer).get(f'/documents/{self.doc_id}/file').status_code, 404)

    def test_a_stranger_is_sent_to_log_in(self):
        self.assertEqual(A.app.test_client().get(f'/documents/{self.doc_id}/file').status_code, 302)

    def test_an_admin_can_because_they_have_to_check_it(self):
        admin = self.db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id").fetchone()
        self.assertEqual(self.client(admin['id']).get(f'/documents/{self.doc_id}/file').status_code, 200)


class UploadTest(Base):

    def test_a_tradie_can_put_one_up_and_take_it_down(self):
        c = self.client(self.trade)
        c.post('/trade/documents', data={
            'kind': 'first_aid', 'name': 'First aid', 'issuer': 'St John',
            'expires_on': (TODAY + timedelta(days=300)).isoformat(), '_csrf': 't',
            'file': (io.BytesIO(b'%PDF-1.4 x'), 'cert.pdf')}, content_type='multipart/form-data')
        rows = credentials.for_trade(self.db, self.trade, T0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status']['key'], 'waiting')
        name = rows[0]['filename']
        c.post(f'/trade/documents/{rows[0]["id"]}/remove', data={'_csrf': 't'})
        self.assertEqual(credentials.for_trade(self.db, self.trade, T0), [])
        self.assertFalse(os.path.exists(os.path.join(A.UPLOAD_DIR, name)), 'the file should go too')

    def test_an_executable_is_not_a_certificate(self):
        c = self.client(self.trade)
        c.post('/trade/documents', data={'kind': 'first_aid', '_csrf': 't',
                                         'file': (io.BytesIO(b'MZ'), 'nasty.exe')},
               content_type='multipart/form-data')
        self.assertEqual(credentials.for_trade(self.db, self.trade, T0), [])

    def test_there_is_a_limit_on_how_many(self):
        for i in range(credentials.MAX_PER_TRADE):
            self.doc(name=f'T{i}')
        with self.assertRaises(credentials.CredentialError):
            credentials.add(self.db, self.trade, {'kind': 'first_aid'}, None, at=T0)

    def test_one_trade_cannot_remove_another_trades(self):
        doc_id = self.doc()
        other = self.user('trade', n=2)
        with self.assertRaises(credentials.CredentialError):
            credentials.remove(self.db, doc_id, other)


if __name__ == '__main__':
    unittest.main()
