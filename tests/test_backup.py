"""
Tests for backups: what goes in the zip, the nightly job on the server, and the
admin download.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'
os.environ['WAITLIST_DEFAULT'] = '0'   # these tests exercise the open site

import app as A  # noqa: E402
import backup  # noqa: E402
import db as dbmod  # noqa: E402
import integrations  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 24, 3, 0, 0)


class BackupTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.folder = tempfile.mkdtemp()
        self.admin = self.db.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id").fetchone()
        self.db.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                        (hash_password('password123'), self.admin['id']))
        self.db.execute("INSERT INTO users (role, email, password_hash, name, phone, created_at) "
                        "VALUES ('customer','casey@test.nz',?, 'Casey Brown','021 555 0101',?)",
                        (hash_password('password123'), ts(T0)))
        self.db.commit()

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def read(self, blob):
        return zipfile.ZipFile(io.BytesIO(blob))

    # ── what's in it ──
    def test_zip_has_a_csv_per_table_and_a_manifest(self):
        buf = io.BytesIO()
        manifest = backup.dump(self.db, buf)
        z = self.read(buf.getvalue())
        names = set(z.namelist())
        self.assertIn('manifest.json', names)
        for table in backup.table_names():
            self.assertIn(f'{table}.csv', names, table)
        # The local admin and the customer. There is no second admin unless
        # ADMIN2_PASSWORD is set — the hardcoded default was removed once the
        # repository turned out to be public.
        self.assertGreaterEqual(manifest['tables']['users'], 2)
        self.assertIn('users', json.loads(z.read('manifest.json'))['holds_personal_details'])

    def test_rows_come_back_readable(self):
        buf = io.BytesIO()
        backup.dump(self.db, buf)
        rows = list(csv.DictReader(io.StringIO(self.read(buf.getvalue()).read('users.csv').decode())))
        casey = [r for r in rows if r['email'] == 'casey@test.nz']
        self.assertEqual(len(casey), 1)
        self.assertEqual(casey[0]['name'], 'Casey Brown')
        self.assertEqual(casey[0]['closed_at'], '')                  # NULL becomes empty, not "None"

    def test_empty_table_still_gets_a_file(self):
        buf = io.BytesIO()
        backup.dump(self.db, buf)
        self.assertEqual(self.read(buf.getvalue()).read('quotes.csv').decode().strip(), '')

    # ── nightly ──
    def test_nightly_writes_one_a_day_and_keeps_the_last_few(self):
        first = backup.maybe_nightly(self.db, at=T0)
        self.assertTrue(first and os.path.exists(first))
        self.assertIsNone(backup.maybe_nightly(self.db, at=T0 + timedelta(hours=3)))   # too soon
        second = backup.maybe_nightly(self.db, at=T0 + timedelta(days=1))
        self.assertTrue(second and second != first)

    def test_nightly_tidies_up_old_files(self):
        for i in range(backup.KEEP_NIGHTLY + 3):
            backup.write_nightly(self.db, self.folder, at=T0 + timedelta(days=i))
        kept = [f for f in os.listdir(self.folder) if f.endswith('.zip')]
        self.assertEqual(len(kept), backup.KEEP_NIGHTLY)

    def test_a_broken_folder_never_breaks_the_sweep(self):
        with unittest.mock.patch.object(backup, 'write_nightly', side_effect=OSError('disk full')):
            self.assertIsNone(backup.maybe_nightly(self.db, at=T0))

    # ── the download ──
    def test_admin_can_download_and_others_cannot(self):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        self.assertEqual(c.get('/admin/backup').status_code, 302)          # signed out
        c.post('/login', data={'email': self.admin['email'], 'password': 'password123', '_csrf': 't'})
        r = c.get('/admin/backup')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'application/zip')
        self.assertIn('attachment; filename=', r.headers['Content-Disposition'])
        self.assertIn('users.csv', self.read(r.data).namelist())

    def test_setup_page_shows_the_backup_state(self):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['_csrf'] = 't'
        c.post('/login', data={'email': self.admin['email'], 'password': 'password123', '_csrf': 't'})
        self.assertIn(b'Download a backup', c.get('/admin/setup').data)



class TellsYouIfNobodyHasTakenACopyTest(BackupTest):
    """The nightly zips live on the server's own disk, which a free Render
    instance wipes on every deploy. "14 kept" can be true and worth nothing.
    The only number that says anything about surviving a lost database is when
    somebody last took a copy off the server — and nothing recorded it."""

    def test_it_starts_by_admitting_nobody_ever_has(self):
        st = backup.status(self.db)
        self.assertTrue(st['never_downloaded'])
        self.assertTrue(st['stale'])
        self.assertIsNone(st['downloaded_days_ago'])

    def test_downloading_one_is_remembered(self):
        backup.record_download(self.db)
        st = backup.status(self.db)
        self.assertFalse(st['never_downloaded'])
        self.assertEqual(st['downloaded_days_ago'], 0)
        self.assertFalse(st['stale'])

    def test_it_goes_stale_again(self):
        from datetime import timedelta
        from engine import utcnow
        backup.record_download(self.db)
        later = utcnow() + timedelta(days=8)
        st = backup.status(self.db, at=later)
        self.assertEqual(st['downloaded_days_ago'], 8)
        self.assertTrue(st['stale'], 'a week-old copy is not a backup of this week')

    def test_the_route_records_it(self):
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        admin = row['id'] if row else None
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = admin
            s['_csrf'] = 't'
        self.assertEqual(c.get('/admin/backup').status_code, 200)
        self.assertFalse(backup.status(self.db)['never_downloaded'])


class NagsWhenNobodyHasTakenACopyTest(BackupTest):
    """An in-app notice, not an email — email may not be set up yet, and
    "we couldn't warn you because the thing you hadn't set up wasn't set up"
    is not a defence."""

    def admins(self):
        return [r['id'] for r in self.db.execute(
            "SELECT id FROM users WHERE role = 'admin' AND closed_at IS NULL")]

    def notices(self):
        return [r['body'] for r in self.db.execute(
            "SELECT body FROM notifications WHERE body LIKE '%backup%'")]

    def test_it_tells_the_admins_when_nobody_ever_has(self):
        sent = backup.remind_admins(self.db)
        self.assertEqual(sent, len(self.admins()))
        self.assertTrue(any('nobody has ever' in n.lower() for n in self.notices()))

    def test_it_does_not_nag_again_the_same_week(self):
        backup.remind_admins(self.db)
        before = len(self.notices())
        backup.remind_admins(self.db)
        self.assertEqual(len(self.notices()), before, 'a reminder that repeats becomes wallpaper')

    def test_it_nags_again_the_week_after(self):
        from datetime import timedelta
        from engine import utcnow
        backup.remind_admins(self.db)
        before = len(self.notices())
        backup.remind_admins(self.db, at=utcnow() + timedelta(days=8))
        self.assertGreater(len(self.notices()), before)

    def test_a_fresh_download_stops_it(self):
        backup.record_download(self.db)
        self.assertEqual(backup.remind_admins(self.db), 0)

    def test_it_says_how_long_it_has_been_once_there_has_been_one(self):
        from datetime import timedelta
        from engine import utcnow
        backup.record_download(self.db)
        backup.remind_admins(self.db, at=utcnow() + timedelta(days=10))
        self.assertTrue(any('10 days ago' in n for n in self.notices()))


class DatabaseExpiryTest(BackupTest):
    """A free Postgres instance is deleted on a date, and nothing warns you."""

    def set_date(self, value):
        integrations._cache.update(at=10 ** 12, values={'db_expires': value})

    def test_nothing_is_claimed_when_nobody_has_said(self):
        self.set_date('')
        self.assertIsNone(backup.expiry(), 'a countdown to a made-up date is worse than none')

    def test_rubbish_is_ignored_rather_than_crashing_every_admin_page(self):
        self.set_date('next tuesday')
        self.assertIsNone(backup.expiry())

    def test_it_counts_down(self):
        from datetime import timedelta
        from engine import utcnow
        soon = (utcnow() + timedelta(days=30)).strftime('%Y-%m-%d')
        self.set_date(soon)
        out = backup.expiry()
        self.assertEqual(out['days'], 30)
        self.assertFalse(out['soon'])

    def test_it_gets_loud_a_fortnight_out(self):
        from datetime import timedelta
        from engine import utcnow
        self.set_date((utcnow() + timedelta(days=10)).strftime('%Y-%m-%d'))
        self.assertTrue(backup.expiry()['soon'])

    def test_a_date_already_past_says_so(self):
        from datetime import timedelta
        from engine import utcnow
        self.set_date((utcnow() - timedelta(days=3)).strftime('%Y-%m-%d'))
        self.assertTrue(backup.expiry()['gone'])

    def test_the_banner_only_shows_to_an_admin(self):
        from datetime import timedelta
        from engine import utcnow
        self.set_date((utcnow() + timedelta(days=5)).strftime('%Y-%m-%d'))
        row = self.db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = row['id']
            s['_csrf'] = 't'
        self.assertIn('database expires in', c.get('/admin').data.decode().lower())
        self.assertNotIn('database expires in',
                         A.app.test_client().get('/').data.decode().lower())

if __name__ == '__main__':
    import unittest.mock  # noqa: F401
    unittest.main()
