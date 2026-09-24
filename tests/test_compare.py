"""
Tests for comparing quotes: the GST trap, the low-price note, and spotting what a
quote doesn't cover. The rule being tested throughout is that a note must be
something the quotes actually say — never an opinion about who to hire.

Run from the project folder:  python3 -m unittest discover tests -v
"""
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
import compare  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
from engine import ts  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 24, 0, 0, 0)

FULL = {'id': 1, 'price_type': 'fixed', 'amount_low': 2000, 'gst_included': 1,
        'inclusions': 'Labour, materials and rubbish removal', 'exclusions': '',
        'message': 'Two days on site, starting with the worst boards. We make good afterwards and take the old timber away.'}
ALSO_FULL = {'id': 2, 'price_type': 'fixed', 'amount_low': 2200, 'gst_included': 1,
             'inclusions': 'Labour and materials, waste taken away', 'exclusions': '',
             'message': 'We can fit this in next month. We patch and tidy up before we go, and the waste goes with us.'}
CHEAP = {'id': 3, 'price_type': 'fixed', 'amount_low': 1100, 'gst_included': 0,
         'inclusions': 'Labour only', 'exclusions': 'Rubbish removal, making good',
         'message': 'Can do it quickly, labour only, you sort the rest out yourself afterwards.'}


def texts(notes, quote_id):
    return ' | '.join(n['text'] for n in notes[quote_id])


class CompareTest(unittest.TestCase):

    # ── GST ──
    def test_plus_gst_price_is_converted_for_comparison(self):
        note = compare.gst_note(CHEAP, [FULL, ALSO_FULL])
        self.assertIsNotNone(note)
        self.assertIn('1,265', note['text'])            # 1100 + 15%
        self.assertIn('plus GST', note['text'])

    def test_no_gst_note_when_everyone_quotes_the_same_way(self):
        both_excl = dict(FULL, gst_included=0), dict(ALSO_FULL, gst_included=0)
        self.assertIsNone(compare.gst_note(dict(CHEAP, gst_included=0), list(both_excl)))

    def test_incl_gst_handles_ranges_and_site_visits(self):
        self.assertEqual(compare.incl_gst({'price_type': 'range', 'amount_low': 1000, 'amount_high': 2000,
                                           'gst_included': 1}), 1500)
        self.assertIsNone(compare.incl_gst({'price_type': 'site_visit', 'amount_low': None}))

    # ── price ──
    def test_low_price_is_flagged_against_the_other_quotes(self):
        note = compare.price_note(CHEAP, [FULL, ALSO_FULL])
        self.assertIsNotNone(note)
        self.assertIn('below the other quotes', note['text'])

    def test_a_normal_price_is_not_flagged(self):
        self.assertIsNone(compare.price_note(FULL, [ALSO_FULL, dict(CHEAP, amount_low=1900, gst_included=1)]))

    def test_falls_back_to_the_price_guide_when_there_is_nothing_to_compare(self):
        guide = {'n': 9, 'low': 1800, 'mid': 2100, 'high': 2600}
        note = compare.price_note(dict(FULL, amount_low=900), [], guide)
        self.assertIn('usually costs', note['text'])
        self.assertIsNone(compare.price_note(FULL, [], guide))          # 2000 is in the normal range
        self.assertIsNone(compare.price_note(dict(FULL, amount_low=900), []))   # no guide, no claim

    # ── scope ──
    def test_says_what_a_quote_leaves_out(self):
        notes = compare.scope_notes(CHEAP, [FULL, ALSO_FULL])
        text = ' | '.join(n['text'] for n in notes)
        self.assertIn('excludes rubbish removal', text)
        self.assertIn('excludes making good afterwards', text)
        self.assertIn('doesn’t mention materials', text)

    def test_a_complete_quote_gets_no_notes(self):
        self.assertEqual(compare.scope_notes(FULL, [ALSO_FULL]), [])

    def test_nothing_is_flagged_when_nobody_mentions_it(self):
        bare = [{'id': i, 'price_type': 'fixed', 'amount_low': 2000, 'gst_included': 1,
                 'inclusions': '', 'exclusions': '', 'message': 'Happy to do this job for you.'}
                for i in (1, 2, 3)]
        for q in bare:
            self.assertEqual(compare.scope_notes(q, [o for o in bare if o['id'] != q['id']]), [])

    def test_notes_for_covers_every_quote(self):
        notes = compare.notes_for([FULL, ALSO_FULL, CHEAP])
        self.assertEqual(notes[1], [])
        self.assertEqual(notes[2], [])
        self.assertIn('below the other quotes', texts(notes, 3))


