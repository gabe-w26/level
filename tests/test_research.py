"""
Searching the web about a business, and the rules that make that safe.

A language model's summary is not evidence, and this is a summary about a real,
named, identifiable business. So the tests are mostly about what cannot get
through: a claim with no source, a conclusion, anything at all reaching a
customer's screen.

No network and no API key — the Anthropic call is stubbed, because what's worth
testing is the parsing and the rules around it, not that the SDK works.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'
os.environ['WAITLIST_DEFAULT'] = '0'

import app as A  # noqa: E402
import db as dbmod  # noqa: E402
import integrations  # noqa: E402
import research  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402


class Block:
    def __init__(self, type_, text=None, content=None):
        self.type, self.text, self.content = type_, text, content


class Response:
    def __init__(self, blocks, stop_reason='end_turn'):
        self.content, self.stop_reason = blocks, stop_reason


def said(payload, searches=1):
    blocks = [Block('web_search_tool_result', content=[{'title': 'x'}]) for _ in range(searches)]
    blocks.append(Block('text', text=json.dumps(payload)))
    return Response(blocks)


GOOD = {
    'presence': [{'platform': 'Facebook', 'url': 'https://facebook.com/tanebuilding',
                  'shows': 'A page posting job photos since 2019.'}],
    'trading_since': '2019, per the Facebook page',
    'observations': [{'note': 'The page lists Porirua, they told us Wellington.',
                      'url': 'https://facebook.com/tanebuilding'}],
    'not_found': ['No Instagram account under this name'],
    'same_name_confusion': False,
}


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={'anthropic_key': 'sk-test'})
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              ('trade', 'w@test.nz', hash_password('password123'), 'Wiremu',
                               ts(utcnow()))).lastrowid
        self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                        (uid, 'Tane Building', ts(utcnow())))
        self.db.commit()
        self.trade = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)


class ReadingWhatItFoundTest(Base):

    def test_a_good_answer_comes_through(self):
        out = research._read(said(GOOD))
        self.assertEqual(len(out['presence']), 1)
        self.assertEqual(out['presence'][0]['platform'], 'Facebook')
        self.assertEqual(len(out['observations']), 1)
        self.assertEqual(out['not_found'], ['No Instagram account under this name'])

    def test_a_claim_with_no_source_is_dropped(self):
        """The rule the whole thing rests on. A model that forgets the URL gets
        its claim thrown away, because an unsourced assertion about a real
        business is the one mistake there's no taking back."""
        payload = dict(GOOD, presence=[
            {'platform': 'Facebook', 'url': 'https://facebook.com/x', 'shows': 'A page.'},
            {'platform': 'Hearsay', 'shows': 'Someone said they were dodgy.'},
            {'platform': 'Vague', 'url': 'not-a-url', 'shows': 'Something.'},
        ])
        out = research._read(said(payload))
        self.assertEqual(len(out['presence']), 1)
        self.assertEqual(out['presence'][0]['platform'], 'Facebook')

    def test_an_observation_with_no_source_is_dropped_too(self):
        payload = dict(GOOD, observations=[{'note': 'They seem unreliable.'}])
        self.assertEqual(research._read(said(payload))['observations'], [])

    def test_prose_around_the_json_is_tolerated(self):
        blocks = [Block('text', text='Here is what I found:\n' + json.dumps(GOOD) + '\nHope that helps.')]
        out = research._read(Response(blocks))
        self.assertEqual(len(out['presence']), 1)

    def test_an_unreadable_answer_is_an_error_not_a_guess(self):
        with self.assertRaises(research.ResearchError):
            research._read(Response([Block('text', text='I could not find anything useful.')]))

    def test_confusion_between_businesses_is_carried_through(self):
        out = research._read(said(dict(GOOD, same_name_confusion=True)))
        self.assertTrue(out['same_name_confusion'])

    def test_a_refusal_records_nothing(self):
        import anthropic  # noqa: F401  (only to prove the import path exists)
        resp = Response([Block('text', text='{}')], stop_reason='refusal')
        self.assertEqual(resp.stop_reason, 'refusal')

    def test_a_failed_search_tool_is_noticed(self):
        """Server tools don't raise — a failure arrives as a result block whose
        content is an error object rather than a list."""
        bad = Response([Block('web_search_tool_result', content={'error_code': 'max_uses_exceeded'}),
                        Block('text', text=json.dumps(GOOD))])
        self.assertEqual(research.searches_failed(bad), 1)
        self.assertEqual(research.searches_failed(said(GOOD)), 0)


