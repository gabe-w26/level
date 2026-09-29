"""
What the public register says, and what we're allowed to do with it.

The register is free and factual, which makes it tempting to treat as a verdict.
Most of this file is about not doing that: nothing negative is published on its
own, a near-match is reported rather than accepted, and every answer is stored
with the date it was given — because "what did it say when we looked" is the
question you need if somebody later disputes it.

No network. The HTTP call is stubbed so the parsing, matching and storing are
proven; the URL and auth header are settings precisely because they are the part
that will need correcting against the real API once there's a key.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import json
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
import companies  # noqa: E402
import db as dbmod  # noqa: E402
import integrations  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402


def entity(nzbn, name, status='Registered', directors=(), registered='2019-04-02'):
    return {
        'nzbn': nzbn, 'entityName': name, 'entityStatusDescription': status,
        'entityTypeDescription': 'NZ Limited Company', 'registrationDate': registered,
        'roles': [{'roleType': 'Director',
                   'rolePerson': {'firstName': d.split(' ')[0], 'lastName': d.split(' ')[-1]},
                   'roleStartDate': '2019-04-02'} for d in directors],
    }


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={'nzbn_key': 'test-key'})
        self.register = {}          # nzbn -> payload
        self.by_name = {}           # search term -> [payloads]
        self.real_get = companies._get
        companies._get = self.fake_get
        self.trade = self.a_trade()

    def tearDown(self):
        companies._get = self.real_get
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def fake_get(self, path):
        if path.startswith('/entities?'):
            import urllib.parse
            term = urllib.parse.unquote(path.split('search-term=', 1)[-1])
            return {'items': self.by_name.get(term, [])}
        return self.register.get(path.rsplit('/', 1)[-1])

    def a_trade(self, name='Tane Building', nzbn='9429001234567'):
        self.n = getattr(self, 'n', 0) + 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              ('trade', f'w{self.n}@test.nz', hash_password('password123'),
                               'Wiremu', ts(utcnow()))).lastrowid
        self.db.execute('INSERT INTO trades (user_id, business_name, nzbn, created_at) '
                        'VALUES (?,?,?,?)', (uid, name, nzbn, ts(utcnow())))
        self.db.commit()
        return self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()


class ReadingTheRegisterTest(Base):

    def test_it_pulls_out_the_few_things_we_use(self):
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        e = companies.entity('9429001234567')
        self.assertEqual(e['name'], 'Tane Building Limited')
        self.assertEqual(e['status'], 'Registered')
        self.assertEqual(e['registered_on'], '2019-04-02')
        self.assertEqual([d['name'] for d in e['directors']], ['Wiremu Tane'])

    def test_a_missing_field_means_we_do_not_know_not_a_crash(self):
        self.register['9429001234567'] = {'nzbn': '9429001234567'}
        e = companies.entity('9429001234567')
        self.assertIsNotNone(e, 'a thin payload must not take an admin page down')
        self.assertEqual(e['name'], '')

    def test_an_ended_company_is_recognised_however_it_is_worded(self):
        for wording in ('Removed', 'In Liquidation', 'Struck Off', 'Receivership'):
            self.assertTrue(companies._has_ended(wording), wording)
        for fine in ('Registered', 'Active', ''):
            self.assertFalse(companies._has_ended(fine), fine)

    def test_no_key_means_no_calls_at_all(self):
        """Not having a key is a kind of "couldn't ask", not a kind of "no"."""
        companies._get = self.real_get
        integrations._cache.update(at=10 ** 12, values={})
        self.assertFalse(companies.configured())
        with self.assertRaises(companies.RegisterDown):
            companies.entity('9429001234567')
        self.assertIsNone(companies.run(self.db, self.trade), 'and nothing is recorded')


class MatchingTheNameTest(Base):

    def test_the_noise_is_forgiven(self):
        for claimed, registered in [('Tane Building', 'Tane Building Limited'),
                                    ('Tane Building Ltd', 'TANE BUILDING'),
                                    ('The Tane Building Co', 'Tane Building')]:
            self.assertTrue(companies.name_matches(claimed, registered), f'{claimed} / {registered}')

    def test_a_different_business_is_not_a_match(self):
        self.assertFalse(companies.name_matches('Tane Building', 'Smith Plumbing'))
        self.assertFalse(companies.name_matches('', 'Tane Building'))


class DirectorHistoryTest(Base):

    def test_it_finds_the_other_companies_behind_the_same_person(self):
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = [
            entity('9429001234567', 'Tane Building Limited'),          # itself, skipped
            entity('9429009999999', 'Tane Homes Limited', status='Removed'),
            entity('9429008888888', 'Tane Developments Limited', status='In Liquidation'),
        ]
        h = companies.history('9429001234567')
        self.assertEqual(len(h['others']), 2, 'the business itself is not one of its others')
        self.assertEqual(h['ended_count'], 2)

    def test_a_clean_history_reads_as_clean(self):
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = [entity('9429007777777', 'Tane Joinery Limited')]
        h = companies.history('9429001234567')
        self.assertEqual(h['ended_count'], 0)


class WhatWeDoWithItTest(Base):
    """The part that matters: a register answer is evidence, not a verdict."""

    def clean_register(self):
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = []

    def test_a_plain_confirmation_ticks_the_nzbn_off(self):
        self.clean_register()
        companies.run(self.db, self.trade)
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                            (self.trade['user_id'],)).fetchone()
        self.assertIsNotNone(t['nzbn_checked_at'], 'the number is registered to this name')
        self.assertEqual(t['nzbn_status'], 'Registered')

    def test_a_name_that_does_not_match_is_not_ticked_off(self):
        self.register['9429001234567'] = entity('9429001234567', 'Someone Else Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = []
        companies.run(self.db, self.trade)
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                            (self.trade['user_id'],)).fetchone()
        self.assertIsNone(t['nzbn_checked_at'], 'a near-miss is for a person to judge')

    def test_a_bad_history_changes_nothing_on_its_own(self):
        """The whole point. Two liquidations is a reason to look, not a verdict
        we are entitled to act on or publish."""
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = [
            entity('9429009999999', 'Tane Homes Limited', status='Removed'),
            entity('9429008888888', 'Tane Developments Limited', status='In Liquidation'),
        ]
        companies.run(self.db, self.trade)
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                            (self.trade['user_id'],)).fetchone()
        self.assertIsNotNone(t['nzbn_checked_at'], 'this NZBN is still genuinely registered')
        self.assertIsNone(t['vetting_status'], 'and nothing has judged them')
        check = companies.latest(self.db, self.trade['user_id'])
        self.assertEqual(check['ended_companies'], 2, 'but it is on file for a person to read')

    def test_it_is_a_dated_snapshot_not_a_running_total(self):
        self.clean_register()
        companies.run(self.db, self.trade)
        companies.run(self.db, self.trade)
        rows = self.db.execute('SELECT * FROM company_checks WHERE trade_id = ?',
                               (self.trade['user_id'],)).fetchall()
        self.assertEqual(len(rows), 2, 'each answer is kept, with when it was given')
        self.assertIsNotNone(companies.latest(self.db, self.trade['user_id'])['checked_at'])

    def test_an_unknown_nzbn_is_recorded_rather_than_forgotten(self):
        companies.run(self.db, self.trade)      # nothing in the register
        check = companies.latest(self.db, self.trade['user_id'])
        self.assertFalse(check['found'])
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                            (self.trade['user_id'],)).fetchone()
        self.assertIsNone(t['nzbn_checked_at'])

    def test_a_register_we_could_not_reach_records_nothing_at_all(self):
        """The distinction that keeps a real business from being written down as
        bogus because an API had a bad afternoon."""
        def down(path):
            raise companies.RegisterDown('timed out')
        companies._get = down
        self.assertIsNone(companies.run(self.db, self.trade))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM company_checks').fetchone()['n'],
                         0, 'nothing may be recorded when we could not ask')

    def test_a_register_that_answers_no_such_entity_is_recorded(self):
        """Different thing entirely, and worth having on file."""
        companies._get = lambda path: None
        companies.run(self.db, self.trade)
        self.assertFalse(companies.latest(self.db, self.trade['user_id'])['found'])

    def test_no_nzbn_means_nothing_to_ask(self):
        t = self.a_trade(name='No Number Ltd', nzbn='')
        self.assertIsNone(companies.run(self.db, t))


class ReasonsToLookTest(Base):

    def test_it_gives_words_not_a_score(self):
        reasons = companies.worth_a_look(
            {'found': True, 'name_matched': False, 'status': 'Removed',
             'ended_companies': 2, 'other_companies': 3})
        self.assertEqual(len(reasons), 3)
        for r in reasons:
            self.assertTrue(r.endswith('.'), 'these are sentences somebody reads')
            self.assertNotIn('score', r.lower())

    def test_a_clean_check_gives_no_reasons(self):
        self.assertEqual(companies.worth_a_look(
            {'found': True, 'name_matched': True, 'status': 'Registered',
             'ended_companies': 0, 'other_companies': 1}), [])

    def test_nothing_found_gives_no_reasons(self):
        self.assertEqual(companies.worth_a_look({'found': False}), [])
        self.assertEqual(companies.worth_a_look(None), [])


class OnlyAnAdminTest(Base):

    def test_the_button_is_admin_only(self):
        path = f'/admin/trades/{self.trade["user_id"]}/company'
        self.assertIn(A.app.test_client().post(path).status_code, (302, 400, 403))

    def test_nothing_about_past_companies_reaches_a_public_profile(self):
        """The rule that keeps this out of court."""
        self.register['9429001234567'] = entity('9429001234567', 'Tane Building Limited',
                                                directors=['Wiremu Tane'])
        self.by_name['Wiremu Tane'] = [
            entity('9429009999999', 'Tane Homes Limited', status='Removed')]
        companies.run(self.db, self.trade)
        page = A.app.test_client().get(f'/pros/{self.trade["user_id"]}').data.decode()
        self.assertNotIn('Tane Homes', page)
        self.assertNotIn('Removed', page)
        self.assertNotIn('liquidation', page.lower())


if __name__ == '__main__':
    unittest.main()
