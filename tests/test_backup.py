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


if __name__ == '__main__':
    import unittest.mock  # noqa: F401
    unittest.main()
