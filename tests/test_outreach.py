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
import config  # noqa: E402
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
        # Not "Free ..." — a subject opening on that word is a spam signal, and
        # deliverability.check_subject now asserts this one stays clean.
        self.assertIn('Plumber job in Karori', subject)
        self.assertFalse(subject.lower().startswith('free'))
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



class VolumeFollowsTheJobTest(unittest.TestCase):
    """How many emails go out is decided by what's actually happening on the job,
    not by a schedule. Queue one, then let the job move on underneath it."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.admin = self.db.execute("SELECT id, name, email FROM users WHERE role = 'admin' "
                                     'ORDER BY id').fetchone()
        self.customer = self.db.execute(
            'INSERT INTO users (role, email, password_hash, name, created_at) '
            "VALUES ('customer','sam@test.nz',?,'Sam',?)",
            (hash_password('x'), ts(utcnow()))).lastrowid
        self.db.commit()
        self.job_id = engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Deck repair',
            description='Twelve square metres of soft boards out the back.',
            value_band='medium', timing='weeks', property_type='house'))[0]
        pid = self.db.execute(
            'INSERT INTO prospects (business_name, email, category_id, token, status, leads_sent, '
            "do_not_contact, created_at) VALUES ('Sparky','p@x.co.nz',?,'tk','new',0,0,?)",
            (self.cat, ts(utcnow()))).lastrowid
        self.db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (pid, self.area))
        self.db.commit()
        self.job = self.db.execute('SELECT j.*, c.name AS category_name, a.name AS area_name FROM jobs j '
                                   'JOIN categories c ON c.id = j.category_id '
                                   'JOIN areas a ON a.id = j.area_id WHERE j.id = ?',
                                   (self.job_id,)).fetchone()
        self.pid = pid

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def queue_one(self):
        self.db.execute("DELETE FROM prospect_sends")
        self.db.commit()
        outreach.queue(self.db, self.job, [self.pid], 'A summary.', self.admin)

    def flush(self):
        with mock.patch.object(mailer, 'enabled', return_value=True), \
             mock.patch.object(mailer, 'send', return_value=True) as send:
            outreach._flush(self.db, 20)
        return send.call_count

    def test_a_queued_lead_goes_out_while_there_is_still_room(self):
        self.queue_one()
        self.assertEqual(self.flush(), 1)

    def test_it_is_dropped_once_the_job_has_all_its_quotes(self):
        """The email promises a slot. Three quotes in, there isn't one."""
        self.queue_one()
        self.db.execute('UPDATE jobs SET quote_count = ? WHERE id = ?',
                        (engine.config.MAX_QUOTES, self.job_id))
        self.db.commit()
        self.assertEqual(self.flush(), 0)
        self.assertEqual(self.db.execute("SELECT status FROM prospect_sends").fetchone()['status'],
                         'skipped')

    def test_it_is_dropped_once_the_customer_has_picked_someone(self):
        self.queue_one()
        self.db.execute("UPDATE jobs SET status = 'hired' WHERE id = ?", (self.job_id,))
        self.db.commit()
        self.assertEqual(self.flush(), 0)

    def test_it_is_dropped_once_every_slot_is_taken_by_trades_already_here(self):
        """Nothing to offer: fifteen locals hold it, even with no quotes in yet."""
        self.queue_one()
        now = ts(utcnow())
        for i in range(engine.config.TRADES_PER_JOB):
            self.db.execute('INSERT INTO offers (job_id, trade_id, wave, status, offered_at, expires_at) '
                            "VALUES (?,?,1,'active',?,?)", (self.job_id, 9000 + i, now, now))
        self.db.commit()
        self.assertEqual(self.flush(), 0)

    def test_the_business_keeps_its_three_emails_for_a_job_it_can_act_on(self):
        """A dropped lead must not count against them."""
        self.queue_one()
        self.db.execute("UPDATE jobs SET status = 'hired' WHERE id = ?", (self.job_id,))
        self.db.commit()
        self.flush()
        self.assertEqual(self.db.execute('SELECT leads_sent FROM prospects WHERE id = ?',
                                         (self.pid,)).fetchone()['leads_sent'], 0)


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