class KeepingItTest(Base):

    def test_findings_are_stored_with_a_date(self):
        research.save(self.db, self.trade['user_id'], research._read(said(GOOD)))
        got = research.latest(self.db, self.trade['user_id'])
        self.assertIsNotNone(got['checked_at'])
        self.assertEqual(len(got['findings']['presence']), 1)

    def test_each_search_is_kept_rather_than_overwritten(self):
        for _ in range(2):
            research.save(self.db, self.trade['user_id'], research._read(said(GOOD)))
        self.assertEqual(self.db.execute('SELECT COUNT(*) AS n FROM web_checks').fetchone()['n'], 2)

    def test_nothing_stored_means_nothing_shown(self):
        self.assertIsNone(research.latest(self.db, self.trade['user_id']))


class NoneOfItIsPublicTest(Base):
    """The rule that keeps this out of court."""

    def test_a_customer_never_sees_any_of_it(self):
        research.save(self.db, self.trade['user_id'], research._read(said(dict(
            GOOD, observations=[{'note': 'A review site thread calls them unreliable.',
                                 'url': 'https://nocowboys.co.nz/thread'}]))))
        page = A.app.test_client().get(f'/pros/{self.trade["user_id"]}').data.decode()
        self.assertNotIn('unreliable', page.lower())
        self.assertNotIn('nocowboys.co.nz/thread', page)
        self.assertNotIn('facebook.com/tanebuilding', page)

    def test_it_changes_no_badge_and_no_score(self):
        before = dict(self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                                      (self.trade['user_id'],)).fetchone())
        research.save(self.db, self.trade['user_id'], research._read(said(GOOD)))
        after = dict(self.db.execute('SELECT * FROM trades WHERE user_id = ?',
                                     (self.trade['user_id'],)).fetchone())
        self.assertEqual(before, after, 'a web search must not move anything on the record')

    def test_only_an_admin_can_run_it(self):
        path = f'/admin/trades/{self.trade["user_id"]}/research'
        self.assertIn(A.app.test_client().post(path).status_code, (302, 400, 403))


