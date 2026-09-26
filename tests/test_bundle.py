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


class HowTheBillsDifferTest(Base):
    """Two bills the same size can be nothing alike. That's the real argument,
    and it only works if every line of it is something the rival says itself."""

    def test_their_total_is_flagged_as_a_floor_not_an_answer(self):
        """Builderscrack meters won jobs and publishes no rate, so any total we
        print for them is the least they could cost, never what they will."""
        self.assertTrue(bundle.elsewhere()['incomplete'])
        self.assertTrue(bundle.compared_with('medium')['floor_only'])

    def test_nothing_metered_means_nothing_to_flag(self):
        original = config.RIVALS
        try:
            config.RIVALS = {'finding work': [{'name': 'Flat', 'from': 60, 'note': 'all in'}],
                             'running the work': [{'name': 'Also flat', 'from': 40, 'note': 'all in'}]}
            self.assertFalse(bundle.elsewhere()['incomplete'])
            self.assertFalse(bundle.compared_with('medium')['floor_only'])
        finally:
            config.RIVALS = original

    def test_every_difference_has_all_three_columns_filled(self):
        rows = bundle.differences('medium')
        self.assertTrue(rows)
        for what, ours, theirs in rows:
            self.assertTrue(what.strip())
            self.assertTrue(ours.strip(), what)
            self.assertTrue(theirs.strip(), what)

    def test_the_commitment_is_one_of_them(self):
        """We're month to month and they aren't. It's the thing most likely to
        catch somebody out, so it gets a row rather than a footnote."""
        rows = dict((w, (a, b)) for w, a, b in bundle.differences('medium'))
        term = next(v for k, v in rows.items() if 'tied in' in k)
        self.assertIn('Month to month', term[0])
        self.assertIn('6 months', term[1])

    def test_what_a_won_job_costs_is_one_of_them(self):
        rows = dict((w, (a, b)) for w, a, b in bundle.differences('medium'))
        won = next(v for k, v in rows.items() if 'won job' in k)
        self.assertEqual(won[0], 'Nothing.')
        self.assertIn('not published', won[1])

    def test_it_does_not_put_per_seat_pricing_in_a_rival_that_has_none(self):
        """ServiceM8 is the cheapest job system and does NOT charge per user — it
        caps jobs. Describing that row as per-person would be quoting Fergus's
        pricing against ServiceM8's name."""
        rows = dict((w, (a, b)) for w, a, b in bundle.differences('medium'))
        grow = next(v for k, v in rows.items() if 'grow' in k)
        cheapest = min(config.RIVALS['running the work'], key=lambda o: o['from'])
        self.assertEqual(grow[1].lower().rstrip('.'), cheapest['note'].lower().rstrip('.'))

    def test_an_unknown_tier_gets_no_differences_rather_than_half_a_table(self):
        self.assertEqual(bundle.differences('enormous'), [])


class NotClaimingMoreThanWeKnowTest(Base):
    """This page is about competitors' money. Every number on it has to be
    traceable, and the ones that aren't must be absent, not softened."""

    def test_the_page_dates_the_prices_it_cannot_currently_verify(self):
        page = A.app.test_client().get('/and-docket').data.decode()
        self.assertIn(config.PRICES_AS_AT, page)
        self.assertIn('Internet Archive', page)

    def test_it_says_their_price_is_no_longer_public(self):
        page = A.app.test_client().get('/and-docket').data.decode()
        self.assertIn('redirects', page)

    def test_it_does_not_claim_quoting_costs_them_money(self):
        """It doesn't. Tokens come off when the homeowner accepts, not when you
        quote — getting this backwards would be the page's worst error, because
        it's the one a tradie who has used Builderscrack would spot instantly."""
        page = A.app.test_client().get('/and-docket').data.decode().lower()
        for wrong in ('credits for each job you want to quote',
                      'every job you chase costs',
                      'pay for each job you quote'):
            self.assertNotIn(wrong, page, 'quoting is free on Builderscrack')

    def test_no_invented_cost_per_connection_appears(self):
        """A "$250 a lead" figure was on this page and could not be sourced to
        anything first-party. Nothing like it comes back without a citation."""
        page = A.app.test_client().get('/and-docket').data.decode()
        self.assertNotIn('$250', page)
        self.assertNotIn('per lead', page.lower())

    def test_it_concedes_the_quote_cap_is_not_a_difference(self):
        """They cap a job at three connected tradies too. Selling our cap as a
        point of difference would be the sort of thing that loses trust."""
        page = A.app.test_client().get('/and-docket').data.decode()
        self.assertIn('isn’t a difference between us', page)


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


