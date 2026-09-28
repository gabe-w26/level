"""
The go-live checklist, and the one way it could do harm.

A list that says you're ready when you aren't is worse than no list, so the
tests that matter are the ones about it refusing to say so: every item is
derived from the running system, `ready` is false while anything blocking is
outstanding, and the things it genuinely cannot determine are named on the page
rather than quietly left off — because a checklist showing only what it can
measure reads as a complete one.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'
os.environ['WAITLIST_DEFAULT'] = '0'

import app as A  # noqa: E402
import backup  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import integrations  # noqa: E402
import launch  # noqa: E402
import waitlist as wl  # noqa: E402
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
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def item(self, key):
        return next(i for i in launch.check(self.db) if i['key'] == key)


class ItAsksTheSystemTest(Base):

    def test_email_is_blocking_and_off_until_it_is_configured(self):
        e = self.item('email')
        self.assertEqual(e['kind'], launch.BLOCKING)
        self.assertFalse(e['done'])
        integrations._cache.update(at=10 ** 12, values={
            'smtp_host': 'smtp.example.com', 'smtp_user': 'a@b.c', 'smtp_pass': 'x'})
        self.assertTrue(self.item('email')['done'], 'it reads the live setting, not a stored answer')

    def test_the_web_address_follows_the_setting(self):
        self.assertFalse(self.item('site_url')['done'])
        integrations._cache.update(at=10 ** 12, values={'site_url': 'https://level.co.nz'})
        self.assertTrue(self.item('site_url')['done'])

    def test_a_backup_download_ticks_its_own_box(self):
        self.assertFalse(self.item('backup')['done'])
        backup.record_download(self.db)
        self.assertTrue(self.item('backup')['done'])

    def test_the_expiry_date_ticks_its_own_box(self):
        self.assertFalse(self.item('db_expiry')['done'])
        integrations._cache.update(at=10 ** 12, values={'db_expires': '2026-11-12'})
        self.assertTrue(self.item('db_expiry')['done'])

    def test_coverage_needs_an_area_that_could_actually_open(self):
        self.assertFalse(self.item('coverage')['done'])
        for i in range(wl.READY_TRADES):
            wl.join(self.db, {'side': 'trade', 'email': f't{i}@test.nz', 'name': '',
                              'area': str(self.area), 'category': str(self.cat)})
        for i in range(wl.READY_CUSTOMERS):
            wl.join(self.db, {'side': 'customer', 'email': f'c{i}@test.nz', 'name': '',
                              'area': str(self.area)})
        self.assertTrue(self.item('coverage')['done'])


class ItWillNotSayReadyWhenItIsNotTest(Base):

    def test_blocking_items_keep_it_from_claiming_readiness(self):
        s = launch.summary(self.db)
        self.assertFalse(s['ready'])
        self.assertTrue(s['blocking'])

    def test_a_risk_alone_does_not_block(self):
        """You can open carrying a risk. You can't open without email."""
        integrations._cache.update(at=10 ** 12, values={
            'smtp_host': 'smtp.example.com', 'smtp_user': 'a@b.c', 'smtp_pass': 'x',
            'site_url': 'https://level.co.nz'})
        for i in range(wl.READY_TRADES):
            wl.join(self.db, {'side': 'trade', 'email': f't{i}@test.nz', 'name': '',
                              'area': str(self.area), 'category': str(self.cat)})
        for i in range(wl.READY_CUSTOMERS):
            wl.join(self.db, {'side': 'customer', 'email': f'c{i}@test.nz', 'name': '',
                              'area': str(self.area)})
        s = launch.summary(self.db)
        self.assertTrue(s['ready'], 'nothing blocking')
        self.assertTrue(s['risky'], 'but still carrying the backup risk')

    def test_the_count_matches_the_items(self):
        s = launch.summary(self.db)
        self.assertEqual(s['total'], len(s['items']))
        self.assertEqual(s['done'], sum(1 for i in s['items'] if i['done']))


class ItAdmitsWhatItCannotSeeTest(Base):

    def test_there_is_a_named_list_of_those_things(self):
        self.assertTrue(launch.CANNOT_CHECK)
        titles = ' '.join(t for t, _ in launch.CANNOT_CHECK).lower()
        for expected in ('domain', 'dkim', 'name', 'developer'):
            self.assertIn(expected, titles)

    def test_the_page_shows_them(self):
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = row['id']
            s['_csrf'] = 't'
        page = c.get('/admin/launch').data.decode()
        self.assertIn('can’t check', page)
        for title, _ in launch.CANNOT_CHECK:
            self.assertIn(title, page)

    def test_only_an_admin_sees_it(self):
        self.assertEqual(A.app.test_client().get('/admin/launch').status_code, 302)


if __name__ == '__main__':
    unittest.main()
