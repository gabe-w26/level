"""
Tests for outreach: importing the prospect spreadsheet, matching a job to local
businesses, sending free-lead emails, the personal sign-up link and unsubscribe.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
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

import app as A  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import mailer  # noqa: E402
import outreach  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

HEADERS = ['ID', 'Trade', 'Business', 'First name', 'Email', 'Phone', 'Website', 'Based in', 'Wellington City',
           'Hutt Valley', 'Porirua & Kāpiti', 'Wairarapa', 'Licence / membership', 'What they do',
           'Source (where the contact details were found)', 'Checked on', 'Do not contact', 'Status']


def sheet_row(**kw):
    base = {'Trade': 'Plumber', 'Business': 'Harbour Plumbing', 'First name': 'John',
            'Email': 'info@harbourplumbing.co.nz', 'Phone': '021 111 2222', 'Website': 'https://harbourplumbing.co.nz',
            'Based in': 'Karori', 'Wellington City': 'Y', 'Hutt Valley': None, 'Porirua & Kāpiti': None,
            'Wairarapa': None, 'Source (where the contact details were found)': 'https://harbourplumbing.co.nz/contact',
            'Do not contact': 'N', 'Status': 'Not contacted'}
    base.update(kw)
    return base


class OutreachTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={'smtp_host': 'smtp.test', 'smtp_user': 'gabe@test.nz'})
        self.plumber = self.db.execute("SELECT id FROM categories WHERE slug = 'plumber'").fetchone()['id']
        self.wgtn = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.hutt = self.db.execute("SELECT id FROM areas WHERE slug = 'hutt'").fetchone()['id']
        self.admin = self.db.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id").fetchone()
        self.db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password('password123'), self.admin['id']))
        self.db.commit()

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    # ── helpers ──
    def job(self, area=None, status='open'):
        self.jobs_made = getattr(self, 'jobs_made', 0) + 1
        cid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                              ('customer', f'customer{self.jobs_made}@test.nz', hash_password('x' * 10), 'Casey',
                               ts(utcnow()))).lastrowid
        jid = self.db.execute(
            'INSERT INTO jobs (customer_id, category_id, area_id, suburb, title, description, value_band, status, '
            'created_at, closes_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (cid, self.plumber, area or self.wgtn, 'Karori', 'Replace hot water cylinder',
             'Our 180L cylinder is leaking from the base. Casey on 021 999 9999, 12 Smith St.', 'small', status,
             ts(utcnow()), ts(utcnow() + timedelta(days=14)))).lastrowid
        self.db.commit()
        return self.load_job(jid)

    def load_job(self, jid):
        return self.db.execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                               'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
                               'WHERE j.id = ?', (jid,)).fetchone()

    def load(self, *rows):
        return outreach.import_rows(self.db, [sheet_row(**r) for r in rows] if rows else [sheet_row()])

    def prospect(self, email='info@harbourplumbing.co.nz'):
        return self.db.execute('SELECT * FROM prospects WHERE email = ?', (email,)).fetchone()

    def client(self, login=False):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        if login:
            c.post('/login', data={'email': self.admin['email'], 'password': 'password123', '_csrf': 't'})
            with c.session_transaction() as s:
                s['_csrf'] = 't'
        return c

    # ── import ──
    def test_import_adds_then_updates_without_duplicates(self):
        self.assertEqual(self.load(), (1, 0, 0))
        self.assertEqual(self.load({'Phone': '04 000 0000'}), (0, 1, 0))
        p = self.prospect()
        self.assertEqual(p['phone'], '04 000 0000')
        self.assertEqual(p['first_name'], 'John')
        areas = {r['area_id'] for r in self.db.execute('SELECT area_id FROM prospect_areas WHERE prospect_id = ?', (p['id'],))}
        self.assertEqual(areas, {self.wgtn})

    def test_import_skips_unknown_trade_and_blank_business(self):
        self.assertEqual(self.load({'Trade': 'Astronaut'}, {'Business': ''}), (0, 0, 2))

    def test_import_never_switches_an_opt_out_back_on(self):
        self.load({'Do not contact': 'Y'})
        self.load()                                   # an older sheet saying N
        self.assertEqual(self.prospect()['do_not_contact'], 1)
        outreach.unsubscribe(self.db, self.prospect())
        self.load({'Status': 'Not contacted'})
        self.assertEqual(self.prospect()['status'], 'unsubscribed')

    def test_xlsx_upload_reads_the_prospects_tab(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = 'Start here'
        ws = wb.create_sheet('Prospects')
        ws.append(HEADERS)
        row = sheet_row()
        ws.append([row.get(h) for h in HEADERS])
        buf = io.BytesIO()
        wb.save(buf)
        rows = outreach.read_upload('outreach.xlsx', buf.getvalue())
        self.assertEqual(outreach.import_rows(self.db, rows), (1, 0, 0))

    # ── matching ──
    def test_matches_only_the_right_trade_area_and_contactable(self):
        self.load({},
                  {'Business': 'Hutt Plumbing', 'Email': 'hi@hutt.nz', 'Wellington City': None, 'Hutt Valley': 'Y'},
                  {'Business': 'No Email Plumbing', 'Email': ''},
                  {'Business': 'Opted Out Plumbing', 'Email': 'no@out.nz', 'Do not contact': 'Y'},
                  {'Business': 'Sparky Ltd', 'Email': 'hi@sparky.nz', 'Trade': 'Electrician'})
        names = [p['business_name'] for p in outreach.matches(self.db, self.job())]
        self.assertEqual(names, ['Harbour Plumbing'])

    def test_no_match_for_someone_already_on_level(self):
        self.load()
        self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                        ('trade', 'info@harbourplumbing.co.nz', 'x', 'John', ts(utcnow())))
        self.db.commit()
        self.assertEqual(outreach.matches(self.db, self.job()), [])

    # ── sending ──
    def test_send_personalises_and_counts(self):
        self.load()
        job = self.job()
        p = self.prospect()
        self.assertEqual(outreach.queue(self.db, job, [p['id']], 'Leaking 180L cylinder, needs replacing.', self.admin), 1)
        with mock.patch.object(mailer, 'send', return_value=True) as send:
            self.assertEqual(outreach.flush(self.db), 1)
        to, name, subject, body = send.call_args[0][:4]
        self.assertEqual(to, 'info@harbourplumbing.co.nz')
        self.assertIn('Free plumber lead in Karori', subject)
        self.assertTrue(body.startswith('Hi John,'))
        self.assertIn(f'/o/{p["token"]}?j={job["id"]}', body)
        self.assertIn('harbourplumbing.co.nz', body)
        self.assertNotIn('Smith St', body)            # only the admin's summary goes out
        self.assertEqual(send.call_args[1]['unsubscribe_url'].split('/o/')[1], f'{p["token"]}/stop')
        self.assertEqual(send.call_args[1]['reply_to'], 'gabe@test.nz')
        p = self.prospect()
        self.assertEqual((p['leads_sent'], p['status']), (1, 'sent'))
        # One email per business per job.
        self.assertEqual(outreach.matches(self.db, job), [])
        self.assertEqual(outreach.queue(self.db, job, [p['id']], 'Again', self.admin), 0)

    def test_stops_after_three_unanswered_leads(self):
        self.load()
        for _ in range(3):
            outreach.queue(self.db, self.job(), [self.prospect()['id']], 'A real job summary here.', self.admin)
            with mock.patch.object(mailer, 'send', return_value=True):
                outreach.flush(self.db)
        self.assertEqual(self.prospect()['leads_sent'], 3)
        self.assertEqual(outreach.matches(self.db, self.job()), [])

    def test_closed_job_or_opt_out_is_skipped_not_sent(self):
        self.load()
        job = self.job()
        outreach.queue(self.db, job, [self.prospect()['id']], 'A real job summary here.', self.admin)
        outreach.unsubscribe(self.db, self.prospect())
        with mock.patch.object(mailer, 'send', return_value=True) as send:
            outreach.flush(self.db)
        send.assert_not_called()
        self.load({'Business': 'Second Plumbing', 'Email': 'two@p.nz'})
        outreach.queue(self.db, job, [self.prospect('two@p.nz')['id']], 'A real job summary here.', self.admin)
        self.db.execute("UPDATE jobs SET status = 'hired' WHERE id = ?", (job['id'],))
        self.db.commit()
        with mock.patch.object(mailer, 'send', return_value=True) as send:
            outreach.flush(self.db)
        send.assert_not_called()

    def test_nothing_sends_without_email_set_up(self):
        integrations._cache.update(at=10 ** 12, values={})
        self.load()
        outreach.queue(self.db, self.job(), [self.prospect()['id']], 'A real job summary here.', self.admin)
        with mock.patch.object(mailer, 'send', return_value=True) as send:
            self.assertEqual(outreach.flush(self.db), 0)
        send.assert_not_called()

    # ── pages ──
    def test_admin_pages_and_send_flow(self):
        self.load()
        job = self.job()
        c = self.client(login=True)
        self.assertEqual(c.get('/admin/outreach').status_code, 200)
        page = c.get(f'/admin/outreach/job/{job["id"]}')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Harbour Plumbing', page.data)
        with mock.patch.object(A, '_outreach_send_now'):
            r = c.post(f'/admin/outreach/job/{job["id"]}',
                       data={'_csrf': 't', 'summary': 'Leaking 180L cylinder, needs replacing.',
                             'prospect': str(self.prospect()['id'])})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db.execute("SELECT COUNT(*) AS n FROM prospect_sends WHERE status = 'queued'").fetchone()['n'], 1)
        self.assertEqual(c.get('/admin/outreach/flyer').status_code, 200)
        self.assertIn(b'<svg', c.get('/admin/outreach/flyer?for=trades').data)

    def test_outreach_pages_need_an_admin(self):
        r = self.client().get('/admin/outreach')
        self.assertEqual(r.status_code, 302)

    def test_personal_link_prefills_signup_and_credits_it(self):
        self.load()
        p = self.prospect()
        c = self.client()
        r = c.get(f'/o/{p["token"]}')
        self.assertEqual(r.status_code, 302)
        page = c.get('/signup')
        self.assertIn(b'value="Harbour Plumbing"', page.data)
        self.assertIn(b'value="info@harbourplumbing.co.nz"', page.data)
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        with mock.patch.object(mailer, 'send', return_value=True):
            r = c.post('/signup', data={'_csrf': 't', 'name': 'John Harbour', 'business_name': 'Harbour Plumbing',
                                        'email': 'info@harbourplumbing.co.nz', 'phone': '021 111 2222',
                                        'password': 'longenough1', 'role': 'trade'})
        self.assertEqual(r.status_code, 302, r.data[:500])
        p = self.prospect()
        self.assertEqual(p['status'], 'signed_up')
        self.assertIsNotNone(p['user_id'])
        setup = c.get('/trade/setup')
        self.assertIn(f'value="{self.plumber}" checked'.encode(), setup.data.replace(b'\n', b' ').replace(b'  ', b' '))

    def test_unsubscribe_link(self):
        self.load()
        p = self.prospect()
        c = self.client()
        self.assertIn(b'Unsubscribe', c.get(f'/o/{p["token"]}/stop').data)
        r = c.post(f'/o/{p["token"]}/stop', data={'_csrf': 't'})
        self.assertIn(b'unsubscribed', r.data)
        self.assertEqual(self.prospect()['status'], 'unsubscribed')
        self.assertEqual(c.get('/o/nonsense/stop').status_code, 200)



class StandingTest(unittest.TestCase):
    """What the email says about how the job is doing. All of it has to be true."""

    def job(self, **over):
        row = {'quote_count': 0, 'area_name': 'Wellington City',
               'created_at': ts(datetime.utcnow() - timedelta(hours=3))}
        row.update(over)
        return row

    def test_an_untouched_job_says_so(self):
        spots, posted = outreach.standing(self.job())
        self.assertIn('No quotes on it yet', spots)
        self.assertIn('3 of 3', spots)
        self.assertEqual(posted, 'Posted 3 hours ago')

    def test_a_nearly_full_job_admits_it(self):
        """Losing a sign-up is cheaper than a tradie finding out we oversold it."""
        spots, _ = outreach.standing(self.job(quote_count=2))
        self.assertIn('2 quotes already in', spots)
        self.assertIn('1 of 3 slot left', spots)

    def test_a_full_job_does_not_pretend_there_is_room(self):
        spots, _ = outreach.standing(self.job(quote_count=3))
        self.assertIn('All 3 quote slots are taken', spots)
        self.assertIn('Wellington City', spots)

    def test_freshness_reads_naturally(self):
        now = datetime.utcnow()
        self.assertEqual(outreach.standing(self.job(created_at=ts(now)))[1], 'Posted in the last hour')
        self.assertEqual(outreach.standing(self.job(created_at=ts(now - timedelta(hours=30))))[1],
                         'Posted 1 day ago')
        self.assertEqual(outreach.standing(self.job(created_at=ts(now - timedelta(hours=60))))[1],
                         'Posted 2 days ago')

    def test_a_row_missing_the_columns_loses_a_line_not_the_email(self):
        """The send path builds the email from a joined row. If that row is ever
        short a column again, the email must still go."""
        spots, posted = outreach.standing({'area_name': 'Wellington City'})
        self.assertIn('No quotes on it yet', spots)
        self.assertIsNone(posted)

    def test_a_broken_date_loses_the_line_rather_than_the_email(self):
        self.assertIsNone(outreach.standing(self.job(created_at='not a date'))[1])

if __name__ == '__main__':
    unittest.main()
