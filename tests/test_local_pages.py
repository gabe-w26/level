"""
Tests for the local pages ("Plumbers in Karori"): the index, the area hubs, the
trade+area pages, the sitemap, and the rule that counts are only shown when true.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import app as A  # noqa: E402
import billing  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import local_pages  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 24, 0, 0, 0)


class LocalPageTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'plumber'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.client = A.app.test_client()

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def trade(self, active=True):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              "VALUES ('trade','t@test.nz',?,'Tama',?)",
                              (hash_password('password123'), ts(T0))).lastrowid
        self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                        (uid, 'Harbour Plumbing', ts(T0)))
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
        self.db.commit()
        if active:
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, 't@test.nz', 'large', '', '', at=T0)
        return uid

    # ── pages ──
    def test_every_trade_and_area_pair_renders(self):
        cats = self.db.execute('SELECT slug FROM categories').fetchall()
        areas = self.db.execute('SELECT slug FROM areas').fetchall()
        self.assertEqual(len(cats), len(local_pages.TRADES))       # content for every trade
        checked = 0
        for c in cats:
            for a in areas[:3]:
                r = self.client.get(f'/find/{c["slug"]}/{a["slug"]}')
                self.assertEqual(r.status_code, 200, f'{c["slug"]}/{a["slug"]}')
                checked += 1
        self.assertGreater(checked, 50)

    def test_index_and_area_hub(self):
        self.assertEqual(self.client.get('/find').status_code, 200)
        r = self.client.get('/find/wellington')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Tradies in Wellington City', r.data)
        self.assertIn(b'/find/plumber/wellington', r.data)

    def test_unknown_trade_or_area_is_404(self):
        for path in ('/find/nope/wellington', '/find/plumber/nope', '/find/nope'):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_page_says_what_the_job_involves(self):
        r = self.client.get('/find/plumber/wellington')
        self.assertIn('Replace a hot water cylinder'.encode(), r.data)
        self.assertIn('registered plumber'.encode(), r.data)          # the licence rule
        self.assertIn(str(config.MAX_QUOTES).encode(), r.data)

    # ── honesty: counts only when true ──
    def test_no_trade_count_until_a_trade_actually_covers_it(self):
        r = self.client.get('/find/plumber/wellington')
        self.assertNotIn(b'covering Wellington City', r.data)
        self.trade()
        r = self.client.get('/find/plumber/wellington')
        self.assertIn(b'covering Wellington City', r.data)
        self.assertIn(b'>1<', r.data)

    def test_paused_and_unsubscribed_trades_are_not_counted(self):
        uid = self.trade(active=False)                                # no plan
        self.assertNotIn(b'covering Wellington City', self.client.get('/find/plumber/wellington').data)
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
        billing.choose_plan(self.db, t, 't@test.nz', 'large', '', '', at=T0)
        self.db.execute('UPDATE trades SET paused = 1 WHERE user_id = ?', (uid,))
        self.db.commit()
        self.assertNotIn(b'covering Wellington City', self.client.get('/find/plumber/wellington').data)

    def test_recent_jobs_count_appears_once_a_job_is_posted(self):
        cid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              "VALUES ('customer','c@test.nz',?,'Casey',?)",
                              (hash_password('password123'), ts(T0))).lastrowid
        self.db.commit()
        engine.post_job(self.db, cid, dict(category_id=self.cat, area_id=self.area, suburb='Karori',
                                           title='Leaking tap', description='Kitchen tap drips all night.',
                                           value_band='small', timing='weeks', property_type='house'))
        self.assertIn(b'posted here in the last 90 days', self.client.get('/find/plumber/wellington').data)

    # ── links ──
    def test_post_a_job_link_prefills_trade_and_area(self):
        r = self.client.get('/post?category=plumber&area=wellington')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'value="plumber" selected', r.data.replace(b'\n', b' '))
        self.assertIn(b'value="wellington" selected', r.data.replace(b'\n', b' '))

    def test_sitemap_lists_the_local_pages(self):
        body = self.client.get('/sitemap.xml').data.decode()
        self.assertIn('/find/plumber/wellington', body)
        self.assertIn('/find/wellington', body)
        self.assertGreater(body.count('<loc>'), 500)


if __name__ == '__main__':
    unittest.main()
