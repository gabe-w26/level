"""
Backups.

Two kinds, because they protect against different things:

  • **Nightly, on the server.** Once a day the sweep writes a zip to the disk
    that holds the job photos, and keeps the last KEEP_NIGHTLY. This survives a
    bad deploy or a bad migration.
  • **Download, from Admin → Setup.** A zip you can put somewhere that isn't
    Render. This is the one that survives losing the account, or the free
    database expiring.

A backup is one CSV per table plus a manifest, so it can be read by anything —
Excel, a new database, or a script — without needing this code to still exist.
"""
import csv
import io
import json
import os
import re
import zipfile
from datetime import timedelta

import config
import schema
from engine import parse_ts, ts, utcnow

KEEP_NIGHTLY = 14
NIGHTLY_DIR = os.environ.get('BACKUP_FOLDER') or os.path.join(
    os.path.dirname(os.environ.get('UPLOAD_FOLDER', '') or '.'), 'backups')

# Tables holding personal details. Named in the manifest so whoever opens the
# zip knows what they're carrying.
SENSITIVE = ('users', 'jobs', 'quotes', 'messages', 'prospects', 'api_tokens', 'tokens')


def table_names():
    """Every table the schema creates, in the order it creates them."""
    return re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', schema.SCHEMA)


def dump(db, fileobj):
    """Write a zip of the whole database to `fileobj`. Returns the manifest."""
    at = utcnow()
    manifest = {'brand': config.BRAND, 'taken_at': ts(at), 'tables': {},
                'holds_personal_details': list(SENSITIVE),
                'note': 'One CSV per table. Password hashes and tokens are included — treat this like a password file.'}
    with zipfile.ZipFile(fileobj, 'w', zipfile.ZIP_DEFLATED) as z:
        for table in table_names():
            try:
                rows = db.execute(f'SELECT * FROM {table}').fetchall()   # table names come from our own schema
            except Exception as e:                                        # a table an older database doesn't have yet
                manifest['tables'][table] = f'skipped: {e}'
                continue
            buf = io.StringIO()
            writer = csv.writer(buf)
            if rows:
                keys = list(rows[0].keys())
                writer.writerow(keys)
                for r in rows:
                    writer.writerow(['' if r[k] is None else r[k] for k in keys])
            z.writestr(f'{table}.csv', buf.getvalue())
            manifest['tables'][table] = len(rows)
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
    return manifest


def filename(at=None):
    return f'{schema.BRAND_SLUG}-backup-{ts(at or utcnow()).replace(" ", "-").replace(":", "")}.zip'


def write_nightly(db, folder=None, at=None):
    """Write today's backup to disk and tidy up old ones. Returns the path, or None."""
    at = at or utcnow()
    folder = folder or NIGHTLY_DIR
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename(at))
    with open(path, 'wb') as fh:
        dump(db, fh)
    old = sorted(f for f in os.listdir(folder) if f.endswith('.zip'))
    for name in old[:-KEEP_NIGHTLY]:
        try:
            os.unlink(os.path.join(folder, name))
        except OSError:
            pass
    return path


def maybe_nightly(db, at=None):
    """Called from the sweep. Writes one backup a day, quietly."""
    at = at or utcnow()
    last = db.execute("SELECT value FROM settings WHERE key = 'last_backup_at'").fetchone()
    if last and last['value'] and parse_ts(last['value']) > at - timedelta(hours=20):
        return None
    try:
        path = write_nightly(db, at=at)
    except Exception as e:                       # a backup must never take the site down
        print(f'[backup] could not write: {e}', flush=True)
        return None
    db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_backup_at', '')")
    db.execute("UPDATE settings SET value = ? WHERE key = 'last_backup_at'", (ts(at),))
    db.commit()
    return path


def status(db):
    """What Admin → Setup shows."""
    row = db.execute("SELECT value FROM settings WHERE key = 'last_backup_at'").fetchone()
    files = []
    if os.path.isdir(NIGHTLY_DIR):
        files = sorted(f for f in os.listdir(NIGHTLY_DIR) if f.endswith('.zip'))
    return {'last_at': row['value'] if row else None, 'kept': len(files),
            'newest': files[-1] if files else None, 'folder': NIGHTLY_DIR, 'keep': KEEP_NIGHTLY}