class TheAdminPageShowsItTest(Base):
    """A branch nothing renders is a branch nobody notices breaking.

    The register block is the first thing on that page and the only part of the
    report that isn't a model's retelling, so it gets rendered here rather than
    trusted to look after itself.
    """

    def admin(self):
        c = A.app.test_client()
        with c.session_transaction() as sess:
            sess['uid'] = self.db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()['id']
            sess['_csrf'] = 't'
        return c

    def store(self, **evidence):
        research.save(self.db, self.trade['user_id'], research._read(said(GOOD), evidence))
        return self.admin().get(f'/admin/trades/{self.trade["user_id"]}').data.decode()

    def test_the_register_answer_is_shown_with_the_other_companies(self):
        page = self.store(register={
            'asked': True, 'found': True, 'nzbn': '9429040000001',
            'name': 'Tane Building Limited', 'status': 'Registered', 'type': 'NZ Limited Company',
            'registered_on': '2014-02-11', 'has_ended': False, 'directors': ['Tane Ngata'],
            'other_companies': [{'name': 'Ngata Holdings Limited', 'status': 'Removed',
                                 'has_ended': True, 'shared_director': 'Tane Ngata'}],
            'other_companies_total': 1, 'other_companies_ended': 1,
            'source': 'NZ Business Number register (api.business.govt.nz)'},
            archive=[{'asked': True, 'domain': 'tanebuilding.co.nz', 'captures': 1,
                      'first_capture': '2014-03-02', 'last_capture': '2026-09-01',
                      'url': 'https://web.archive.org/web/*/tanebuilding.co.nz'}],
            tool_calls=2)
        self.assertIn('Tane Building Limited', page)
        self.assertIn('Ngata Holdings Limited', page)
        self.assertIn('Tane Ngata', page)
        self.assertIn('first archived', page)
        self.assertIn('web.archive.org', page)
        # Said plainly, because a closed company is not evidence of anything.
        self.assertIn('close for every reason', page)

    def test_a_trading_name_that_is_not_the_registered_name_is_pointed_out(self):
        page = self.store(register={
            'asked': True, 'found': True, 'nzbn': '9429040000001',
            'name': 'Waikato Holdings No. 4 Limited', 'status': 'Registered', 'type': '',
            'registered_on': '2024-08-01', 'has_ended': False, 'directors': [],
            'other_companies': [], 'other_companies_total': 0, 'other_companies_ended': 0,
            'source': 'x'})
        self.assertIn('registered as', page)

        page2 = self.store(register={
            'asked': True, 'found': True, 'nzbn': '9429040000001',
            'name': 'Tane Building Ltd', 'status': 'Registered', 'type': '',
            'registered_on': '2014-02-11', 'has_ended': False, 'directors': [],
            'other_companies': [], 'other_companies_total': 0, 'other_companies_ended': 0,
            'source': 'x'})
        self.assertNotIn('registered as', page2)

    def test_a_domain_the_archive_has_never_seen_says_so(self):
        page = self.store(archive=[{'asked': True, 'domain': 'brandnew.co.nz', 'captures': 0,
                                    'note': 'never captured'}])
        self.assertIn('never been archived', page)

    def test_a_report_with_no_lookups_renders_as_it_always_did(self):
        page = self.store()
        self.assertIn('facebook.com/tanebuilding', page)
        self.assertNotIn('The register says', page)


class WithoutAKeyTest(Base):

    def test_it_says_so_rather_than_pretending(self):
        integrations._cache.update(at=10 ** 12, values={})
        self.assertFalse(research.enabled())
        with self.assertRaises(research.ResearchError):
            research.run(self.trade)


class DomainReadingTest(unittest.TestCase):
    """The archive tool takes a domain from the model, so it is checked first.

    Not because a model is malicious, but because "the model said so" is not a
    reason for this server to go and fetch something. Everything that isn't
    plainly a hostname is refused before it reaches a URL.
    """

    def test_a_domain_is_read_out_of_whatever_shape_it_arrives_in(self):
        for given in ('tanebuilding.co.nz', 'www.tanebuilding.co.nz', 'TaneBuilding.co.nz',
                      'https://tanebuilding.co.nz/about', 'http://www.tanebuilding.co.nz:8080/x?y=1',
                      'tanebuilding.co.nz.', '  tanebuilding.co.nz  '):
            self.assertEqual(research._tidy_domain(given), 'tanebuilding.co.nz', given)

    def test_anything_that_is_not_a_hostname_is_refused(self):
        for junk in ('', None, 'localhost', '127.0.0.1', '169.254.169.254', '10.0.0.1',
                     'not a domain', 'a b.co.nz', 'file:///etc/passwd', '../../etc/passwd',
                     'nz', 'x.co.nz&url=evil.com', '-lead.co.nz', 'x..co.nz',
                     'x.co.123', 'a' * 300 + '.co.nz'):
            self.assertIsNone(research._tidy_domain(junk), repr(junk))

    def test_extra_url_parts_are_dropped_rather_than_carried_into_the_request(self):
        """The domain goes into a query string, so anything trailing it matters.

        A path or query is stripped and the hostname kept — the same tolerance
        that lets an admin paste a URL — so a value carrying its own parameters
        cannot rewrite the request we make.
        """
        for given in ('x.co.nz/../../y', 'x.co.nz?a=b', 'x.co.nz#frag',
                      'x.co.nz/cdx?url=other.com&limit=99999'):
            self.assertEqual(research._tidy_domain(given), 'x.co.nz', given)

    def test_an_unreadable_domain_never_reaches_the_network(self):
        # Proven by there being no network in this test run at all: a call that
        # tried would raise, and archive_history does not.
        with mock.patch.object(research, '_ask_archive',
                               side_effect=AssertionError('must not be called')):
            self.assertEqual(research.archive_history('!!!')['captures'], 0)


