"""
Demo data — trades, customers, jobs, quotes and reviews so every screen has
something in it. Run `python3 seed.py`, or press "Load demo data" in admin.

Demo logins (password demo1234): customer@level.local and trade@level.local
"""
import random
from datetime import timedelta

import billing
import engine
from engine import RuleError, ts, utcnow
from schema import hash_password

FIRST = ['Aroha', 'Ben', 'Mere', 'Sam', 'Tama', 'Priya', 'Jack', 'Hana', 'Liam', 'Ana', 'Wiremu', 'Grace',
         'Mateo', 'Ruby', 'Nikau', 'Isla', 'Josh', 'Mia', 'Rawiri', 'Emma', 'Daniel', 'Leilani', 'Ethan', 'Kiri']
LAST = ['Walker', 'Ngata', 'Singh', 'Brown', 'Tipene', 'Chen', 'Wilson', 'Parata', 'Taylor', 'Kaur', 'Smith',
        'Henare', 'Lee', 'Thompson', 'Rangi', 'Patel', 'Martin', 'Fa’asavalu', 'Clarke', 'Moana']
WORDS = ['Harbourline', 'Summit', 'Southern Cross', 'Ironsand', 'Westgate', 'Maunga', 'Tūī', 'Kōwhai', 'Coastline',
         'Fernleaf', 'Ranges', 'Keystone', 'Greenstone', 'Northcote', 'Blockhouse', 'Brightwater', 'Totara Park',
         'Rangitoto', 'Waitākere', 'Pine Harbour', 'Bayview', 'Stonefield', 'Clearwater', 'Te Atatū', 'Oakridge',
         'Riverhead', 'Redwood', 'Longbay', 'Hillcrest', 'Glenfield', 'Parnell', 'Beachlands', 'Seaview',
         'Whenuapai', 'Kaipātiki', 'Birkdale', 'Pakuranga', 'Ōrākei', 'Onewhero', 'Kumeū', 'Albany', 'Mangere',
         'Hobsonville', 'Waiuku', 'Karaka', 'Bucklands', 'Castor Bay', 'Titirangi', 'Laingholm', 'Muriwai',
         'Piha', 'Waiheke', 'Clevedon', 'Ardmore', 'Drury', 'Ramarama', 'Pukekohe', 'Bombay', 'Mauku', 'Patumahoe']
SUFFIX = {'builder': ['Builders', 'Construction', 'Build Co', 'Carpentry'], 'electrician': ['Electrical', 'Electric'],
          'plumber': ['Plumbing', 'Plumbing & Gas'], 'painter': ['Painters', 'Decorating'],
          'landscaper': ['Landscapes', 'Gardens'], 'roofer': ['Roofing', 'Roofing Co']}
POOL = [('builder', 22), ('electrician', 10), ('plumber', 9), ('painter', 8), ('landscaper', 6), ('roofer', 6)]
AREAS = ['akl-central', 'akl-north-shore', 'akl-west', 'akl-east']
LICENCE = {'builder': 'lbp', 'roofer': 'lbp', 'electrician': 'ewrb', 'plumber': 'pgdb'}
INSURERS = ['Vero', 'NZI', 'QBE', 'Tower', 'AIG']

MESSAGES = [
    'Thanks for the detail and photos. We do plenty of these around {suburb}. The price covers labour and materials, '
    'and we’d confirm everything on a quick look before we start.',
    'Happy to take this on. Once the old material is off I’ll check what’s underneath — if there’s anything extra, '
    'I’ll show you and price it before doing any more work.',
    'We can fit this in soon. We’ll protect floors and gardens while we work and leave the site tidy at the end of each day.',
    'Keen to help. I’ve priced this from the photos; it could come in under if access is as easy as it looks.',
    'Good clear brief, thanks. I’d rather see it before giving a firm number, so I’ve put down a site visit — '
    'no charge anywhere in Auckland.',
]
INCLUDES = ['Labour and materials', 'All materials, skip bin and clean-up', 'Labour, materials and fixings to code',
            'Supply and install, rubbish removal']
EXCLUDES = ['Painting or staining', 'Anything hidden we find once work starts (priced separately)',
            'Electrical work', 'Council fees']
WARRANTY = ['12 months on workmanship', '2 years on workmanship', '5 years on workmanship, manufacturer warranty on materials']
STARTS = ['Next week', 'Within 2 weeks', 'Early next month', 'Week after next']
DURATION = {'small': ['1 day', '2 days', '2–3 days'], 'medium': ['About a week', '2 weeks', '3 weeks'],
            'large': ['8–10 weeks', 'About 3 months']}
PRICE = {'small': (450, 4800), 'medium': (6000, 44000), 'large': (65000, 240000)}