class WorkForDocketsTabTest(Base):
    """What Level hands Docket for its Find Work tab.

    The rule that matters is what is NOT in it. The platform key is a shared
    secret between two systems, not this tradie's own login, so a customer's
    name, address and words stay on Level. Docket is being given a reason to
    click through, not a copy of the job.
    """

    def a_job(self, suburb='Karori', title='Rebuild the back steps'):
        customer = self.db.execute(
            'INSERT INTO users (role, email, password_hash, name, created_at) VALUES (?,?,?,?,?)',
            ('customer', f'c{self.n}@test.nz', hash_password('password123'),
             'Mrs Wilberforce', ts(utcnow()))).lastrowid
        self.n += 1
        self.db.commit()
        import engine
        return engine.post_job(self.db, customer, dict(
            category_id=self.cat, area_id=self.area, suburb=suburb, title=title,
            description='Four treads, treated pine, handrail one side, side gate is padlocked.',
            value_band='medium', timing='weeks', property_type='house'), at=utcnow())[0]

    def test_it_lists_the_work_waiting(self):
        self.a_job()
        self.a_job(suburb='Brooklyn', title='Reline a bedroom ceiling')
        w = bundle.work_waiting(self.db, self.email)
        self.assertTrue(w['on_level'])
        self.assertEqual(w['waiting'], 2)
        self.assertEqual({j['suburb'] for j in w['jobs']}, {'Karori', 'Brooklyn'})

    def test_it_hands_over_nothing_about_the_customer(self):
        self.a_job()
        w = bundle.work_waiting(self.db, self.email)
        blob = repr(w)
        self.assertNotIn('Wilberforce', blob, 'the customer’s name is not Docket’s to hold')
        self.assertNotIn('padlocked', blob, 'nor the description')
        self.assertNotIn('@test.nz', blob, 'nor any address')
        for job in w['jobs']:
            self.assertEqual(set(job), {'title', 'suburb', 'area', 'trade', 'size', 'closes'})

    def test_it_says_when_the_soonest_one_closes(self):
        self.a_job()
        w = bundle.work_waiting(self.db, self.email)
        self.assertEqual(w['deadline'], min(j['closes'] for j in w['jobs']))

    def test_an_expired_offer_is_not_offered_up(self):
        self.a_job()
        self.db.execute("UPDATE offers SET expires_at = ? WHERE trade_id = ?",
                        (ts(utcnow() - timedelta(hours=1)), self.trade))
        self.db.commit()
        self.assertEqual(bundle.work_waiting(self.db, self.email)['waiting'], 0)

    def test_it_is_capped_so_one_tab_cannot_pull_the_whole_board(self):
        for i in range(9):
            self.a_job(title=f'Job {i}')
        w = bundle.work_waiting(self.db, self.email, limit=4)
        self.assertEqual(len(w['jobs']), 4)
        self.assertEqual(w['waiting'], 4)

    def test_somebody_who_is_not_on_level_gets_a_flat_no(self):
        w = bundle.work_waiting(self.db, 'nobody@test.nz')
        self.assertFalse(w['on_level'])
        self.assertEqual(w['jobs'], [])

    def test_a_closed_account_gets_a_flat_no(self):
        self.db.execute('UPDATE users SET closed_at = ? WHERE id = ?', (ts(utcnow()), self.trade))
        self.db.commit()
        self.assertFalse(bundle.work_waiting(self.db, self.email)['on_level'])

    def test_a_paused_or_unpaid_account_says_which(self):
        """The tab explains why it's quiet instead of looking broken."""
        self.db.execute('UPDATE trades SET paused = 1 WHERE user_id = ?', (self.trade,))
        self.db.commit()
        self.assertTrue(bundle.work_waiting(self.db, self.email)['paused'])
        self.db.execute("UPDATE trades SET sub_status = 'cancelled' WHERE user_id = ?", (self.trade,))
        self.db.commit()
        self.assertFalse(bundle.work_waiting(self.db, self.email)['subscribed'])

    def test_the_endpoint_needs_the_shared_key(self):
        self.connected()
        c = A.app.test_client()
        self.assertEqual(c.get('/api/work', query_string={'email': self.email}).status_code, 403)
        r = c.get('/api/work', query_string={'email': self.email}, headers={'X-Level-Key': KEY})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['on_level'])

    def test_an_unconfigured_level_says_ask_again_later(self):
        self.assertEqual(A.app.test_client().get(
            '/api/work', query_string={'email': self.email},
            headers={'X-Level-Key': KEY}).status_code, 503)

    def test_the_answer_is_never_cached_by_anything_in_between(self):
        self.connected()
        r = A.app.test_client().get('/api/work', query_string={'email': self.email},
                                    headers={'X-Level-Key': KEY})
        self.assertEqual(r.headers.get('Cache-Control'), 'no-store')


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