class ArchiveTest(unittest.TestCase):
    """First seen is the best dated evidence there is for how long they've been going."""

    def rows(self, stamp, original='http://tanebuilding.co.nz/'):
        return [['timestamp', 'original'], [stamp, original]]

    def test_the_first_and_last_capture_come_back_as_dates(self):
        with mock.patch.object(research, '_ask_archive',
                               side_effect=[self.rows('20140302120000'), self.rows('20260901090000')]):
            out = research.archive_history('tanebuilding.co.nz')
        self.assertTrue(out['asked'])
        self.assertEqual(out['first_capture'], '2014-03-02')
        self.assertEqual(out['last_capture'], '2026-09-01')
        self.assertEqual(out['url'], 'https://web.archive.org/web/*/tanebuilding.co.nz')

    def test_never_captured_and_could_not_ask_are_different_answers(self):
        # The whole reason companies.py has RegisterDown. A ten-year-old business
        # and a domain that never existed look identical if a timeout is recorded
        # as an absence.
        with mock.patch.object(research, '_ask_archive', return_value=[]):
            never = research.archive_history('tanebuilding.co.nz')
        self.assertTrue(never['asked'])
        self.assertEqual(never['captures'], 0)

        with mock.patch.object(research, '_ask_archive', side_effect=OSError('timed out')):
            down = research.archive_history('tanebuilding.co.nz')
        self.assertFalse(down['asked'])
        self.assertIn('could not be reached', down['why'])
        self.assertNotIn('captures', down)

    def test_a_junk_timestamp_is_not_turned_into_a_date(self):
        with mock.patch.object(research, '_ask_archive', return_value=self.rows('not-a-date')):
            out = research.archive_history('tanebuilding.co.nz')
        self.assertEqual(out['captures'], 0)

    def test_a_shape_we_did_not_expect_does_not_raise(self):
        for bad in (None, {}, 'text', [['timestamp']], [['t'], 'notalist'], [['t'], []]):
            with mock.patch.object(research, '_ask_archive', return_value=bad):
                self.assertIn('asked', research.archive_history('tanebuilding.co.nz'))


