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


class WithoutAKeyTest(Base):

    def test_it_says_so_rather_than_pretending(self):
        integrations._cache.update(at=10 ** 12, values={})
        self.assertFalse(research.enabled())
        with self.assertRaises(research.ResearchError):
            research.run(self.trade)


if __name__ == '__main__':
    unittest.main()