class JobPageTest(unittest.TestCase):
    """The same thing, end to end, as a customer sees it."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        dbmod._DATABASE = self.tmp.name
        init_db()
        A.app.testing = True
        self.db = dbmod.get_db()
        integrations._cache.update(at=10 ** 12, values={})
        self.cat = self.db.execute("SELECT id FROM categories WHERE slug = 'builder'").fetchone()['id']
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.n = 0

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def user(self, role='customer'):
        self.n += 1
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                              (role, f'u{self.n}@test.nz', hash_password('password123'), f'Person {self.n}',
                               ts(T0))).lastrowid
        if role == 'trade':
            self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                            (uid, f'Trade {uid}', ts(T0)))
            self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
            self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
            self.db.commit()
            t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
            billing.choose_plan(self.db, t, f'u{self.n}@test.nz', 'large', '', '', at=T0)
        self.db.commit()
        return uid

    def test_customer_sees_the_notes_and_the_price_is_not_the_headline(self):
        customer = self.user()
        trades = [self.user('trade') for _ in range(3)]
        job_id = engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Deck repair',
            description='Replace rotten boards.', value_band='medium', timing='weeks',
            property_type='house'), at=T0)[0]
        for trade, q in zip(trades, (FULL, ALSO_FULL, CHEAP)):
            engine.submit_quote(self.db, job_id, trade, dict(
                price_type='fixed', amount_low=q['amount_low'], gst_included=q['gst_included'],
                message=q['message'], inclusions=q['inclusions'], exclusions=q['exclusions'] or None),
                at=T0 + timedelta(minutes=10))

        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = customer
            s['_csrf'] = 't'
        page = c.get(f'/me/jobs/{job_id}').data.decode()
        self.assertIn('below the other quotes', page)
        self.assertIn('including GST', page)
        self.assertIn('excludes rubbish removal', page)
        self.assertIn('Before you choose', page)
        # Price now sits with what you get, rather than in the heading.
        self.assertIn('quote-price-row', page)
        self.assertNotIn('<div class="quote-price">', page)

    def test_the_phone_app_gets_the_same_notes(self):
        customer = self.user()
        trades = [self.user('trade') for _ in range(3)]
        job_id = engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Deck repair',
            description='Replace rotten boards.', value_band='medium', timing='weeks',
            property_type='house'), at=T0)[0]
        for trade, q in zip(trades, (FULL, ALSO_FULL, CHEAP)):
            engine.submit_quote(self.db, job_id, trade, dict(
                price_type='fixed', amount_low=q['amount_low'], gst_included=q['gst_included'],
                message=q['message'], inclusions=q['inclusions'], exclusions=q['exclusions'] or None),
                at=T0 + timedelta(minutes=10))

        c = A.app.test_client()
        email = self.db.execute('SELECT email FROM users WHERE id = ?', (customer,)).fetchone()['email']
        token = c.post('/api/mobile/login', json={'login': email, 'password': 'password123'}).get_json()['token']
        body = c.get(f'/api/mobile/customer/jobs/{job_id}',
                     headers={'Authorization': f'Bearer {token}'}).get_json()
        got = {q['amount_low']: ' | '.join(n['text'] for n in q['notes']) for q in body['quotes']}
        self.assertEqual(got[2000], '')
        self.assertIn('below the other quotes', got[1100])
        self.assertIn('including GST', got[1100])
        self.assertIn('excludes rubbish removal', got[1100])


if __name__ == '__main__':
    unittest.main()