class RegisterToolTest(unittest.TestCase):
    """The register is the one source here that isn't somebody's web page.

    It is also the only one that can answer the question the whole feature was
    asked for: what else have these directors run, and how much of it ended.
    """

    ENTITY = {'nzbn': '9429040000001', 'name': 'Tane Building Limited', 'status': 'Registered',
              'type': 'NZ Limited Company', 'registered_on': '2014-02-11', 'ended': False,
              'directors': [{'name': 'Tane Ngata', 'from': '2014-02-11'}]}

    def ask(self, args, **patches):
        import companies
        with mock.patch.object(companies, 'configured', return_value=True):
            for name, value in patches.items():
                patcher = mock.patch.object(companies, name, **value)
                patcher.start()
                self.addCleanup(patcher.stop)
            return research.register_lookup(args)

    def test_no_key_is_reported_as_not_having_asked(self):
        import companies
        with mock.patch.object(companies, 'configured', return_value=False):
            out = research.register_lookup({'nzbn': '9429040000001'})
        self.assertFalse(out['asked'])
        self.assertIn('no NZBN API key', out['why'])

    def test_an_entity_comes_back_with_the_directors_other_companies(self):
        out = self.ask({'nzbn': '9429040000001'}, history={'return_value': {
            'entity': self.ENTITY,
            'others': [dict(self.ENTITY, nzbn='9429040000002', name='Ngata Holdings Limited',
                            status='Removed', ended=True, director='Tane Ngata'),
                       dict(self.ENTITY, nzbn='9429040000003', name='Coastal Decks Limited',
                            status='Registered', ended=False, director='Tane Ngata')],
            'ended': [1], 'ended_count': 1, 'total': 3}})
        self.assertTrue(out['asked'])
        self.assertTrue(out['found'])
        self.assertEqual(out['registered_on'], '2014-02-11')
        self.assertEqual(out['directors'], ['Tane Ngata'])
        self.assertEqual(out['other_companies_total'], 2)
        self.assertEqual(out['other_companies_ended'], 1)
        self.assertEqual([c['name'] for c in out['other_companies']],
                         ['Ngata Holdings Limited', 'Coastal Decks Limited'])
        self.assertIn('api.business.govt.nz', out['source'])

    def test_no_such_nzbn_and_register_down_are_different_answers(self):
        import companies
        nope = self.ask({'nzbn': '9429040000001'}, history={'return_value': None})
        self.assertTrue(nope['asked'])
        self.assertFalse(nope['found'])

        down = self.ask({'nzbn': '9429040000009'},
                        history={'side_effect': companies.RegisterDown('timed out')})
        self.assertFalse(down['asked'])
        self.assertNotIn('found', down)

    def test_a_name_search_returns_the_matches_it_found(self):
        out = self.ask({'name': 'Tane Building'}, search={'return_value': [self.ENTITY]})
        self.assertTrue(out['found'])
        self.assertEqual(out['matches'][0]['nzbn'], '9429040000001')

    def test_asking_nothing_is_an_error_not_a_lookup(self):
        import companies
        with mock.patch.object(companies, 'configured', return_value=True):
            self.assertIn('error', research.register_lookup({}))

    def test_a_tool_that_blows_up_does_not_lose_the_run(self):
        import companies
        with mock.patch.object(companies, 'configured', side_effect=RuntimeError('boom')):
            out = research._use_tool('nz_business_register', {'nzbn': '1'})
        self.assertFalse(out['asked'])
        self.assertIn('RuntimeError', out['why'])

    def test_a_tool_we_do_not_have_is_answered_not_raised(self):
        out = research._use_tool('rm_minus_rf', {})
        self.assertFalse(out['asked'])
        self.assertIn('no tool called', out['why'])

    def test_bad_input_shapes_are_tolerated(self):
        for args in (None, [], 'text', 42):
            self.assertIn('asked', research._use_tool('web_archive_history', args))


