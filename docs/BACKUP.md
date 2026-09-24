# Backups — what exists and how to use it

## What happens on its own

Every night the sweep writes a zip of the whole database to the same disk that holds the
job photos (`/data/backups` on Render), and keeps the last 14. Nothing to set up.

That protects you from a bad deploy or a bad migration. It does **not** protect you from
losing the Render account, or the free database expiring — the backup is on the same
machine. For that, download a copy now and then.

## Download a copy

Admin → Setup → **Download a backup**. You get a zip of spreadsheets, one per table,
plus `manifest.json` saying when it was taken and how many rows are in each table.

Do this **at least monthly**, and before anything risky. Keep it somewhere that isn't
Render — Google Drive, Dropbox or an external drive is fine.

**It holds personal details.** Customer and tradie names, emails, phone numbers and
password hashes. Treat the file like a password file: don't email it, don't put it in a
shared folder other people can read.

## From the command line

```bash
flask --app app backup          # writes one to the backups folder and prints the path
```

Useful if you ever want a scheduled job outside the app.

## Restoring

The zip is plain CSV, so there's no magic to it.

1. Make a new, empty database and let the app create the tables (start it once).
2. Load each CSV into the matching table, in the order they appear in `manifest.json` —
   that's the order the tables are created, so parents come before children.
   In `psql`: `\copy users FROM 'users.csv' WITH (FORMAT csv, HEADER true, NULL '')`
3. Reset the ID sequences afterwards, or new rows will collide:
   `SELECT setval(pg_get_serial_sequence('users','id'), (SELECT MAX(id) FROM users));`
   Repeat for each table with an `id` column.
4. Job photos are separate — they live on the Render disk, not in the backup.

If you're restoring in anger, copy the zip somewhere safe first and work on a copy.

## The 90-day thing

The free Postgres on Render expires 90 days after it's created. Before that date you need
to either move to a paid database or export and import into a new one. Put a reminder in
your calendar now — when it expires, the data goes with it, and the nightly backups on the
server disk are the only copy unless you've downloaded one.
