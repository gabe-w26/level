"""
The whole thing, through the front door, as four strangers.

Every other test file knows something about how this works — it calls
engine.post_job directly, or seeds a quote row. This one refuses to. It signs
up, posts, quotes, hires and reviews entirely through HTTP, the way a real
person would, because that is the only way to catch a gate added around the
edges that quietly breaks the middle.

That risk is not hypothetical here: a launch gate now sits in front of sign-up
and posting, referral links carry state through it, and the waitlist can refuse
a request outright. All of that is close enough to this path to break it.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'
os.environ['WAITLIST_DEFAULT'] = '0'          # the open site; the shut one is test_waitlist

import app as A  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
from schema import init_db  # noqa: E402


class TheWholeThingTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        A._login_failures.clear()
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})

    def tearDown(self):
        A._login_failures.clear()
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def browser(self):
        """A client that carries a CSRF token the way a real browser would."""
        c = A.app.test_client()
        self.refresh_token(c)
        return c

    def refresh_token(self, c, path='/login'):
        """Re-read the token off a page.

        Signing in clears the session, so the token from before a login is dead
        afterwards. A real browser never notices because it reads the token out
        of whatever page it is looking at; a test has to do the same or it ends
        up testing the CSRF check instead of the thing it meant to.
        """
        page = c.get(path).data.decode()
        found = re.search(r'name="_csrf" value="([^"]+)"', page)
        self.token = found.group(1) if found else 't'
        return self.token

    def sign_in(self, c, email, password, then='/'):
        c.post('/login', data=self.form(email=email, password=password), follow_redirects=True)
        self.refresh_token(c, then)
        return c

    def form(self, **fields):
        return dict(fields, _csrf=self.token)

    # ── the journey ──────────────────────────────────────────────────────────

    def test_a_job_goes_from_posted_to_reviewed_without_touching_the_engine(self):
        # 1. A tradie signs up from the front page.
        trade = self.browser()
        r = trade.post('/signup', data=self.form(
            role='trade', name='Wiremu Tane', email='wiremu@example.nz', phone='021 555 0101',
            password='a-long-enough-password', business_name='Tane Building'),
            follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        tid = self.db.execute("SELECT id FROM users WHERE email = 'wiremu@example.nz'").fetchone()
        self.assertIsNotNone(tid, 'the tradie never got an account')
        tid = tid['id']

        # They pick a trade and an area, and a plan, or no job can ever reach them.
        cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (tid, cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (tid, area))
        self.db.commit()
        import billing
        row = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (tid,)).fetchone()
        billing.choose_plan(self.db, row, 'wiremu@example.nz', 'large', '', '')
        self.db.commit()

        # 2. A homeowner posts a job through the form.
        home = self.browser()
        r = home.post('/post', data=self.form(
            category='builder', area='wellington', suburb='Karori',
            title='Replace the rotten deck boards',
            description='About twelve square metres. The boards are soft underfoot and two joists '
                        'look like they have gone as well. Treated pine to match.',
            value_band='medium', timing='weeks', property_type='house',
            name='Sam Walker', email='sam@example.nz', password='another-long-password',
            phone='021 555 0202'), follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        job = self.db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertIsNotNone(job, 'the job was never created')
        self.assertEqual(job['title'], 'Replace the rotten deck boards')

        # 3. It was offered to the tradie, by the rotation, without anyone asking.
        offer = self.db.execute(
            "SELECT * FROM offers WHERE job_id = ? AND trade_id = ? AND status = 'active'",
            (job['id'], tid)).fetchone()
        self.assertIsNotNone(offer, 'a matching trade was not offered the job')

        # 4. The tradie quotes, from their own session.
        trade2 = self.browser()
        self.sign_in(trade2, 'wiremu@example.nz', 'a-long-enough-password', then='/trade')
        r = trade2.post(f'/trade/jobs/{job["id"]}/quote', data=self.form(
            price_type='fixed', amount_low='6400', gst='incl',
            message='Two days on site. Timber supplied, old boards taken away, handrail checked.',
            warranty='2 years', available_from='In a couple of weeks'), follow_redirects=True)
        quote = self.db.execute('SELECT * FROM quotes WHERE job_id = ?', (job['id'],)).fetchone()
        self.assertIsNotNone(quote, f'the quote never landed (status {r.status_code})')
        self.assertEqual(quote['amount_low'], 6400)

        # 5. The customer sees it and hires them.
        home2 = self.browser()
        self.sign_in(home2, 'sam@example.nz', 'another-long-password',
                     then=f'/me/jobs/{job["id"]}')
        page = home2.get(f'/me/jobs/{job["id"]}').data.decode()
        self.assertIn('Tane Building', page, 'the customer cannot see who quoted')
        self.assertIn('6,400', page, 'the customer cannot see the price')

        home2.post(f'/me/jobs/{job["id"]}/quotes/{quote["id"]}/accept',
                   data=self.form(act_ack='1'), follow_redirects=True)
        after = engine.get_job(self.db, job['id'])
        self.assertEqual(after['status'], 'hired')
        self.assertEqual(after['hired_trade_id'], tid)

        # 6. And the two of them can talk about it.
        home2.post(f'/thread/{job["id"]}/{tid}',
                   data=self.form(body='Morning — when could you start?'),
                   content_type='multipart/form-data')
        msg = self.db.execute('SELECT * FROM messages WHERE job_id = ?', (job['id'],)).fetchone()
        self.assertIsNotNone(msg, 'the message never sent')

    def test_the_trade_is_not_offered_work_outside_their_area(self):
        """The rotation's whole promise, checked through the front door."""
        cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        here = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']

        trade = self.browser()
        trade.post('/signup', data=self.form(
            role='trade', name='Wiremu Tane', email='w@example.nz', phone='021 555 0101',
            password='a-long-enough-password', business_name='Tane Building'),
            follow_redirects=True)
        tid = self.db.execute("SELECT id FROM users WHERE email = 'w@example.nz'").fetchone()['id']
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (tid, cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (tid, here))
        self.db.commit()
        import billing
        row = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (tid,)).fetchone()
        billing.choose_plan(self.db, row, 'w@example.nz', 'large', '', '')
        self.db.commit()

        home = self.browser()
        home.post('/post', data=self.form(
            category='builder', area='akl-central', suburb='Ponsonby',
            title='Replace the rotten deck boards',
            description='Twelve square metres of soft boards, and the joists want a look at too.',
            value_band='medium', timing='weeks', property_type='house',
            name='Sam Walker', email='s@example.nz', password='another-long-password',
            phone='021 555 0202'), follow_redirects=True)
        job = self.db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertIsNotNone(job)
        offered = self.db.execute('SELECT COUNT(*) AS n FROM offers WHERE job_id = ? AND trade_id = ?',
                                  (job['id'], tid)).fetchone()['n']
        self.assertEqual(offered, 0, 'a Wellington builder was offered an Auckland job')


if __name__ == '__main__':
    unittest.main()
