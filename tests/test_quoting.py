"""
Tests for quote line items: the maths, the GST, and the rule that a breakdown
sets the price rather than sitting next to a different one.

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
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import integrations  # noqa: E402
import quoting  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

T0 = datetime(2026, 9, 25, 9, 0, 0)

LINES = [
    {'description': 'Labour, two builders', 'qty': 16, 'unit': 'hours', 'unit_price': 85},
    {'description': 'Decking timber', 'qty': 24, 'unit': 'lineal m', 'unit_price': 32.50},
    {'description': 'Rubbish removal', 'qty': None, 'unit': 'sum', 'unit_price': 180},
]


class Form(dict):
    """Just enough of a Flask form for parse_items."""

    def getlist(self, key):
        return self.get(key, [])


class MathsTest(unittest.TestCase):

    def test_a_line_without_a_quantity_counts_once(self):
        self.assertEqual(quoting.line_total({'unit_price': 180, 'qty': None}), 180)
        self.assertEqual(quoting.line_total({'unit_price': 85, 'qty': 16}), 1360)
        self.assertIsNone(quoting.line_total({'unit_price': None, 'qty': 4}))

    def test_totals_add_gst_on_top_when_prices_are_exclusive(self):
        t = quoting.totals(LINES, gst_included=False)
        self.assertEqual(t['subtotal'], 2320.0)              # 1360 + 780 + 180
        self.assertEqual(t['gst'], 348.0)
        self.assertEqual(t['total'], 2668.0)

    def test_totals_work_backwards_when_prices_already_include_gst(self):
        t = quoting.totals(LINES, gst_included=True)
        self.assertEqual(t['total'], 2320.0)
        self.assertAlmostEqual(t['subtotal'], 2017.39, places=2)
        self.assertAlmostEqual(t['subtotal'] + t['gst'], t['total'], places=2)

    def test_nothing_priced_means_no_total_rather_than_zero(self):
        t = quoting.totals([{'description': 'Have a look first', 'unit_price': None, 'qty': None}])
        self.assertIsNone(t['total'])
        self.assertEqual(t['unpriced'], 1)

    def test_the_headline_price_matches_how_gst_was_entered(self):
        self.assertEqual(quoting.price_from_items(LINES, gst_included=False)['amount_low'], 2320)
        self.assertEqual(quoting.price_from_items(LINES, gst_included=True)['amount_low'], 2320)
        self.assertIsNone(quoting.price_from_items([], gst_included=False))

    def test_the_printed_lines_add_up_to_the_printed_total(self):
        """Each line is rounded to the cent first, because each line is shown as money.

        1.005 would be the spreadsheet answer. It is the wrong one: the customer
        sees three lines of $0.34, and a total of $1.005 underneath them is a
        discrepancy the tradie has to explain.
        """
        thirds = [{'description': f'Line {i}', 'qty': 1, 'unit_price': 0.335} for i in range(3)]
        self.assertEqual([quoting.line_total(t) for t in thirds], [0.34, 0.34, 0.34])
        self.assertEqual(quoting.totals(thirds)['subtotal'], 1.02)

    # ── reading the form ──
    def test_blank_rows_are_dropped_not_rejected(self):
        f = Form({'item_description': ['Labour', '', '  '], 'item_qty': ['8', '', ''],
                  'item_unit': ['hours', '', ''], 'item_price': ['85', '', '']})
        items = quoting.parse_items(f)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['qty'], 8)

    def test_dollar_signs_and_commas_survive(self):
        f = Form({'item_description': ['Timber'], 'item_qty': ['1,200'], 'item_unit': [''],
                  'item_price': ['$1,450.50']})
        item = quoting.parse_items(f)[0]
        self.assertEqual(item['qty'], 1200)
        self.assertEqual(item['unit_price'], 1450.50)

    def test_rubbish_in_a_number_box_becomes_nothing_not_an_error(self):
        f = Form({'item_description': ['Labour'], 'item_qty': ['about eight'], 'item_unit': [''],
                  'item_price': ['dunno']})
        item = quoting.parse_items(f)[0]
        self.assertIsNone(item['qty'])
        self.assertIsNone(item['unit_price'])

    def test_too_many_lines_is_refused_with_a_useful_message(self):
        f = Form({'item_description': [f'Line {i}' for i in range(quoting.MAX_ITEMS + 1)]})
        with self.assertRaises(quoting.QuoteError) as e:
            quoting.parse_items(f)
        self.assertIn('small stuff', str(e.exception))

    def test_a_summary_names_the_first_few_lines(self):
        self.assertIn('3 lines', quoting.summarise(LINES))
        self.assertIsNone(quoting.summarise([]))

    def test_template_items_never_blow_up_on_bad_json(self):
        self.assertEqual(quoting.template_items({'items': 'not json'}), [])
        self.assertEqual(quoting.template_items({'items': None}), [])
        self.assertEqual(quoting.template_items({}), [])
        self.assertEqual(len(quoting.template_items({'items': quoting.dump_items(LINES)})), 3)


class EndToEndTest(unittest.TestCase):

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

    def client(self, uid):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = uid
            s['_csrf'] = 't'
        return c

    def post_job(self, customer):
        """Posted now, not at a fixed date.

        These tests quote through the HTTP route, which has no way to be told
        what time it is and uses the real clock. A job pinned to T0 therefore
        passed on the day this was written and started failing four hours later
        — the offer window had closed. Anything that goes through a route has to
        be anchored to now.
        """
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb='Karori', title='Replace rotten deck boards',
            description='Twelve square metres, boards are soft.', value_band='medium',
            timing='weeks', property_type='house'), at=utcnow())[0]

    def quote_form(self, **over):
        f = {'price_type': 'fixed', 'amount_low': '999', 'gst': 'excl', '_csrf': 't',
             'message': 'Two days on site, starting with the worst boards.',
             'item_description': ['Labour, two builders', 'Decking timber', 'Rubbish removal'],
             'item_qty': ['16', '24', ''], 'item_unit': ['hours', 'lineal m', 'sum'],
             'item_price': ['85', '32.50', '180']}
        f.update(over)
        return f

    def test_the_breakdown_sets_the_price_and_is_stored(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        r = self.client(trade).post(f'/trade/jobs/{job_id}/quote', data=self.quote_form())
        self.assertEqual(r.status_code, 302, r.data[:400])
        q = self.db.execute('SELECT * FROM quotes WHERE job_id = ?', (job_id,)).fetchone()
        self.assertEqual(q['amount_low'], 2320, 'the typed 999 must be ignored in favour of the lines')
        rows = quoting.items_for(self.db, q['id'])
        self.assertEqual([r['description'] for r in rows],
                         ['Labour, two builders', 'Decking timber', 'Rubbish removal'])
        self.assertEqual(rows[0]['total'], 1360)

    def test_a_quote_with_no_lines_works_exactly_as_before(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        form = self.quote_form(item_description=[], item_qty=[], item_unit=[], item_price=[],
                               amount_low='6400', gst='incl')
        self.client(trade).post(f'/trade/jobs/{job_id}/quote', data=form)
        q = self.db.execute('SELECT * FROM quotes WHERE job_id = ?', (job_id,)).fetchone()
        self.assertEqual(q['amount_low'], 6400)
        self.assertEqual(quoting.items_for(self.db, q['id']), [])

    def test_lines_with_no_prices_are_refused_rather_than_quietly_dropped(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        form = self.quote_form(item_price=['', '', ''])
        r = self.client(trade).post(f'/trade/jobs/{job_id}/quote', data=form)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'at least one line', r.data)
        self.assertIsNone(self.db.execute('SELECT id FROM quotes WHERE job_id = ?', (job_id,)).fetchone())

    def test_the_customer_sees_the_breakdown(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        self.client(trade).post(f'/trade/jobs/{job_id}/quote', data=self.quote_form())
        page = self.client(customer).get(f'/me/jobs/{job_id}').data.decode()
        self.assertIn('Where the money goes', page)
        self.assertIn('Decking timber', page)
        self.assertIn('1360.00', page)

    def test_revising_replaces_the_lines_and_the_price(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        c = self.client(trade)
        c.post(f'/trade/jobs/{job_id}/quote', data=self.quote_form())
        c.post(f'/trade/jobs/{job_id}/quote/edit',
               data=self.quote_form(item_description=['Labour'], item_qty=['10'],
                                    item_unit=['hours'], item_price=['90']))
        q = self.db.execute('SELECT * FROM quotes WHERE job_id = ?', (job_id,)).fetchone()
        self.assertEqual(q['amount_low'], 900)
        self.assertEqual(len(quoting.items_for(self.db, q['id'])), 1)

    def test_a_template_remembers_the_lines(self):
        customer, trade = self.user(), self.user('trade')
        job_id = self.post_job(customer)
        self.client(trade).post(f'/trade/jobs/{job_id}/quote',
                                data=self.quote_form(save_template='1', template_name='Deck repair'))
        tpl = self.db.execute('SELECT * FROM quote_templates WHERE trade_id = ?', (trade,)).fetchone()
        self.assertEqual(len(quoting.template_items(tpl)), 3)


if __name__ == '__main__':
    unittest.main()
