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
os.environ['WAITLIST_DEFAULT'] = '0'   # these tests exercise the open site

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


class CoverWarningTest(Base):
    """Public liability is the one credential nobody was warned about.

    Tickets have had a 30-day warning since the feature was built. Insurance
    lives in a column on `trades` rather than in `trade_documents`, so it missed
    the machinery entirely — the first a tradie knew their cover had lapsed was
    a customer seeing "Insurance expired" on their quote.
    """

    def cover(self, expires, checked=True):
        self.db.execute('UPDATE trades SET insurance_insurer = ?, insurance_expiry = ?, '
                        'insurance_checked_at = ?, insurance_warned_at = NULL WHERE user_id = ?',
                        ('AMI', expires.isoformat() if expires else None,
                         ts(T0) if checked else None, self.trade))
        self.db.commit()

    def notes(self):
        return [r['body'] for r in self.db.execute(
            'SELECT body FROM notifications WHERE user_id = ? ORDER BY id', (self.trade,))]

    def state(self):
        return self.db.execute('SELECT insurance_warned_at FROM trades WHERE user_id = ?',
                               (self.trade,)).fetchone()['insurance_warned_at']

    def test_they_are_warned_a_month_out_while_it_can_still_be_renewed(self):
        self.cover(TODAY + timedelta(days=20))
        self.assertEqual(credentials.warn_about_cover(self.db, T0), 1)
        said = [n for n in self.notes() if 'runs out' in n]
        self.assertEqual(len(said), 1)
        self.assertIn('20 days', said[0])
        self.assertEqual(self.state(), 'soon')

    def test_it_is_not_repeated_every_sweep(self):
        self.cover(TODAY + timedelta(days=20))
        for _ in range(5):
            credentials.warn_about_cover(self.db, T0)
        self.assertEqual(len([n for n in self.notes() if 'runs out' in n]), 1)

    def test_they_are_told_again_the_day_it_actually_goes(self):
        self.cover(TODAY + timedelta(days=20))
        credentials.warn_about_cover(self.db, T0)
        self.cover(TODAY - timedelta(days=1))       # clears warned_at the way an edit does
        self.db.execute("UPDATE trades SET insurance_warned_at = 'soon' WHERE user_id = ?", (self.trade,))
        self.db.commit()
        credentials.warn_about_cover(self.db, T0)
        gone = [n for n in self.notes() if 'ran out' in n]
        self.assertEqual(len(gone), 1)
        self.assertIn('Insurance expired', gone[0], 'say what the customer is now seeing')
        self.assertEqual(self.state(), 'expired')

    def test_the_expired_notice_is_also_said_only_once(self):
        self.cover(TODAY - timedelta(days=40))
        for _ in range(4):
            credentials.warn_about_cover(self.db, T0)
        self.assertEqual(len([n for n in self.notes() if 'ran out' in n]), 1)

    def test_cover_we_never_checked_is_not_warned_about(self):
        # We would be telling them a certificate we never saw has expired.
        self.cover(TODAY - timedelta(days=5), checked=False)
        self.assertEqual(credentials.warn_about_cover(self.db, T0), 0)
        self.assertEqual(self.notes(), [])

    def test_no_expiry_recorded_is_never_warned_about(self):
        self.cover(None)
        self.assertEqual(credentials.warn_about_cover(self.db, T0), 0)
        self.assertEqual(self.notes(), [])

    def test_a_date_we_cannot_read_is_skipped_not_raised(self):
        self.db.execute("UPDATE trades SET insurance_insurer = 'AMI', insurance_expiry = 'next March', "
                        'insurance_checked_at = ? WHERE user_id = ?', (ts(T0), self.trade))
        self.db.commit()
        self.assertEqual(credentials.warn_about_cover(self.db, T0), 0)

    def test_renewing_re_arms_the_warning(self):
        """Or the second lapse passes in silence, which is the worse bug.

        Goes through the real setup form, because the clearing is a CASE
        expression in that UPDATE and nowhere else.
        """
        self.cover(TODAY - timedelta(days=2))
        credentials.warn_about_cover(self.db, T0)
        self.assertEqual(self.state(), 'expired')

        c = self.client(self.trade)
        r = c.post('/trade/setup', data={
            '_csrf': 't', 'business_name': 'Karori Building', 'years_trading': '9',
            'licence_type': 'none', 'insurance_insurer': 'AMI',
            'insurance_expiry': (TODAY + timedelta(days=400)).isoformat(),
            'categories': str(self.cat), 'areas': str(
                self.db.execute('SELECT id FROM areas LIMIT 1').fetchone()['id'])})
        self.assertIn(r.status_code, (200, 302), r.data[:400])
        self.assertIsNone(self.state(), 'a new expiry date must clear the old warning')

    def test_the_sweep_runs_it(self):
        # A warning nothing calls is a warning nobody gets. This is the wiring.
        self.cover(TODAY + timedelta(days=10))
        report = engine.sweep(self.db, at=T0)
        self.assertIn('cover_warned', report)
        self.assertEqual(report['cover_warned'], 1)


class AdminLapsedQueueTest(Base):
    """The operational half: somebody has to chase the certificate.

    The tradie is warned, and their badge tells the truth. Neither of those gets
    the new certificate onto the file — an admin has to ask. Before this there
    was no page anywhere that listed who had lapsed.
    """

    def setUp(self):
        super().setUp()
        self.admin = self.db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()['id']
        import billing
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (self.trade,)).fetchone()
        billing.choose_plan(self.db, t, 'trade1@test.nz', 'large', '', '')
        self.db.commit()

    def cover(self, expiry):
        self.db.execute('UPDATE trades SET insurance_insurer = ?, insurance_expiry = ?, '
                        'insurance_checked_at = ? WHERE user_id = ?',
                        ('AMI', expiry, ts(T0), self.trade))
        self.db.commit()

    def test_a_lapsed_trade_is_counted_and_listed_and_a_current_one_is_not(self):
        c = self.client(self.admin)
        past = (date.today() - timedelta(days=30)).isoformat()
        future = (date.today() + timedelta(days=300)).isoformat()

        self.cover(future)
        self.assertNotIn(b'insurance lapsed', c.get('/admin').data)
        self.assertNotIn(b'Karori Building', c.get('/admin/trades?show=lapsed').data)

        self.cover(past)
        self.assertIn(b'insurance lapsed', c.get('/admin').data)
        self.assertIn(b'Karori Building', c.get('/admin/trades?show=lapsed').data)

    def test_cover_that_was_never_checked_is_not_in_the_lapsed_queue(self):
        # It belongs in "need checking". Two different jobs, two different lists.
        self.db.execute('UPDATE trades SET insurance_insurer = ?, insurance_expiry = ?, '
                        'insurance_checked_at = NULL WHERE user_id = ?',
                        ('AMI', (date.today() - timedelta(days=30)).isoformat(), self.trade))
        self.db.commit()
        c = self.client(self.admin)
        self.assertNotIn(b'Karori Building', c.get('/admin/trades?show=lapsed').data)
        self.assertIn(b'Karori Building', c.get('/admin/trades?show=unchecked').data)