class FunnelTest(unittest.TestCase):
    """From the link in the email to holding the job. This is the whole point of
    the outreach engine, and every step of it used to forget why they came."""

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

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def a_job(self):
        cust = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                               "VALUES ('customer','sam@test.nz',?,'Sam',?)",
                               (hash_password('password123'), ts(utcnow()))).lastrowid
        self.db.commit()
        return engine.post_job(self.db, cust, dict(
            category_id=self.cat, area_id=self.area, suburb='Thorndon', title='Reclad a small lean-to',
            description='Three by four metres of weatherboard off the back of the kitchen.',
            value_band='medium', timing='weeks', property_type='house'))[0]

    def a_prospect(self):
        pid = self.db.execute(
            'INSERT INTO prospects (business_name, first_name, email, category_id, token, status, '
            "leads_sent, do_not_contact, created_at) VALUES (?,?,?,?,?,'new',0,0,?)",
            ('Karori Building', 'John', 'john@karori.co.nz', self.cat, 'tok-1', ts(utcnow()))).lastrowid
        self.db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (pid, self.area))
        self.db.commit()
        return pid

    def test_the_job_follows_them_all_the_way_in(self):
        job_id = self.a_job()
        self.a_prospect()
        c = A.app.test_client()

        c.get(f'/o/tok-1?j={job_id}', follow_redirects=False)
        for page in ('/signup',):
            body = c.get(page).data.decode()
            self.assertIn('You’re signing up to quote on', body, page)
            self.assertIn('Reclad a small lean-to', body, page)
            self.assertIn('Thorndon', body, page)

        with c.session_transaction() as sess:
            sess['_csrf'] = 't'
        c.post('/signup', data={'role': 'trade', 'name': 'John Smith', 'business_name': 'Karori Building',
                                'email': 'john@karori.co.nz', 'phone': '021 123 4567',
                                'password': 'password123', '_csrf': 't'})
        uid = self.db.execute("SELECT id FROM users WHERE email = 'john@karori.co.nz'").fetchone()
        self.assertIsNotNone(uid, 'sign-up should have worked')
        # Logging in clears the session, so the form token has to be re-established
        # exactly as a real browser would pick up the new one.
        with c.session_transaction() as sess:
            sess['_csrf'] = 't'

        # Their trade and area are pre-ticked, and the job is still in front of them.
        setup = c.get('/trade/setup').data.decode()
        self.assertIn('Reclad a small lean-to', setup)

        c.post('/trade/setup', data={'business_name': 'Karori Building', 'categories': str(self.cat),
                                     'areas': str(self.area), 'licence_type': 'none', '_csrf': 't'})
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM trade_categories WHERE trade_id = ?',
                                         (uid['id'],)).fetchone()['n'], 1, 'the profile should have saved')
        self.assertIn('Reclad a small lean-to', c.get('/trade/plan').data.decode())

        # Picking a plan is the moment they can take work: they should land on
        # that job, holding it.
        r = c.post('/trade/plan/choose', data={'tier': 'large', '_csrf': 't'})
        self.assertEqual(r.headers['Location'].rstrip('/').split('/')[-1], str(job_id),
                         'they should land on the job they came for')
        offer = engine.get_offer(self.db, job_id, uid['id'])
        self.assertIsNotNone(offer, 'and be holding it')
        self.assertEqual(offer['status'], 'active')

    def test_a_job_that_filled_up_is_not_dangled_in_front_of_them(self):
        """Promising a slot that has gone is the fastest way to lose someone."""
        job_id = self.a_job()
        self.db.execute('UPDATE jobs SET quote_count = ? WHERE id = ?', (engine.config.MAX_QUOTES, job_id))
        self.db.commit()
        self.assertIsNone(outreach.the_job(self.db, job_id))

    def test_a_closed_job_is_not_dangled_either(self):
        job_id = self.a_job()
        self.db.execute("UPDATE jobs SET status = 'expired' WHERE id = ?", (job_id,))
        self.db.commit()
        self.assertIsNone(outreach.the_job(self.db, job_id))


class DailyCapTest(unittest.TestCase):
    """Going past a free Gmail's daily limit locks the mailbox for 24 hours and
    takes password resets down with it. So we stop first — and leads stop well
    before anything a person is waiting on."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        self.db = dbmod.get_db()

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def already_sent(self, n, hours_ago=1):
        """Pretend n emails went out that recently."""
        when = ts(utcnow() - timedelta(hours=hours_ago))
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              "VALUES ('customer','x@test.nz','x','X',?)", (when,)).lastrowid
        for _ in range(n):
            self.db.execute('INSERT INTO notifications (user_id, body, created_at, emailed_at) '
                            'VALUES (?,?,?,?)', (uid, 'hi', when, when))
        self.db.commit()

    def test_it_counts_what_actually_went_out(self):
        self.assertEqual(mailer.sent_today(self.db), 0)
        self.already_sent(5)
        self.assertEqual(mailer.sent_today(self.db), 5)

    def test_yesterdays_email_does_not_count_against_today(self):
        self.already_sent(5, hours_ago=30)
        self.assertEqual(mailer.sent_today(self.db), 0)

    def test_leads_stop_long_before_the_things_people_wait_on(self):
        # The effective cap, not the override — it now follows the provider.
        share = int(mailer.daily_cap() * config.MAIL_OUTREACH_SHARE)
        self.already_sent(share)
        self.assertEqual(mailer.allowance(self.db, 'outreach'), 0, 'leads should be done')
        self.assertGreater(mailer.allowance(self.db, 'alert'), 0,
                           'password resets must still have room')

    def test_the_cap_is_a_floor_of_zero_not_a_negative(self):
        self.already_sent(mailer.daily_cap() + 50)
        self.assertEqual(mailer.allowance(self.db, 'alert'), 0)
        self.assertEqual(mailer.allowance(self.db, 'outreach'), 0)

    def test_a_capped_flush_sends_nothing_rather_than_erroring(self):
        self.already_sent(mailer.daily_cap())
        with mock.patch.object(mailer, 'enabled', return_value=True):
            self.assertEqual(mailer.flush(self.db), 0)
        self.assertEqual(outreach._flush(self.db, 20), 0)

if __name__ == '__main__':
    unittest.main()