# (trade, band, area, suburb, title, description, timing, hours ago, demo customer?, quotes, then)
JOBS = [
    ('builder', 'small', 'akl-central', 'Grey Lynn', 'Replace rotten deck boards and balustrade',
     'About 18 m² of deck off the back of a 1920s villa. Roughly a third of the boards are soft and the balustrade '
     'wobbles at one end. Open to pine or hardwood — happy to take advice. Easy side access from the driveway.',
     'weeks', 30, True, 4, 'share'),
    ('builder', 'medium', 'akl-north-shore', 'Takapuna', 'Bathroom and laundry renovation',
     'Full strip-out of a 1990s bathroom (shower over bath) and the laundry next to it. Want a walk-in shower, '
     'wall-hung vanity, heated towel rail and new laundry cabinetry. Plumber and sparky will be needed too.',
     'months', 50, True, 6, None),
    ('electrician', 'small', 'akl-central', 'Mt Eden', 'Add downlights and a heat pump circuit',
     'Six LED downlights in the lounge (currently one pendant) and a dedicated circuit for a heat pump we’re '
     'having installed next month. Single-storey, good ceiling space.', 'weeks', 3, True, 1, None),
    ('plumber', 'small', 'akl-central', 'Kingsland', 'Replace leaking hot water cylinder',
     '180 L electric cylinder in the hallway cupboard is leaking from the base. It’s about 20 years old. '
     'Want a like-for-like replacement, mains pressure if possible.', 'asap', 120, True, 3, 'accept'),
    ('builder', 'small', 'akl-central', 'Ponsonby', 'Re-hang sticking French doors',
     'Pair of timber French doors onto the deck. They stick badly in wet weather and the latch no longer lines up. '
     'Might need planing and new hinges.', 'weeks', 5, False, 1, None),
    ('builder', 'medium', 'akl-central', 'Sandringham', 'Convert double garage into a sleep-out',
     'Detached double garage, concrete floor, sound roof. Want it lined and insulated, with a new window, a door '
     'where the tilt door is, and a small kitchenette. Happy to talk about whether it needs consent.',
     'months', 9, False, 2, None),
    ('builder', 'large', 'akl-central', 'Remuera', 'Single-storey extension: family room and deck',
     'Around 35 m² extension off the kitchen of a 1950s weatherboard house, with bi-fold doors onto a new deck. '
     'Architect plans are done and consent is lodged. Looking for a builder to price and project-manage.',
     'months', 14, False, 1, None),
    ('builder', 'small', 'akl-north-shore', 'Devonport', 'Replace weatherboards on one wall',
     'South-facing wall of a bungalow — about 12 m² of weatherboards have split or rotted at the bottom. '
     'Would like them replaced and primed ready for painting.', 'weeks', 2, False, 0, None),
    ('builder', 'small', 'akl-central', 'Mt Albert', 'Replace rotten subfloor bearers under laundry',
     'Floor in the laundry has gone springy. Had a look under the house and two bearers look rotten where a pipe '
     'was leaking (now fixed). About 60 cm crawl space.', 'asap', 1, False, 0, None),
    ('painter', 'medium', 'akl-west', 'Titirangi', 'Repaint weatherboard exterior of a 3-bed house',
     'Single-storey weatherboard, some flaking paint on the north side. Would like it washed, prepped and painted, '
     'including soffits and window frames. Colour stays the same.', 'months', 20, False, 3, None),
    ('landscaper', 'medium', 'akl-east', 'Howick', 'Retaining wall and new lawn on a sloping section',
     'Back yard slopes about 1 m over 10 m. Want a timber retaining wall (under 1.5 m) to create a flat lawn, '
     'plus new turf and a garden edge.', 'months', 26, False, 2, None),
    ('roofer', 'medium', 'akl-west', 'Henderson', 'Re-roof 1960s bungalow in long-run steel',
     'Existing concrete tile roof, about 140 m². Want it replaced with long-run coloured steel, new underlay and '
     'spouting. Single storey, easy access.', 'months', 8, False, 2, None),
]

REVIEWS = [
    ('Turned up when they said, did a tidy job and cleaned up after. Would use again.', (5, 5, 5, 4)),
    ('Great communication throughout. Price was fair and there were no surprises.', (5, 5, 4, 5)),
    ('Good work, took a day longer than planned because of the weather, but kept us in the loop.', (4, 5, 3, 4)),
    ('Really knew their stuff and explained the options clearly.', (5, 4, 5, 4)),
    ('Solid job. A bit hard to get hold of at times.', (4, 3, 4, 4)),
    ('Fixed a problem two other trades couldn’t. Highly recommend.', (5, 5, 5, 5)),
]


