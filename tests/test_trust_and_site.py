"""
Tests for the five things added on 25 September 2026: the trust score, the
profile photo, automatic slot top-up, the routing check, and the site section.

The one that matters most is `test_trust_never_changes_who_gets_offered_a_job`.
Everything else here is a feature; that one is the promise.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

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
import matching  # noqa: E402
import outreach  # noqa: E402
import trust  # noqa: E402
import worksite  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 25, 9, 0, 0)


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
        self.plumb = self.db.execute("SELECT id FROM categories WHERE slug = 'plumber'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.n = 0

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer', cat=None):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'), f'Person {self.n}',
                               ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, cat or self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, f'u{self.n}@test.nz', 'large', '', '', at=T0)
        self.db.commit()
        return uid

    def job(self, customer, cat=None, at=T0):
        return engine.post_job(self.db, customer, dict(
            category_id=cat or self.cat, area_id=self.area, suburb='Karori', address='12 Rata St, Karori',
            title='Replace rotten deck boards', description='Twelve square metres, boards are soft.',
            value_band='medium', timing='weeks', property_type='house'), at=at)[0]

    def trade(self, tid):
        return self.db.execute('SELECT * FROM trades WHERE user_id = ?', (tid,)).fetchone()

    def hire(self, customer, trade_id, at=T0):
        """A job all the way through to hired, which is what the site section needs."""
        job_id = self.job(customer, at=at)
        engine.submit_quote(self.db, job_id, trade_id, dict(
            price_type='fixed', amount_low=6400, gst_included=1,
            message='Two days on site, starting with the worst boards. We take the old timber away.'),
            at=at + timedelta(minutes=5))
        qid = self.db.execute('SELECT id FROM quotes WHERE job_id = ?', (job_id,)).fetchone()['id']
        engine.accept_quote(self.db, engine.get_job(self.db, job_id), qid, act_ack=True,
                            at=at + timedelta(minutes=10))
        return engine.get_job(self.db, job_id)

    def client(self, uid):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = uid
            s['_csrf'] = 't'
        return c


class TrustScoreTest(Base):

    def test_a_brand_new_trade_reads_as_unproven_not_bad(self):
        t = self.trade(self.user('trade'))
        e = trust.explain(self.db, t)
        self.assertEqual(e['score'], 0)
        self.assertFalse(e['proven'])
        conduct = [s for s in e['sections'] if s['key'] == 'conduct'][0]
        self.assertTrue(all(r['note'] == 'not enough jobs yet' for r in conduct['rows']))
        self.assertIn('not enough finished jobs', ' '.join(trust.summary(self.db, t)['lines']))

    def test_checked_facts_are_worth_points_and_say_when(self):
        tid = self.user('trade')
        self.db.execute('UPDATE trades SET licence_checked_at = ?, insurance_checked_at = ? WHERE user_id = ?',
                        (ts(T0), ts(T0), tid))
        self.db.commit()
        e = trust.explain(self.db, self.trade(tid))
        rows = {r['key']: r for r in e['sections'][0]['rows']}
        worth = dict((k, w) for k, _label, w in trust.CHECKED)
        self.assertEqual(rows['licence']['got'], worth['licence'])
        self.assertEqual(rows['insurance']['got'], worth['insurance'])
        self.assertEqual(rows['nzbn']['got'], 0)
        self.assertGreater(worth['licence'], worth['nzbn'],
                           'a licence is the thing that matters most; keep it weighted that way')
        self.assertEqual(rows['licence']['when'], ts(T0))
        self.assertGreater(e['score'], 0)

    def test_volunteered_things_only_count_once_they_are_real(self):
        tid = self.user('trade')
        # A photo the tradie uploaded but we haven't looked at still counts —
        # it is a face on the profile either way. ID and vetting need us.
        self.db.execute("UPDATE trades SET photo = 'x.jpg', vetting_status = 'offered', vetting_at = ? "
                        'WHERE user_id = ?', (ts(T0), tid))
        self.db.commit()
        worth = dict((k, w) for k, _label, w in trust.VOLUNTEERED)
        rows = {r['key']: r for r in trust.volunteered(self.trade(tid))}
        self.assertEqual(rows['photo']['got'], worth['photo'])
        self.assertEqual(rows['id']['got'], 0)
        self.assertEqual(rows['vetting']['got'], 0, 'merely saying you have one must not score')

        self.db.execute("UPDATE trades SET vetting_status = 'seen' WHERE user_id = ?", (tid,))
        self.db.commit()
        self.assertEqual({r['key']: r['got'] for r in trust.volunteered(self.trade(tid))}['vetting'],
                         dict((k, w) for k, _l, w in trust.VOLUNTEERED)['vetting'])

    def test_referees_count_only_after_we_have_rung_them(self):
        tid = self.user('trade')
        for i in range(2):
            self.db.execute('INSERT INTO trade_referees (trade_id, name, created_at) VALUES (?,?,?)',
                            (tid, f'Ref {i}', ts(T0)))
        self.db.commit()
        self.assertEqual(trust.referee_count(self.db, tid), 0)
        self.db.execute('UPDATE trade_referees SET checked_at = ?', (ts(T0),))
        self.db.commit()
        self.assertEqual(trust.referee_count(self.db, tid), 2)
        self.assertEqual({r['key']: r['got'] for r in trust.volunteered(self.trade(tid), 2)}['referees'],
                         dict((k, w) for k, _l, w in trust.VOLUNTEERED)['referees'])

    def test_everything_checked_scores_full_marks(self):
        tid = self.user('trade')
        self.db.execute('UPDATE trades SET licence_checked_at = ?, insurance_checked_at = ?, nzbn_checked_at = ?, '
                        "business_checked_at = ?, photo = 'x.jpg', photo_checked_at = ?, id_checked_at = ?, "
                        "vetting_status = 'seen', vetting_at = ? WHERE user_id = ?",
                        (ts(T0),) * 7 + (tid,))
        for i in range(2):
            self.db.execute('INSERT INTO trade_referees (trade_id, name, checked_at, created_at) VALUES (?,?,?,?)',
                            (tid, f'Ref {i}', ts(T0), ts(T0)))
        for i in range(trust.TICKET_CAP):
            self.db.execute('INSERT INTO trade_documents (trade_id, kind, name, checked_at, created_at) '
                            'VALUES (?,?,?,?,?)', (tid, 'site_safe', f'Ticket {i}', ts(T0), ts(T0)))
        self.db.commit()
        e = trust.explain(self.db, self.trade(tid))
        # Conduct is excluded while unproven, so a fully checked newcomer reads 100.
        self.assertEqual(e['score'], 100)
        self.assertEqual(e['band'], 'Well checked')
        self.assertIsNone(e['next'])

    def test_the_four_sections_add_up_to_a_hundred(self):
        total = (sum(w for _k, _l, w in trust.CHECKED) + sum(w for _k, _l, w in trust.CONDUCT)
                 + trust.TICKET_EACH * trust.TICKET_CAP + sum(w for _k, _l, w in trust.VOLUNTEERED))
        self.assertEqual(total, 100, 'a score out of 100 has to be out of 100')

    def test_the_next_step_is_the_most_valuable_missing_thing(self):
        tid = self.user('trade')
        self.assertEqual(trust.explain(self.db, self.trade(tid))['next']['key'], 'licence')
        self.db.execute('UPDATE trades SET licence_checked_at = ? WHERE user_id = ?', (ts(T0), tid))
        self.db.commit()
        self.assertEqual(trust.explain(self.db, self.trade(tid))['next']['key'], 'insurance')

    def test_refresh_caches_the_score_on_the_row(self):
        tid = self.user('trade')
        self.db.execute('UPDATE trades SET licence_checked_at = ? WHERE user_id = ?', (ts(T0), tid))
        self.db.commit()
        trust.refresh(self.db, T0)
        row = self.trade(tid)
        self.assertEqual(row['trust_score'], trust.score_for(self.db, row))
        self.assertIsNotNone(row['trust_at'])

    # ── the promise ──
    def test_trust_never_changes_who_gets_offered_a_job(self):
        """A high score must not buy a place in the queue. This is the whole point."""
        low, high = self.user('trade'), self.user('trade')
        self.db.execute('UPDATE trades SET licence_checked_at = ?, insurance_checked_at = ?, nzbn_checked_at = ?, '
                        'business_checked_at = ?, trust_score = 100 WHERE user_id = ?',
                        (ts(T0),) * 4 + (high,))
        self.db.execute('UPDATE trades SET trust_score = 0 WHERE user_id = ?', (low,))
        self.db.commit()

        # Over a run of jobs, both are offered the same number — rotation is blind.
        for i in range(6):
            self.job(self.user(), at=T0 + timedelta(minutes=i))
        counts = {r['trade_id']: r['n'] for r in self.db.execute(
            'SELECT trade_id, COUNT(*) AS n FROM offers GROUP BY trade_id')}
        self.assertEqual(counts.get(low), counts.get(high))
        self.assertEqual(counts.get(low), 6)


class ClosingAnAccountTest(Base):
    """The privacy page promises these go. It has to be true."""

    def test_closing_a_trade_account_removes_the_optional_personal_things(self):
        import accounts
        tid = self.user('trade')
        self.db.execute("UPDATE trades SET photo = 'me.jpg', photo_at = ?, photo_checked_at = ?, "
                        "id_checked_at = ?, vetting_status = 'seen', vetting_at = ?, "
                        "vetting_note = 'seen in person' WHERE user_id = ?", (ts(T0),) * 4 + (tid,))
        self.db.execute('INSERT INTO trade_referees (trade_id, name, phone, created_at) VALUES (?,?,?,?)',
                        (tid, 'Aroha Ngata', '021 555 0100', ts(T0)))
        self.db.commit()

        user = self.db.execute('SELECT * FROM users WHERE id = ?', (tid,)).fetchone()
        accounts.close(self.db, user, at=T0)

        row = self.trade(tid)
        self.assertIsNone(row['photo'])
        self.assertIsNone(row['photo_checked_at'])
        self.assertIsNone(row['id_checked_at'])
        self.assertIsNone(row['vetting_status'])
        self.assertIsNone(row['vetting_note'])
        # The referee never had an account here; their details are not this
        # person's to leave behind.
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM trade_referees WHERE trade_id = ?',
                                         (tid,)).fetchone()['n'], 0)


class PhotoTest(Base):

    def png(self):
        return (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 40), 'me.png')

    def setup_form(self, **over):
        f = {'business_name': 'Harbourline', 'categories': str(self.cat), 'areas': str(self.area),
             'licence_type': 'none', '_csrf': 't'}
        f.update(over)
        return f

    def test_a_trade_can_add_a_photo_and_it_shows_on_their_quote(self):
        tid = self.user('trade')
        c = self.client(tid)
        r = c.post('/trade/setup', data=dict(self.setup_form(), photo=self.png()),
                   content_type='multipart/form-data')
        self.assertIn(r.status_code, (200, 302))
        row = self.trade(tid)
        self.assertTrue(row['photo'])
        self.assertIsNone(row['photo_checked_at'], 'a new photo starts unconfirmed')
        os.remove(os.path.join(A.UPLOAD_DIR, row['photo']))

    def test_changing_the_photo_clears_the_id_match(self):
        tid = self.user('trade')
        self.db.execute("UPDATE trades SET photo = 'old.png', photo_checked_at = ?, id_checked_at = ? "
                        'WHERE user_id = ?', (ts(T0), ts(T0), tid))
        self.db.commit()
        c = self.client(tid)
        c.post('/trade/setup', data=dict(self.setup_form(), photo=self.png()), content_type='multipart/form-data')
        row = self.trade(tid)
        self.assertIsNone(row['photo_checked_at'])
        self.assertIsNone(row['id_checked_at'], 'a new face means the old ID match no longer applies')
        os.remove(os.path.join(A.UPLOAD_DIR, row['photo']))


class TopUpTest(Base):

    def prospect(self, email='sparky@example.co.nz', cat=None):
        pid = self.db.execute(
            'INSERT INTO prospects (business_name, email, category_id, token, status, leads_sent, '
            'do_not_contact, created_at) VALUES (?,?,?,?,?,0,0,?)',
            ('Sparky Ltd', email, cat or self.cat, f'tok{self.n}{email}', 'new', ts(T0))).lastrowid
        self.db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (pid, self.area))
        self.db.commit()
        return pid

    def test_a_job_with_nobody_to_send_it_to_emails_the_businesses_who_arent_here(self):
        for i in range(4):
            self.prospect(f'p{i}@example.co.nz')
        job_id = self.job(self.user())                       # no trades on Level at all
        job = engine.get_job(self.db, job_id)
        self.assertIsNotNone(job['topup_at'], 'the shortfall should have been noticed')
        queued = self.db.execute('SELECT COUNT(*) AS n FROM prospect_sends WHERE job_id = ?',
                                 (job_id,)).fetchone()['n']
        self.assertEqual(queued, 4)

    def test_it_does_not_go_again_straight_away(self):
        self.prospect()
        job_id = self.job(self.user())
        before = self.db.execute('SELECT COUNT(*) AS n FROM prospect_sends').fetchone()['n']
        engine.sweep(self.db, at=T0 + timedelta(minutes=5))
        engine.sweep(self.db, at=T0 + timedelta(minutes=10))
        after = self.db.execute('SELECT COUNT(*) AS n FROM prospect_sends').fetchone()['n']
        self.assertEqual(before, after, 'give the first email a chance to work before going again')
        self.assertIsNotNone(job_id)

    def test_it_goes_again_later_and_reaches_further_out(self):
        near = self.prospect('near@example.co.nz')
        # Same region, different area: only reachable once we widen.
        far_area = self.db.execute(
            'SELECT id FROM areas WHERE region = (SELECT region FROM areas WHERE id = ?) AND id <> ?',
            (self.area, self.area)).fetchone()
        far = self.prospect('far@example.co.nz')
        self.db.execute('UPDATE prospect_areas SET area_id = ? WHERE prospect_id = ?', (far_area['id'], far))
        self.db.commit()

        job_id = self.job(self.user())
        first = [r['prospect_id'] for r in self.db.execute('SELECT prospect_id FROM prospect_sends')]
        self.assertEqual(first, [near], 'the nearest people get asked first')

        engine.sweep(self.db, at=T0 + timedelta(hours=outreach.TOPUP_RETRY_HOURS, minutes=1))
        now = {r['prospect_id'] for r in self.db.execute('SELECT prospect_id FROM prospect_sends')}
        self.assertEqual(now, {near, far}, 'a later round reaches across the region')
        self.assertEqual(engine.get_job(self.db, job_id)['topup_rounds'], 2)

    def test_it_stops_after_the_last_round(self):
        for i in range(30):
            self.prospect(f'p{i}@example.co.nz')
        self.job(self.user())
        at = T0
        for _ in range(6):
            at += timedelta(hours=outreach.TOPUP_RETRY_HOURS + 1)
            engine.sweep(self.db, at=at)
        rounds = self.db.execute('SELECT topup_rounds FROM jobs').fetchone()['topup_rounds']
        self.assertEqual(rounds, outreach.TOPUP_ROUNDS, 'a job nobody answers must not be nagged about forever')

    def test_a_business_that_joins_gets_the_job_that_brought_them_in(self):
        """The whole point of the email. Waiting for the next sweep spends the clock."""
        pid = self.prospect()
        job_id = self.job(self.user())
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM prospect_sends WHERE job_id = ?',
                                         (job_id,)).fetchone()['n'], 1)

        # They sign up and pick a plan — which is the moment they can take work.
        trade = self.user('trade')
        self.db.execute('UPDATE prospects SET user_id = ?, status = ? WHERE id = ?', (trade, 'signed_up', pid))
        self.db.commit()
        offered = [r['trade_id'] for r in self.db.execute(
            "SELECT trade_id FROM offers WHERE job_id = ? AND status = 'active'", (job_id,))]
        self.assertIn(trade, offered, 'they should already hold the job, not wait for a sweep')

    def test_opt_outs_are_never_topped_up_to(self):
        pid = self.prospect()
        self.db.execute('UPDATE prospects SET do_not_contact = 1 WHERE id = ?', (pid,))
        self.db.commit()
        job_id = self.job(self.user())
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM prospect_sends WHERE job_id = ?',
                                         (job_id,)).fetchone()['n'], 0)

    def test_a_full_job_doesnt_trigger_it(self):
        self.prospect()
        for _ in range(engine.config.TRADES_PER_JOB):
            self.user('trade')
        job_id = self.job(self.user())
        self.assertIsNone(engine.get_job(self.db, job_id)['topup_at'])


class QuietJobTest(Base):
    """A job nobody quotes on. The customer should hear about it from us, not
    work it out from fourteen days of silence."""

    def notes(self, uid):
        return [r['body'] for r in self.db.execute(
            'SELECT body FROM notifications WHERE user_id = ? ORDER BY id', (uid,))]

    def test_nothing_is_said_in_the_first_day(self):
        customer = self.user()
        self.user('trade')
        self.job(customer)
        engine.sweep(self.db, at=T0 + timedelta(hours=config.QUIET_AFTER_HOURS - 1))
        self.assertFalse([n for n in self.notes(customer) if 'No quotes on' in n])

    def test_a_thin_trade_is_explained_with_the_real_number(self):
        customer = self.user()
        for i in range(4):
            pid = self.db.execute(
                'INSERT INTO prospects (business_name, email, category_id, token, status, leads_sent, '
                "do_not_contact, created_at) VALUES (?,?,?,?,'new',0,0,?)",
                ('Sparky', f'p{i}@x.co.nz', self.cat, f'tk{i}', ts(T0))).lastrowid
            self.db.execute('INSERT INTO prospect_areas (prospect_id, area_id) VALUES (?,?)', (pid, self.area))
        self.db.commit()
        self.job(customer)                                   # no trades at all → top-up fires
        engine.sweep(self.db, at=T0 + timedelta(hours=config.QUIET_AFTER_HOURS + 1))
        said = [n for n in self.notes(customer) if 'No quotes on' in n]
        self.assertEqual(len(said), 1)
        self.assertIn('we’ve emailed 4 more local businesses', said[0])

    def test_it_is_said_once_not_every_sweep(self):
        customer = self.user()
        self.job(customer)
        for h in range(config.QUIET_AFTER_HOURS + 1, config.QUIET_AFTER_HOURS + 40, 8):
            engine.sweep(self.db, at=T0 + timedelta(hours=h))
        self.assertEqual(len([n for n in self.notes(customer) if 'No quotes on' in n]), 1)

    def test_a_full_board_that_simply_hasnt_quoted_yet_is_left_alone(self):
        """Fifteen trades holding it is the system working, not a problem."""
        customer = self.user()
        for _ in range(config.TRADES_PER_JOB):
            self.user('trade')
        self.job(customer)
        # Still inside the offer window, so the board is full.
        engine.sweep(self.db, at=T0 + timedelta(hours=1))
        self.db.execute('UPDATE jobs SET created_at = ?',
                        (ts(T0 - timedelta(hours=config.QUIET_AFTER_HOURS + 2)),))
        self.db.commit()
        engine.sweep(self.db, at=T0 + timedelta(hours=1))
        self.assertFalse([n for n in self.notes(customer) if 'No quotes on' in n])

    def test_a_job_with_a_quote_is_never_told_it_is_quiet(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.job(customer)
        engine.submit_quote(self.db, job_id, trade, dict(
            price_type='fixed', amount_low=2000, gst_included=1,
            message='Happy to take this on, two days on site.'), at=T0 + timedelta(minutes=10))
        engine.sweep(self.db, at=T0 + timedelta(hours=config.QUIET_AFTER_HOURS + 1))
        self.assertFalse([n for n in self.notes(customer) if 'No quotes on' in n])


class ExpiryTest(Base):
    """A job that ran the full fourteen days without a quote."""

    def closing_note(self, uid):
        """The one about the job closing. Not simply the last — the "who did you
        hire?" follow-up lands in the same sweep, right after it."""
        for r in self.db.execute('SELECT body FROM notifications WHERE user_id = ? ORDER BY id DESC', (uid,)):
            if 'closed after' in r['body'] or 'has closed' in r['body']:
                return r['body']
        return ''

    def test_it_says_why_rather_than_shrugging(self):
        customer = self.user()
        job_id = self.job(customer)
        self.db.execute('UPDATE jobs SET topup_sent = 7 WHERE id = ?', (job_id,))
        self.db.commit()
        engine.sweep(self.db, at=T0 + timedelta(days=config.JOB_OPEN_DAYS + 1))
        note = self.closing_note(customer)
        self.assertIn('without a quote', note)
        self.assertIn('asked 7 local businesses', note)
        self.assertIn('photo', note)

    def test_a_job_that_got_quotes_keeps_the_plain_message(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.job(customer)
        engine.submit_quote(self.db, job_id, trade, dict(
            price_type='fixed', amount_low=2000, gst_included=1,
            message='Happy to take this on, two days on site.'), at=T0 + timedelta(minutes=10))
        engine.sweep(self.db, at=T0 + timedelta(days=config.JOB_OPEN_DAYS + 1))
        self.assertIn('Post it again', self.closing_note(customer))


class RoutingTest(Base):

    def test_the_check_is_silent_with_no_api_key(self):
        self.assertFalse(matching.enabled())
        job = engine.get_job(self.db, self.job(self.user()))
        result = matching.review(self.db, job, self.db.execute('SELECT id, slug, name FROM categories').fetchall())
        self.assertIsNone(result['category'])
        self.assertIn('not checked', result['note'].lower())

    def test_the_check_runs_before_the_job_is_offered_to_anybody(self):
        """A wrong trade caught after distribution has already burned fifteen slots."""
        import matching
        seen = {}
        real_enabled, real_review = matching.enabled, matching.review

        def fake_review(db, job, cats):
            # How many people already hold this job at the moment we're asked?
            seen['offers'] = db.execute('SELECT COUNT(*) AS n FROM offers WHERE job_id = ?',
                                        (job['id'],)).fetchone()['n']
            return {'category': None, 'confidence': 'sure', 'reason': '', 'licence': False,
                    'also': [], 'band': None, 'note': 'Looks right.', 'category_id': None}

        matching.enabled, matching.review = (lambda: True), fake_review
        try:
            for _ in range(3):
                self.user('trade')
            self.job(self.user())
        finally:
            matching.enabled, matching.review = real_enabled, real_review
        self.assertEqual(seen.get('offers'), 0, 'the check must run before anyone is offered the job')

    def test_the_other_trades_a_job_needs_are_kept_and_named(self):
        import matching
        job_id = self.job(self.user())
        job = engine.get_job(self.db, job_id)
        matching.apply_to(self.db, job, {
            'category': None, 'confidence': 'likely', 'reason': 'It is a bathroom.',
            'licence': True, 'also': ['plumber', 'tiler'], 'band': None, 'note': 'Looks right.'}, T0)
        extra = matching.extra_for(engine.get_job(self.db, job_id))
        self.assertTrue(extra['licence'])
        self.assertEqual([a['name'] for a in extra['also']], ['Plumber', 'Tiler'])

        page = self.client(job['customer_id']).get(f'/me/jobs/{job_id}').data.decode()
        self.assertIn('needs a registered or licensed person', page)
        self.assertIn('plumber', page.lower())

    def test_extra_survives_a_row_that_predates_the_column(self):
        import matching
        job = engine.get_job(self.db, self.job(self.user()))
        self.assertIsNone(matching.extra_for(job))

    def test_a_job_is_never_rerouted_without_the_customer(self):
        customer = self.user()
        job_id = self.job(customer)
        job = engine.get_job(self.db, job_id)
        matching.apply_to(self.db, job, {'category': 'plumber', 'confidence': 'sure', 'reason': 'It is a leak.',
                                         'licence': True, 'also': [], 'band': None,
                                         'note': 'Should be a Plumber.'}, T0)
        self.assertEqual(engine.get_job(self.db, job_id)['category_id'], self.cat,
                         'the machine must not move somebody’s job')

    def test_the_customer_can_accept_the_suggestion(self):
        customer = self.user()
        self.user('trade')                                    # a builder, who will lose the job
        plumber = self.user('trade', cat=self.plumb)
        job_id = self.job(customer)
        self.db.execute('UPDATE jobs SET routed_category_id = ? WHERE id = ?', (self.plumb, job_id))
        self.db.commit()

        c = self.client(customer)
        c.post(f'/me/jobs/{job_id}/reroute', data={'category_id': str(self.plumb), '_csrf': 't'})
        self.assertEqual(engine.get_job(self.db, job_id)['category_id'], self.plumb)
        live = [r['trade_id'] for r in self.db.execute(
            "SELECT trade_id FROM offers WHERE job_id = ? AND status = 'active'", (job_id,))]
        self.assertEqual(live, [plumber], 'the builders’ offers should be closed and plumbers offered it')

    def test_saying_no_stops_the_question_coming_back(self):
        customer = self.user()
        job_id = self.job(customer)
        self.db.execute('UPDATE jobs SET routed_category_id = ? WHERE id = ?', (self.plumb, job_id))
        self.db.commit()
        self.client(customer).post(f'/me/jobs/{job_id}/reroute', data={'category_id': 'keep', '_csrf': 't'})
        self.assertIsNone(engine.get_job(self.db, job_id)['routed_category_id'])


class WorksiteTest(Base):

    def test_the_site_section_only_exists_once_someone_is_hired(self):
        customer = self.user()
        open_job = self.job(customer)
        c = self.client(customer)
        self.assertNotIn(b'The site', c.get(f'/me/jobs/{open_job}').data)

        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        page = self.client(job['customer_id']).get(f'/me/jobs/{job["id"]}').data
        self.assertIn(b'The site', page)
        self.assertIn(b'Getting in', page)

    def test_both_sides_can_write_site_details_but_nobody_else(self):
        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        self.client(trade).post(f'/jobs/{job["id"]}/site',
                                data={'access': 'Side gate, code 1234', 'pets': 'Dog', '_csrf': 't'})
        site = worksite.get_site(self.db, job['id'])
        self.assertEqual(site['access'], 'Side gate, code 1234')
        # The address stays the job's own, whatever anyone types.
        self.assertEqual(site['address'], '12 Rata St, Karori')

        stranger = self.user()
        self.assertEqual(self.client(stranger).post(f'/jobs/{job["id"]}/site',
                                                    data={'pets': 'None', '_csrf': 't'}).status_code, 404)

    def test_a_private_note_stays_private(self):
        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        self.client(trade).post(f'/jobs/{job["id"]}/notes',
                                data={'body': 'Customer is never home before six.', 'shared': 'private',
                                      '_csrf': 't'}, content_type='multipart/form-data')
        mine = worksite.notes_for(self.db, job['id'], trade, is_customer=False)
        theirs = worksite.notes_for(self.db, job['id'], job['customer_id'], is_customer=True)
        self.assertEqual(len(mine), 1)
        self.assertEqual(theirs, [], 'the customer must not see a note kept private')

    def test_the_pre_start_check_records_what_was_found(self):
        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        answers = {k: 'yes' for k in worksite.CHECK_KEYS}
        answers['hazards'] = 'no'
        data = dict(answers, hazards_text='', notes='', _csrf='t')
        data['hazards'] = 'no'
        self.client(trade).post(f'/trade/jobs/{job["id"]}/site-check', data=data)
        checks = worksite.checks_for(self.db, job['id'])
        self.assertEqual(len(checks), 1)
        self.assertEqual(len(checks[0]['flags']), 1)
        self.assertIn('asbestos', checks[0]['flags'][0])

    def test_every_question_is_phrased_so_that_no_is_the_concern(self):
        """A mixed polarity flags a tradie for a good answer.

        The consent item used to ask "does this need a consent?", where "no" is
        the reassuring answer — and it was flagged alongside "no, I haven't
        found the mains switch". Found by filling the form in on a phone.
        """
        for key, question, _why in worksite.CHECK_ITEMS:
            flags = worksite.flags({k: ('no' if k == key else 'yes') for k in worksite.CHECK_KEYS})
            self.assertEqual(len(flags), 1, f'{key}: answering no should raise exactly one thing')
            yes_only = worksite.flags({k: 'yes' for k in worksite.CHECK_KEYS})
            self.assertEqual(yes_only, [], f'{key}: all yes must raise nothing')
            self.assertTrue(question.endswith('?'))

    def test_an_unanswered_check_is_refused(self):
        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        with self.assertRaises(worksite.SiteError):
            worksite.save_check(self.db, job, trade, {'access': 'yes'}, None, None, at=T0)

    def test_a_customer_cannot_sign_off_their_own_site_check(self):
        trade = self.user('trade')
        job = self.hire(self.user(), trade)
        answers = {k: 'yes' for k in worksite.CHECK_KEYS}
        with self.assertRaises(worksite.SiteError):
            worksite.save_check(self.db, job, job['customer_id'], answers, None, None, at=T0)


if __name__ == '__main__':
    unittest.main()