class EvidenceTest(unittest.TestCase):
    """What our tools said is kept as they said it, beside the model's report.

    The model wrote the prose; the register and the archive answered for
    themselves. Folding one into the other is how "a language model's summary is
    not evidence" stops being true in practice.
    """

    def test_only_answers_we_actually_got_are_filed(self):
        ev = {'register': None, 'archive': [], 'tool_calls': 0}
        research._keep(ev, 'nz_business_register', {'asked': False, 'why': 'down'})
        research._keep(ev, 'web_archive_history', {'asked': False, 'why': 'down'})
        self.assertIsNone(ev['register'])
        self.assertEqual(ev['archive'], [])

    def test_a_name_search_is_not_filed_as_the_entity(self):
        # A list of businesses sharing a name is not a record of this business.
        ev = {'register': None, 'archive': [], 'tool_calls': 0}
        research._keep(ev, 'nz_business_register', {'asked': True, 'found': True, 'matches': [{}]})
        self.assertIsNone(ev['register'])
        research._keep(ev, 'nz_business_register',
                       {'asked': True, 'found': True, 'nzbn': '9429040000001'})
        self.assertEqual(ev['register']['nzbn'], '9429040000001')

    def test_the_same_domain_is_not_filed_twice(self):
        ev = {'register': None, 'archive': [], 'tool_calls': 0}
        for _ in range(3):
            research._keep(ev, 'web_archive_history',
                           {'asked': True, 'domain': 'x.co.nz', 'captures': 1})
        self.assertEqual(len(ev['archive']), 1)

    def test_evidence_reaches_the_findings_and_is_counted(self):
        out = research._read(said(GOOD), {'register': {'nzbn': '9429040000001'},
                                          'archive': [{'domain': 'x.co.nz'}], 'tool_calls': 3})
        self.assertEqual(out['register']['nzbn'], '9429040000001')
        self.assertEqual(out['lookups'], 3)
        self.assertEqual(len(out['archive']), 1)

    def test_findings_without_any_evidence_still_read_cleanly(self):
        out = research._read(said(GOOD))
        self.assertIsNone(out['register'])
        self.assertEqual(out['archive'], [])
        self.assertEqual(out['lookups'], 0)

    def test_a_failed_fetch_is_noticed_like_a_failed_search(self):
        bad = Response([Block('web_fetch_tool_result', content={'type': 'web_fetch_tool_error'}),
                        Block('text', text=json.dumps(GOOD))])
        self.assertEqual(research.searches_failed(bad), 1)


class Asks:
    """A tool_use block, the shape the loop reads."""

    type = 'tool_use'

    def __init__(self, name, args, id_='tu_1'):
        self.name, self.input, self.id = name, args, id_


class Turns:
    """A stand-in Anthropic client that hands back a scripted run of turns."""

    def __init__(self, turns):
        self.turns, self.sent = list(turns), []
        self.beta = type('B', (), {'messages': self})()

    def create(self, **kw):
        self.sent.append(kw)
        return self.turns.pop(0) if self.turns else said(GOOD)