def _nzbn(rng):
    body = '94' + ''.join(str(rng.randint(0, 9)) for _ in range(10))
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def run(db):
    if db.execute("SELECT 1 FROM users WHERE email = 'trade@level.local'").fetchone():
        return 'Demo data is already loaded.'
    rng = random.Random(7)
    now = utcnow()
    pw = hash_password('demo1234')
    cats = {r['slug']: r['id'] for r in db.execute('SELECT id, slug FROM categories').fetchall()}
    areas = {r['slug']: r['id'] for r in db.execute('SELECT id, slug FROM areas').fetchall()}
    sub_start = now - timedelta(days=12)
    names = iter(rng.sample(WORDS, len(WORDS)))

    def phone():
        return f'02{rng.randint(1, 9)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}'

    def user(role, name, email, created):
        return db.execute('INSERT INTO users (role, email, password_hash, name, phone, created_at) VALUES (?,?,?,?,?,?)',
                          (role, email, pw, name, phone(), ts(created))).lastrowid

    def make_trade(cat, business=None, email=None, person=None, tier=None, cover=None, verified=None):
        business = business or f'{next(names)} {rng.choice(SUFFIX[cat])}'
        email = email or business.lower().replace(' ', '').replace('&', '').replace('’', '') + '@demo.level.local'
        person = person or f'{rng.choice(FIRST)} {rng.choice(LAST)}'
        uid = user('trade', person, email, sub_start - timedelta(days=rng.randint(5, 300)))
        tier = tier or rng.choices(['small', 'medium', 'large'], weights=[3, 5, 3])[0]
        lic = LICENCE.get(cat, 'none')
        verified = rng.random() < .7 if verified is None else verified
        checked = ts(sub_start) if verified else None
        number = {'lbp': f'BP{rng.randint(100000, 140000)}', 'ewrb': f'EW{rng.randint(100000, 299999)}',
                  'pgdb': str(rng.randint(10000, 39999))}.get(lic)
        db.execute('INSERT INTO trades (user_id, business_name, about, years_trading, nzbn, nzbn_checked_at, '
                   'licence_type, licence_number, licence_checked_at, insurance_insurer, insurance_expiry, '
                   'insurance_checked_at, workmanship_guarantee, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (uid, business, f'Owner-operated {cat} business with a small crew. We look after homes across '
                                   'Auckland and keep customers in the loop from quote to clean-up.',
                    rng.randint(2, 28), _nzbn(rng), checked, lic, number, checked if number else None,
                    rng.choice(INSURERS), f'{now.year + 1}-0{rng.randint(1, 9)}-15', checked,
                    rng.choice(WARRANTY), ts(sub_start)))
        db.execute('INSERT INTO trade_categories (trade_id, category_id) VALUES (?,?)', (uid, cats[cat]))
        for a in cover or rng.sample(AREAS, rng.randint(1, 3)):
            db.execute('INSERT INTO trade_areas (trade_id, area_id) VALUES (?,?)', (uid, areas[a]))
        db.commit()
        t = db.execute('SELECT * FROM trades WHERE user_id = ?', (uid,)).fetchone()
        billing.choose_plan(db, t, email, tier, '', '', at=sub_start + timedelta(hours=rng.randint(0, 36)))
        return uid

    # The demo trade: builder covering Central and the North Shore, on the top plan.
    demo_trade = make_trade('builder', 'Harbourline Builders', 'trade@level.local', 'Tama Ngata', 'large',
                            ['akl-central', 'akl-north-shore'], True)
    trades = {'builder': [demo_trade]}
    for cat, n in POOL:
        for i in range(n - (1 if cat == 'builder' else 0)):
            cover = None
            if cat == 'builder':   # most builders cover Central, so its jobs rotate through a real pool
                cover = ['akl-central'] + rng.sample(['akl-north-shore', 'akl-west', 'akl-east'], rng.randint(0, 2))
            trades.setdefault(cat, []).append(make_trade(cat, cover=cover))

    demo_customer = user('customer', 'Sam Walker', 'customer@level.local', now - timedelta(days=90))
    others = [user('customer', f'{rng.choice(FIRST)} {rng.choice(LAST)}', f'customer{i}@demo.level.local',
                   now - timedelta(days=rng.randint(10, 400))) for i in range(8)]
    db.commit()

    # Past, reviewed jobs so some trades have a track record (and some don't).
    reviewed = rng.sample([t for ts_ in trades.values() for t in ts_ if t != demo_trade], 14) + [demo_trade]
    for t in reviewed:
        count = 4 if t == demo_trade else rng.choice([1, 2, 3, 4, 5])
        cat = next(c for c, ids in trades.items() if t in ids)
        for k in range(count):
            created = now - timedelta(days=rng.randint(40, 300))
            body, scores = rng.choice(REVIEWS)
            cust = rng.choice(others + [demo_customer])
            jid = db.execute(
                'INSERT INTO jobs (customer_id, category_id, area_id, suburb, title, description, value_band, timing, '
                'property_type, status, quote_count, hired_trade_id, created_at, closes_at, closed_at, close_reason) '
                "VALUES (?,?,?,?,?,?,?,?,?,'hired',1,?,?,?,?,'hired')",
                (cust, cats[cat], areas['akl-central'], rng.choice(['Mt Eden', 'Epsom', 'Point Chevalier', 'Onehunga']),
                 rng.choice(['Fix leaking roof flashing', 'Replace kitchen benchtop', 'Repair storm damage',
                             'Install new vanity', 'Replace front steps', 'Rewire garage']),
                 'Completed job.', 'small', 'weeks', 'house', t, ts(created), ts(created + timedelta(days=14)),
                 ts(created + timedelta(days=2)))).lastrowid
            db.execute('INSERT INTO reviews (job_id, trade_id, customer_id, rating, workmanship, communication, '
                       'timeliness, value_for_money, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (jid, t, cust, sum(scores) / 4, *scores, body, ts(created + timedelta(days=20))))
    db.commit()

    def quote(cat, band, suburb):
        kind = rng.choice(['fixed', 'fixed', 'fixed', 'range', 'site_visit'])
        low = rng.randrange(PRICE[band][0], PRICE[band][1], 50)
        return dict(price_type=kind, amount_low=low, amount_high=int(low * rng.uniform(1.1, 1.3)) if kind == 'range' else None,
                    gst_included=rng.random() < .75, message=rng.choice(MESSAGES).format(suburb=suburb),
                    inclusions=rng.choice(INCLUDES), exclusions=rng.choice(EXCLUDES) if rng.random() < .7 else None,
                    warranty=rng.choice(WARRANTY), available_from=rng.choice(STARTS),
                    duration=rng.choice(DURATION[band]), act_docs_promised=True)

    # Live jobs, posted in the past so offers, quotes and handovers have happened.
    for cat, band, area, suburb, title, desc, timing, hours, is_demo, n_quotes, then in JOBS:
        at = now - timedelta(hours=hours)
        cust = demo_customer if is_demo else rng.choice(others)
        job_id, _ = engine.post_job(db, cust, dict(category_id=cats[cat], area_id=areas[area], suburb=suburb,
                                                   address=f'{rng.randint(2, 180)} Example Street, {suburb}',
                                                   title=title, description=desc, value_band=band, timing=timing,
                                                   property_type='house'), at=at)
        offered = [r['trade_id'] for r in db.execute('SELECT trade_id FROM offers WHERE job_id = ? ORDER BY id',
                                                     (job_id,)).fetchall()]
        # The demo trade quotes on older Central builder jobs; newer ones stay waiting for them.
        quoters = [t for t in offered if t != demo_trade]
        rng.shuffle(quoters)
        quoters = quoters[:n_quotes]
        if demo_trade in offered and hours >= 5 and band != 'large' and n_quotes:
            quoters = [demo_trade] + quoters[:n_quotes - 1]
        qat = at
        for t in quoters:
            qat += timedelta(minutes=rng.randint(35, 170))
            if qat >= now:
                break
            try:
                engine.submit_quote(db, job_id, t, quote(cat, band, suburb), at=qat)
            except RuleError:
                pass
        job = engine.get_job(db, job_id)
        first = db.execute('SELECT id, trade_id FROM quotes WHERE job_id = ? ORDER BY id LIMIT 1', (job_id,)).fetchone()
        if then == 'share' and first:
            engine.share_contact(db, job, first['id'], at=at + timedelta(hours=6))
            for who, body, h in [(cust, 'Hi — are the balustrade posts included, or just the rails?', 7),
                                 (first['trade_id'], 'Posts are included. I’d replace all four and bolt them through '
                                                     'the joists so it’s solid.', 8)]:
                db.execute('INSERT INTO messages (job_id, trade_id, sender_id, body, created_at) VALUES (?,?,?,?,?)',
                           (job_id, first['trade_id'], who, body, ts(at + timedelta(hours=h))))
            db.commit()
        elif then == 'accept' and first:
            engine.accept_quote(db, job, first['id'], at=at + timedelta(hours=20))

    engine.sweep(db, at=now)
    n_trades = sum(len(v) for v in trades.values())
    return (f'Loaded {n_trades} trades, {len(others) + 1} customers and {len(JOBS)} live jobs. '
            'Log in as customer@level.local or trade@level.local (password demo1234).')


if __name__ == '__main__':
    from db import get_db
    from schema import init_db
    init_db()
    conn = get_db()
    print(run(conn))
    conn.close()
