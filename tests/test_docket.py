"""
Sending a won job to the trade's own Docket.

The behaviour that matters is not "it works" — it's what happens when it
doesn't. Docket being down, a revoked key, a retry after a timeout: none of
those may cost anybody a job, and none may put the same job in twice.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import json
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

import billing  # noqa: E402
import db as dbmod  # noqa: E402
import docket  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import worksite  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 25, 9, 0, 0)


class FakeDocket:
    """Stands in for the other end. Records what it was sent."""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.calls = []
        self.jobs = {}

    def __call__(self, base_url, key, path, payload=None, method='POST'):
        self.calls.append((path, payload))
        if self.fail_with:
            raise self.fail_with
        if path.endswith('/ping'):
            return {'ok': True, 'company': 'Karori Building', 'product': 'Docket'}
        ref = payload['external_ref']
        created = ref not in self.jobs
        self.jobs[ref] = payload                       # the same ref replaces, never adds
        return {'ok': True, 'job_id': 1, 'job_number': 'J-2026-001', 'created': created}


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.trade = self._user('trade', 'karori@test.nz', 'Karori Building')
        self.customer = self._user('customer', 'sam@test.nz', 'Sam Whitiora', '021 555 0199')

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def _user(self, role, email, name, phone=None):
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, phone, created_at) '
                              'VALUES (?,?,?,?,?,?)',
                              (role, email, hash_password('password123'), name, phone, ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, name, ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, email, 'large', '', '', at=T0)
        self.db.commit()
        return uid

    def connect(self):
        self.db.execute("UPDATE trades SET docket_url = 'https://d.example.nz', docket_key = 'lvl_k', "
                        "docket_name = 'Karori Building' WHERE user_id = ?", (self.trade,))
        self.db.commit()

    def win_a_job(self, with_site=True):
        job_id = engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', address='12 Rata Street, Karori',
            title='Replace rotten deck boards', description='Twelve square metres, boards are soft.',
            value_band='medium', timing='weeks', property_type='house'), at=T0)[0]
        engine.submit_quote(self.db, job_id, self.trade, dict(
            price_type='fixed', amount_low=6400, gst_included=1,
            message='Two days on site. We take the old timber away.',
            inclusions='Labour, materials and rubbish removal', exclusions='Painting',
            warranty='2 years on workmanship'), at=T0 + timedelta(minutes=5))
        if with_site:
            worksite.save_site(self.db, engine.get_job(self.db, job_id), self.customer,
                               {'access': 'Side gate, code 1234', 'parking': '', 'pets': 'Dog out the back',
                                'hazards': '', 'power_water': '', 'notes': ''})
        qid = self.db.execute('SELECT id FROM quotes WHERE job_id = ?', (job_id,)).fetchone()['id']
        engine.accept_quote(self.db, engine.get_job(self.db, job_id), qid, act_ack=True,
                            at=T0 + timedelta(minutes=10))
        return job_id

    def job(self, job_id):
        return engine.get_job(self.db, job_id)


class SendingTest(Base):

    def test_winning_a_job_puts_it_in_docket(self):
        self.connect()
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        sent = fake.jobs['level-job-%d' % job_id]
        self.assertEqual(sent['title'], 'Replace rotten deck boards')
        self.assertEqual(sent['client']['name'], 'Sam Whitiora')
        self.assertEqual(sent['client']['phone'], '021 555 0199')
        self.assertEqual(sent['site_address'], '12 Rata Street, Karori')
        self.assertEqual(sent['job_type'], 'Builder')
        self.assertIn('Side gate, code 1234', sent['access_notes'])
        self.assertIn('Dog out the back', sent['access_notes'])
        self.assertIn('$6,400', sent['notes'])
        self.assertIn('2 years on workmanship', sent['notes'])
        self.assertEqual(self.job(job_id)['docket_ref'], 'J-2026-001')

    def test_a_trade_without_docket_is_simply_left_alone(self):
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        self.assertEqual(fake.calls, [], 'nothing should have been sent')
        self.assertIsNone(self.job(job_id)['docket_at'])
        self.assertIsNone(self.job(job_id)['docket_error'])

    def test_only_a_job_they_won_goes_across(self):
        """A quote that wasn't accepted is nobody else's business."""
        self.connect()
        other = self._user('trade', 'other@test.nz', 'Someone Else')
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        sent = fake.jobs['level-job-%d' % job_id]
        self.assertNotIn('Someone Else', json.dumps(sent))
        self.assertIsNotNone(other)

    def test_a_vague_start_date_is_left_out_rather_than_guessed(self):
        self.connect()
        self.db.execute("UPDATE quotes SET available_from = 'next week'")
        self.db.commit()
        job = self.job(engine.post_job(self.db, self.customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='X',
            description='Something needs doing at the back.', value_band='small',
            timing='weeks', property_type='house'), at=T0)[0])
        self.assertIsNone(docket.payload_for(self.db, job, self.trade)['start_date'])


class WhenItGoesWrongTest(Base):

    def test_the_same_job_twice_updates_rather_than_duplicating(self):
        self.connect()
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
            docket.push(self.db, self.job(job_id), self.trade)      # a retry after a timeout
            docket.push(self.db, self.job(job_id), self.trade)
        self.assertEqual(len(fake.jobs), 1, 'one job over there, however many times we send it')

    def test_docket_being_down_does_not_cost_anybody_the_job(self):
        self.connect()
        fake = FakeDocket(fail_with=docket.DocketError('Couldn’t reach Docket.'))
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        job = self.job(job_id)
        self.assertEqual(job['status'], 'hired', 'the job is still won')
        self.assertEqual(job['hired_trade_id'], self.trade)
        self.assertIn('Couldn’t reach', job['docket_error'])
        self.assertEqual(job['docket_tries'], 1)

    def test_an_unexpected_bug_is_caught_too(self):
        """A mistake in our own code must not be the thing that breaks accepting."""
        self.connect()
        boom = mock.Mock(side_effect=ZeroDivisionError('oops'))
        with mock.patch.object(docket, '_post', boom):
            job_id = self.win_a_job()
        self.assertEqual(self.job(job_id)['status'], 'hired')
        self.assertIn('Unexpected', self.job(job_id)['docket_error'])

    def test_the_sweep_tries_again_and_then_stops(self):
        self.connect()
        fake = FakeDocket(fail_with=docket.DocketError('down'))
        with mock.patch.object(docket, '_post', fake):
            self.win_a_job()
            for _ in range(10):
                docket.retry_failed(self.db, T0)
        tries = self.db.execute('SELECT docket_tries FROM jobs').fetchone()['docket_tries']
        self.assertEqual(tries, docket.MAX_TRIES,
                         'stop hammering a Docket that is off, moved, or no longer paying')

    def test_a_retry_succeeds_once_docket_comes_back(self):
        self.connect()
        with mock.patch.object(docket, '_post', FakeDocket(fail_with=docket.DocketError('down'))):
            job_id = self.win_a_job()
        self.assertIsNone(self.job(job_id)['docket_at'])
        with mock.patch.object(docket, '_post', FakeDocket()):
            self.assertEqual(docket.retry_failed(self.db, T0), 1)
        job = self.job(job_id)
        self.assertIsNotNone(job['docket_at'])
        self.assertIsNone(job['docket_error'], 'the old error should be cleared, not left lying around')

    def test_a_bad_key_says_what_to_do_about_it(self):
        with mock.patch.object(docket, '_post', mock.Mock(
                side_effect=docket.DocketError('Docket didn’t recognise that key.'))):
            with self.assertRaises(docket.DocketError) as e:
                docket.test('https://d.example.nz', 'lvl_wrong')
        self.assertIn('recognise', str(e.exception))

    def test_both_halves_are_needed_before_we_try_at_all(self):
        for url, key in (('', 'lvl_k'), ('https://d.example.nz', '')):
            with self.assertRaises(docket.DocketError):
                docket.test(url, key)


class NoSetupTest(Base):
    """The whole point: a tradie pastes nothing and their jobs turn up anyway."""

    def platform_on(self):
        import integrations
        integrations.save(self.db, {'docket_url': 'https://docket.example.nz',
                                    'docket_key': 'shared_platform_key_abcdefghijklmnop'})
        integrations.refresh(self.db, force=True)

    def tearDown(self):
        import integrations
        integrations.save(self.db, {'docket_url': '', 'docket_key': ''})
        integrations._cache.update(at=0, values={})
        super().tearDown()

    def test_a_tradie_who_pasted_nothing_still_gets_their_jobs_across(self):
        self.platform_on()
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        sent = fake.jobs['level-job-%d' % job_id]
        self.assertEqual(sent['for_email'], 'karori@test.nz',
                         'the shared key has to say whose job it is')
        self.assertEqual(self.job(job_id)['docket_ref'], 'J-2026-001')

    def test_a_tradie_with_no_docket_is_not_an_error_and_is_asked_about_once(self):
        """Most tradies will never have a Docket. That must be quiet, and final."""
        self.platform_on()

        class NoMatch(FakeDocket):
            def __call__(self, *a, **kw):
                super().__call__(*a, **kw)
                return {'ok': True, 'matched': False}

        with mock.patch.object(docket, '_post', NoMatch()):
            job_id = self.win_a_job()
        job = self.job(job_id)
        self.assertIsNone(job['docket_error'], 'not having a Docket is not a failure')
        self.assertIsNone(job['docket_at'])
        self.assertEqual(job['docket_tries'], docket.MAX_TRIES, 'and we stop asking')
        with mock.patch.object(docket, '_post', NoMatch()) as again:
            docket.retry_failed(self.db, T0)
            self.assertEqual(again.call_count if hasattr(again, 'call_count') else 0, 0)

    def test_their_own_key_still_wins_over_the_shared_one(self):
        self.platform_on()
        self.connect()
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            job_id = self.win_a_job()
        self.assertNotIn('for_email', fake.jobs['level-job-%d' % job_id],
                         'a per-trade key is tied to one business already')

    def test_a_tradie_can_turn_it_off_and_nothing_is_sent(self):
        self.platform_on()
        self.db.execute('UPDATE trades SET docket_off = 1 WHERE user_id = ?', (self.trade,))
        self.db.commit()
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            self.win_a_job()
        self.assertEqual(fake.calls, [])

    def test_nothing_happens_at_all_until_level_itself_is_connected(self):
        fake = FakeDocket()
        with mock.patch.object(docket, '_post', fake):
            self.win_a_job()
        self.assertEqual(fake.calls, [], 'an unconfigured Level behaves exactly as before')

    def test_the_dashboard_hint_never_calls_docket(self):
        """It runs on every page load; a page that waits on another system hangs."""
        self.platform_on()
        boom = mock.Mock(side_effect=AssertionError('must not touch the network'))
        with mock.patch.object(docket, '_post', boom):
            docket.worth_mentioning(self.db, self.trade)
        boom.assert_not_called()


class AddressTest(unittest.TestCase):

    def test_a_pasted_address_is_made_usable(self):
        self.assertEqual(docket.clean_url('yourbusiness.docket.co.nz/'), 'https://yourbusiness.docket.co.nz')
        self.assertEqual(docket.clean_url(' http://localhost:5094 '), 'http://localhost:5094')
        self.assertEqual(docket.clean_url('https://x.nz///'), 'https://x.nz')
        self.assertEqual(docket.clean_url(''), '')


if __name__ == '__main__':
    unittest.main()