class LoopTest(unittest.TestCase):
    """The loop, which is where a tool-using agent actually goes wrong.

    A turn can end three ways that are not the end of the work: paused mid
    search, asking us something, or out of rounds. Only the third is a failure,
    and it has to fail rather than report a half-finished answer.
    """

    def setUp(self):
        # A dict is close enough to a sqlite3.Row for _ask(): keys() and [].
        self.trade = {'business_name': 'Tane Building', 'nzbn': '9429040000001',
                      'licence_number': 'BP123456', 'about': None,
                      'website': 'tanebuilding.co.nz'}

    def go(self, turns, answer=None):
        import anthropic
        client = Turns(turns)
        with mock.patch.object(research, 'enabled', return_value=True), \
             mock.patch.object(integrations, 'get', return_value='sk-test'), \
             mock.patch.object(anthropic, 'Anthropic', return_value=client), \
             mock.patch.object(research, '_use_tool',
                               return_value=answer if answer is not None
                               else {'asked': True, 'found': True, 'nzbn': '9429040000001'}):
            return research.run(self.trade), client

    def test_a_tool_the_model_asks_for_is_answered_and_the_run_carries_on(self):
        asking = Response([Asks('nz_business_register', {'nzbn': '9429040000001'})], 'tool_use')
        out, client = self.go([asking, said(GOOD)])
        self.assertEqual(out['presence'][0]['platform'], 'Facebook')
        self.assertEqual(out['register']['nzbn'], '9429040000001')
        self.assertEqual(out['lookups'], 1)

        # The answer went back as a tool_result in a new user turn, against the id.
        last = client.sent[-1]['messages'][-1]
        self.assertEqual(last['role'], 'user')
        self.assertEqual(last['content'][0]['type'], 'tool_result')
        self.assertEqual(last['content'][0]['tool_use_id'], 'tu_1')

    def test_several_tools_in_one_turn_are_all_answered(self):
        asking = Response([Asks('nz_business_register', {'nzbn': '1'}, 'tu_a'),
                           Asks('web_archive_history', {'domain': 'x.co.nz'}, 'tu_b')], 'tool_use')
        out, client = self.go([asking, said(GOOD)])
        self.assertEqual(out['lookups'], 2)
        ids = [r['tool_use_id'] for r in client.sent[-1]['messages'][-1]['content']]
        self.assertEqual(ids, ['tu_a', 'tu_b'])

    def test_a_paused_turn_is_handed_straight_back(self):
        out, client = self.go([Response([Block('text', text='')], 'pause_turn'), said(GOOD)])
        self.assertEqual(out['presence'][0]['platform'], 'Facebook')
        self.assertEqual(client.sent[-1]['messages'][-1]['role'], 'assistant')

    def test_the_lookup_budget_is_enforced(self):
        """A model that keeps asking is told the budget is gone.

        Each lookup costs a real request to a real register, so the cap has to
        bite in the loop and not rely on the model being reasonable. Asserted by
        counting the calls actually made, not just that the run ended.
        """
        asking = [Response([Asks('nz_business_register', {'nzbn': '1'})], 'tool_use')
                  for _ in range(5)]
        used = []
        import anthropic
        client = Turns(asking + [said(GOOD)])
        with mock.patch.object(research, 'MAX_TOOL_CALLS', 2), \
             mock.patch.object(research, 'enabled', return_value=True), \
             mock.patch.object(integrations, 'get', return_value='sk-test'), \
             mock.patch.object(anthropic, 'Anthropic', return_value=client), \
             mock.patch.object(research, '_use_tool',
                               side_effect=lambda n, a: used.append(n) or {'asked': False}):
            out = research.run(self.trade)

        self.assertEqual(len(used), 2, 'the third ask must not reach a register')
        self.assertEqual(out['lookups'], 2)
        # And the model is told why, rather than being left to wonder.
        refusals = [json.loads(r['content']) for turn in client.sent
                    for msg in turn['messages'] if msg['role'] == 'user'
                    and isinstance(msg['content'], list)
                    for r in msg['content'] if r.get('type') == 'tool_result']
        self.assertTrue(any('used up its lookups' in (r.get('why') or '') for r in refusals))

    def test_running_out_of_rounds_is_an_error_not_a_half_report(self):
        paused = [Response([Block('text', text='')], 'pause_turn')
                  for _ in range(research.MAX_ROUNDS + 2)]
        with self.assertRaises(research.ResearchError) as caught:
            self.go(paused)
        self.assertIn('didn’t finish', str(caught.exception))

    def test_a_refusal_still_records_nothing(self):
        with self.assertRaises(research.ResearchError):
            self.go([Response([Block('text', text='no')], 'refusal')])

    def test_all_four_tools_are_offered(self):
        _, client = self.go([said(GOOD)])
        offered = client.sent[0]['tools']
        names = [t.get('name') for t in offered]
        self.assertEqual(names, ['web_search', 'web_fetch', 'nz_business_register',
                                 'web_archive_history'])
        kinds = {t.get('name'): t.get('type') for t in offered}
        self.assertEqual(kinds['web_search'], 'web_search_20260209')
        self.assertEqual(kinds['web_fetch'], 'web_fetch_20260209')
        self.assertIsNone(kinds['nz_business_register'], 'ours are client tools, not server ones')

    def test_fetching_is_held_to_the_same_allow_list_as_searching(self):
        # A fetcher that can open anything is a different tool from one that can
        # read the sites we chose to look at.
        _, client = self.go([said(GOOD)])
        fetch = next(t for t in client.sent[0]['tools'] if t.get('name') == 'web_fetch')
        self.assertEqual(fetch['allowed_domains'], research.LOOK_AT)
        self.assertEqual(fetch['max_uses'], research.MAX_FETCHES)


if __name__ == '__main__':
    unittest.main()
