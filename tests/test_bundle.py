"""
Buying both, and the one thing that must never go wrong.

The offer is money, so the tests that matter aren't the ones about the discount
— they're the ones about it stopping. A tradie who cancels Level must stop being
entitled to Docket's bundled price, and they must stop the moment the Level
period runs out rather than whenever somebody remembers. Half this file is that.

The rest holds the comparison honest: it's built from published rival prices, so
it has to be able to reach the conclusion that we're *not* cheaper, or it isn't
a comparison.

Run from the project folder:  python3 -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('DATABASE', tempfile.NamedTemporaryFile(suffix='.db', delete=False).name)
os.environ['RUN_SWEEPER'] = '0'

import app as A  # noqa: E402
import billing  # noqa: E402
import bundle  # noqa: E402
import config  # noqa: E402
import db as dbmod  # noqa: E402
import integrations  # noqa: E402
from engine import ts, utcnow  # noqa: E402
from schema import hash_password, init_db  # noqa: E402

KEY = 'k' * 40


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
        self.area = self.db.execute("SELECT id FROM areas WHERE slug = 'wellington'").fetchone()['id']
        self.n = 0
        self.email = 'karori@test.nz'
        self.trade = self.a_trade('medium')

    def tearDown(self):
        integrations._cache.update(at=0, values={})
        self.db.close()
        os.unlink(self.tmp.name)

    def a_trade(self, tier='medium', email=None):
        self.n += 1
        email = email or self.email
        uid = self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) '
                              'VALUES (?,?,?,?,?)',
                              ('trade', email, hash_password('password123'), 'Karori Building',
                               ts(utcnow()))).lastrowid
        self.db.execute('INSERT INTO trades (user_id, business_name, created_at) VALUES (?,?,?)',
                        (uid, 'Karori Building', ts(utcnow())))
        self.db.execute('INSERT INTO trade_categories VALUES (?,?)', (uid, self.cat))
        self.db.execute('INSERT INTO trade_areas VALUES (?,?)', (uid, self.area))
        self.db.commit()
        t = self.db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
        billing.choose_plan(self.db, t, email, tier, '', '')
        self.db.commit()
        return uid

    def connected(self):
        """Level and Docket know about each other, so the API answers at all."""
        integrations._cache.update(at=10 ** 12, values={
            'docket_url': 'http://localhost:5094', 'docket_key': KEY})

    def asked(self, email=None, key=KEY):
        headers = {'X-Level-Key': key} if key else {}
        return A.app.test_client().get('/api/bundle', query_string={'email': email or self.email},
                                       headers=headers)


class ThePriceTest(Base):

    def test_docket_costs_the_add_on_price_not_its_list_price(self):
        p = bundle.price_for('medium')
        self.assertEqual(p['price'], config.BUNDLE_PRICE)
        self.assertGreater(p['list_price'], p['price'], 'a bundle price above list is not a bundle')
        self.assertEqual(p['saving'], p['list_price'] - p['price'])

    def test_both_together_is_the_two_prices_added_up(self):
        for tier in config.TIER_ORDER:
            p = bundle.price_for(tier)
            self.assertEqual(p['both'], config.TIERS[tier]['price'] + config.BUNDLE_PRICE,
                             f'{tier}: the total has to be the parts')

    def test_every_level_tier_has_a_docket_plan_that_fits_it(self):
        seen = []
        for tier in config.TIER_ORDER:
            plan = bundle.docket_plan_for(tier)
            self.assertIsNotNone(plan, tier)
            seen.append(plan['slug'])
        self.assertEqual(len(set(seen)), len(seen), 'a bigger Level plan should not get a smaller Docket one')

    def test_an_unknown_tier_gets_no_price_rather_than_a_guess(self):
        self.assertIsNone(bundle.price_for('enormous'))
        self.assertIsNone(bundle.docket_plan_for(None))


class TheComparisonTest(Base):

    def test_it_quotes_the_cheapest_rival_not_the_dearest(self):
        e = bundle.elsewhere()
        for job, part in e['parts'].items():
            cheapest = min(o['from'] for o in config.RIVALS[job])
            self.assertEqual(part['from'], cheapest, f'{job}: quoting anything else stacks the deck')

    def test_the_total_is_both_halves_of_their_bill(self):
        e = bundle.elsewhere()
        self.assertEqual(e['total'], sum(p['from'] for p in e['parts'].values()))
        self.assertEqual(len(e['parts']), 2, 'finding the work and running the work')

    def test_every_rival_price_comes_with_the_catch(self):
        for job, options in config.RIVALS.items():
            for o in options:
                self.assertTrue(o.get('note'), f'{o["name"]}: a starting price on its own is misleading')

    def test_it_can_say_we_are_dearer(self):
        """A comparison that can only reach one conclusion is an advert."""
        original = config.RIVALS
        try:
            config.RIVALS = {'finding work': [{'name': 'Cheap', 'from': 1, 'note': 'a dollar'}],
                             'running the work': [{'name': 'Free', 'from': 0, 'note': 'nothing'}]}
            c = bundle.compared_with('medium')
            self.assertFalse(c['cheaper'])
            self.assertLess(c['difference'], 0)
        finally:
            config.RIVALS = original

    def test_on_the_real_numbers_both_of_ours_is_cheaper(self):
        c = bundle.compared_with('medium')
        self.assertTrue(c['cheaper'], 'if this fails the page says so — but check the pricing')
        self.assertEqual(c['difference'], c['theirs']['total'] - c['ours']['both'])


class CostPerJobTest(Base):
    """A lead site's bill depends on how many jobs you chase. Ours doesn't."""

    def test_ours_does_not_move_with_how_many_jobs_you_quote_on(self):
        prices = {bundle.compared_with('medium', jobs)['ours']['both']
                  for jobs in (0, 1, 4, 8, 50)}
        self.assertEqual(len(prices), 1, 'the flat price is the product')

    def test_an_unconfirmed_lead_price_is_left_out_and_owned_up_to(self):
        """We don't know Builderscrack's token price yet. Until we do, their
        total is a floor and `incomplete` says so — inventing a figure to make
        our own case is the one thing this page must not do."""
        rival = config.RIVALS['finding work'][0]
        was = rival.get('per_lead')
        try:
            rival['per_lead'] = None
            base = bundle.elsewhere(0)['total']
            busy = bundle.elsewhere(8)
            self.assertEqual(busy['total'], base, 'nothing may be guessed at')
            self.assertTrue(busy['incomplete'], 'and the page has to know it is short')
        finally:
            rival['per_lead'] = was

    def test_a_confirmed_lead_price_is_multiplied_by_the_jobs(self):
        rival = config.RIVALS['finding work'][0]
        was = rival.get('per_lead')
        try:
            rival['per_lead'] = 25
            base = bundle.elsewhere(0)['total']
            self.assertEqual(bundle.elsewhere(4)['total'], base + 100)
            self.assertFalse(bundle.elsewhere(4)['incomplete'])
        finally:
            rival['per_lead'] = was

    def test_a_demand_priced_lead_becomes_a_range_not_a_single_number(self):
        rival = config.RIVALS['finding work'][0]
        was = rival.get('per_lead')
        try:
            rival['per_lead'] = (25, 70)
            e = bundle.elsewhere(4)
            self.assertTrue(e['a_range'])
            self.assertEqual(e['total_high'] - e['total'], (70 - 25) * 4)
        finally:
            rival['per_lead'] = was

    def test_we_are_compared_against_the_cheap_end_of_their_range(self):
        """Costing their surge pricing at its worst would flatter us."""
        rival = config.RIVALS['finding work'][0]
        was = rival.get('per_lead')
        try:
            rival['per_lead'] = (25, 70)
            c = bundle.compared_with('medium', 4)
            self.assertEqual(c['difference'], c['theirs']['total'] - c['ours']['both'])
            self.assertLess(c['theirs']['total'], c['theirs']['total_high'])
        finally:
            rival['per_lead'] = was

    def test_no_lead_price_at_all_means_quoting_adds_nothing(self):
        self.assertEqual(bundle.lead_cost({'from': 50}, 10), (0, 0))
        self.assertEqual(bundle.lead_cost({'from': 50, 'per_lead': 25}, 0), (0, 0))

    def test_there_is_a_row_for_every_volume_we_show(self):
        rows = bundle.by_volume('medium')
        self.assertEqual([r['jobs'] for r in rows], config.JOBS_A_MONTH)


class WhoGetsItTest(Base):

    def test_a_paying_trade_gets_the_offer(self):
        offer = bundle.offer_for(self.db, self.trade)
        self.assertIsNotNone(offer)
        self.assertEqual(offer['price'], config.BUNDLE_PRICE)

    def test_a_trade_with_no_plan_does_not(self):
        self.db.execute("UPDATE trades SET sub_status = 'cancelled' WHERE user_id = ?", (self.trade,))
        self.db.commit()
        self.assertIsNone(bundle.offer_for(self.db, self.trade))

    def test_a_closed_account_does_not(self):
        self.db.execute('UPDATE users SET closed_at = ? WHERE id = ?', (ts(utcnow()), self.trade))
        self.db.commit()
        self.assertIsNone(bundle.offer_for(self.db, self.trade))

    def test_the_offer_carries_the_comparison_so_the_page_need_not_work_it_out(self):
        offer = bundle.offer_for(self.db, self.trade)
        self.assertIn('comparison', offer)
        self.assertEqual(offer['comparison']['ours']['both'], offer['both'])


class WhatDocketIsToldTest(Base):
    """The money question: what price does Docket charge this person?"""

    def test_a_paying_level_trade_is_entitled(self):
        e = bundle.entitlement(self.db, self.email)
        self.assertTrue(e['entitled'])
        self.assertEqual(e['price'], config.BUNDLE_PRICE)
        self.assertEqual(e['plan'], config.BUNDLE_TIERS['medium'])

    def test_the_answer_says_how_long_it_is_good_for(self):
        e = bundle.entitlement(self.db, self.email)
        self.assertIsNotNone(e['until'], 'without this Docket would have to guess when to ask again')

    def test_it_stops_the_moment_the_level_period_runs_out(self):
        until = self.db.execute('SELECT period_end FROM trades WHERE user_id = ?',
                                (self.trade,)).fetchone()['period_end']
        from engine import parse_ts
        end = parse_ts(until)
        self.assertTrue(bundle.entitlement(self.db, self.email, end - timedelta(minutes=1))['entitled'])
        self.assertFalse(bundle.entitlement(self.db, self.email, end + timedelta(minutes=1))['entitled'],
                         'a lapsed Level plan must not keep buying a cheap Docket')

    def test_cancelling_level_ends_it_without_anyone_telling_docket(self):
        self.db.execute("UPDATE trades SET sub_status = 'cancelled' WHERE user_id = ?", (self.trade,))
        self.db.commit()
        self.assertFalse(bundle.entitlement(self.db, self.email)['entitled'])

    def test_nothing_is_stored_that_could_outlive_the_subscription(self):
        """Derived every time, never written down. A stored yes is a permanent yes."""
        bundle.entitlement(self.db, self.email)
        columns = [r[1] for r in self.db.execute('PRAGMA table_info(trades)')]
        self.assertNotIn('bundle_entitled', columns)
        self.assertFalse([c for c in columns if 'bundle' in c],
                         'the live subscription is the record; a copy of it is a bug waiting to happen')

    def test_an_address_nobody_here_uses_is_not_entitled(self):
        self.assertFalse(bundle.entitlement(self.db, 'someone@else.nz')['entitled'])
        self.assertFalse(bundle.entitlement(self.db, '')['entitled'])
        self.assertFalse(bundle.entitlement(self.db, None)['entitled'])

    def test_the_address_is_matched_however_it_was_typed(self):
        self.assertTrue(bundle.entitlement(self.db, '  KARORI@Test.NZ ')['entitled'])

    def test_a_customer_with_the_same_kind_of_address_is_not_entitled(self):
        self.db.execute('INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
                        ('customer', 'sam@test.nz', hash_password('password123'), 'Sam', ts(utcnow())))
        self.db.commit()
        self.assertFalse(bundle.entitlement(self.db, 'sam@test.nz')['entitled'])

    def test_a_lapse_is_flagged_before_it_happens_not_after(self):
        from engine import parse_ts
        end = parse_ts(self.db.execute('SELECT period_end FROM trades WHERE user_id = ?',
                                       (self.trade,)).fetchone()['period_end'])
        self.assertFalse(bundle.ending_soon(self.db, self.email, at=end - timedelta(days=20)))
        self.assertTrue(bundle.ending_soon(self.db, self.email, at=end - timedelta(days=2)))


class TheApiTest(Base):

    def test_docket_can_ask_with_the_shared_key(self):
        self.connected()
        r = self.asked()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['entitled'])

    def test_without_the_key_it_will_not_say(self):
        self.connected()
        self.assertEqual(self.asked(key=None).status_code, 403)
        self.assertEqual(self.asked(key='wrong' * 8).status_code, 403)

    def test_an_unconfigured_level_says_ask_again_rather_than_no(self):
        """503, not a cheerful "not entitled" — which Docket would bill on."""
        r = self.asked()
        self.assertEqual(r.status_code, 503)

    def test_the_answer_is_never_cached(self):
        self.connected()
        self.assertEqual(self.asked().headers.get('Cache-Control'), 'no-store')

    def test_it_says_nothing_about_the_person_beyond_the_price(self):
        self.connected()
        body = self.asked().get_json()
        self.assertEqual(set(body) - {'entitled', 'plan', 'price', 'list_price', 'until', 'because'}, set())
        self.assertNotIn('Karori', str(body), 'Docket already knows who it asked about')

    def test_an_address_with_no_level_plan_gets_a_flat_no(self):
        self.connected()
        body = self.asked(email='nobody@test.nz').get_json()
        self.assertFalse(body['entitled'])
        self.assertIsNone(body['price'])


class ThePagesTest(Base):

    def test_the_comparison_page_shows_both_totals(self):
        page = A.app.test_client().get('/and-docket').data.decode()
        self.assertEqual(A.app.test_client().get('/and-docket').status_code, 200)
        both = bundle.price_for('medium')['both']
        self.assertIn(f'${both}', page)
        self.assertIn(f"${bundle.elsewhere()['total']}", page)

    def test_it_admits_the_tier_where_we_are_not_cheaper(self):
        """On the biggest plan we're a dollar dearer than the cheapest pair. The
        page has to say so — the argument for us there is the structure of the
        bill, and hiding a dollar would cost us the right to make it."""
        page = A.app.test_client().get('/and-docket').data.decode()
        dearer = [t for t in config.TIER_ORDER if not bundle.compared_with(t)['cheaper']]
        for tier in dearer:
            gap = -bundle.compared_with(tier)['difference']
            self.assertIn(f'${gap} dearer', page, f'{tier}: shown as cheaper, or not shown at all')

    def test_it_names_the_catch_on_every_rival_price(self):
        page = A.app.test_client().get('/and-docket').data.decode()
        for options in config.RIVALS.values():
            cheapest = min(options, key=lambda o: o['from'])
            self.assertIn(cheapest['name'], page)
            self.assertIn(cheapest['note'], page)

    def test_the_pricing_page_offers_it_without_making_it_the_point(self):
        page = A.app.test_client().get('/pricing').data.decode()
        self.assertIn(f'${config.BUNDLE_PRICE}', page)
        self.assertIn('and-docket', page)

    def test_a_trade_sees_their_own_price_on_their_docket_page(self):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = self.trade
            s['_csrf'] = 't'
        page = c.get('/trade/docket').data.decode()
        self.assertIn(f'${config.BUNDLE_PRICE} a month', page)
        self.assertIn(str(config.DOCKET_PLANS[config.BUNDLE_TIERS['medium']]['price']), page)

    def test_a_trade_with_no_plan_is_not_shown_a_price_they_cannot_have(self):
        self.db.execute("UPDATE trades SET sub_status = 'cancelled' WHERE user_id = ?", (self.trade,))
        self.db.commit()
        c = A.app.test_client()
        with c.session_transaction() as s:
            s['uid'] = self.trade
            s['_csrf'] = 't'
        self.assertNotIn(f'${config.BUNDLE_PRICE} a month', c.get('/trade/docket').data.decode())


if __name__ == '__main__':
    unittest.main()
