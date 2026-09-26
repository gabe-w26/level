"""
Level and Docket as one thing to buy.

The two products do the two halves of a trade business, and neither of them is
the other: Level is where the work comes from, Docket is where the work gets
run. A tradie who wants both currently buys them the way everyone else does —
a lead site and a job management system, separately, from two companies. So the
offer is simply that buying both from us costs less than buying the two parts:
Docket at a flat add-on price instead of its own list price, on the Docket plan
that matches the Level plan they already pay for.

Two rules hold this together, and both matter more than the discount:

  · **The entitlement is worked out fresh every time it's asked for, never
    stored as a yes.** Docket asks Level "what should I charge this person?"
    and Level answers from the live subscription. Cancel Level and the next
    answer is no. A stored flag would quietly give away Docket forever to
    anyone who paid for Level once.

  · **Neither product needs the other to work.** No entitlement, no answer
    from Level, Level down entirely — Docket charges its normal price and
    carries on. This module never decides anything Docket can't decide alone.

How Docket knows who's asking: the email address, checked against the shared
platform key, exactly like the job sync (docket.py) — a tradie shouldn't have
to paste a code between two products that are already talking to each other.
The tradeoff is that anything holding the platform key can ask whether an
address is on Level, so the answer carries the minimum to price an account and
nothing else about the person.
"""
import config
from engine import is_subscribed, parse_ts, ts, utcnow


def docket_plan_for(tier):
    """The Docket plan that goes with a Level tier.

    Matched on scale rather than sold as an upsell: somebody on Level's $5k plan
    is a one or two-person outfit, and a plan for fifteen people would be a plan
    they'd never fill.
    """
    slug = config.BUNDLE_TIERS.get(tier)
    if not slug:
        return None
    plan = dict(config.DOCKET_PLANS[slug])
    plan['slug'] = slug
    return plan


def price_for(tier):
    """What Docket costs on that Level tier, what it costs on its own, and the gap.

    Returns None for a tier we don't recognise rather than guessing at a price.
    """
    plan = docket_plan_for(tier)
    if not plan:
        return None
    return {
        'plan': plan['slug'],
        'plan_name': plan['name'],
        'covers': plan['covers'],
        'price': config.BUNDLE_PRICE,
        'list_price': plan['price'],
        'saving': plan['price'] - config.BUNDLE_PRICE,
        'level_price': config.TIERS[tier]['price'],
        'both': config.TIERS[tier]['price'] + config.BUNDLE_PRICE,
    }


def elsewhere():
    """The cheapest way to buy the same two things from anybody else.

    Deliberately the *cheapest* rival for each half rather than the dearest, and
    the catches come along with it: a monthly price that turns into per-seat
    billing, a six-month commitment, or a metered cost nobody publishes is the
    thing worth knowing, and quoting only the headline would be the same trick
    played in our favour.

    `total` is therefore a floor, not an answer, and `incomplete` says so
    whenever some part of their bill exists that we can't put a number on.
    """
    out, total = {}, 0
    for job, options in config.RIVALS.items():
        cheapest = min(options, key=lambda o: o['from'])
        out[job] = dict(cheapest, others=[o for o in options if o is not cheapest],
                        # A subscription they could skip isn't an entry price.
                        optional_sub=cheapest.get('sub_needed') is False)
        total += cheapest['from']
    return {
        'parts': out,
        'total': total,
        'incomplete': any(p.get('metered') for p in out.values()),
        'as_at': config.PRICES_AS_AT,
    }


def compared_with(tier):
    """Both of ours against the cheapest of theirs, for one Level tier.

    Only ever returns a difference the numbers actually support. If we're not
    cheaper — which a price change on either side could do at any time — it says
    so rather than dressing it up, because a comparison table that can only ever
    reach one conclusion is an advert.
    """
    ours = price_for(tier)
    if not ours:
        return None
    theirs = elsewhere()
    return {
        'ours': ours,
        'theirs': theirs,
        'difference': theirs['total'] - ours['both'],
        'cheaper': ours['both'] < theirs['total'],
        # True when their bill has a part we can't price, so the difference above
        # is the *most* favourable reading for them and still shouldn't be sold
        # as the whole story.
        'floor_only': theirs['incomplete'],
    }


def differences(tier):
    """How the two bills are shaped, which is where the real gap is.

    Money aside, these are the things a tradie finds out after signing up rather
    than before. Each row is (what, ours, theirs) and every `theirs` is something
    a rival publishes about itself — no row here is an inference.
    """
    ours = price_for(tier)
    if not ours:
        return []
    finding = min(config.RIVALS['finding work'], key=lambda o: o['from'])
    running = min(config.RIVALS['running the work'], key=lambda o: o['from'])
    return [
        ('What it costs a month',
         f"${ours['both']}, flat",
         f"${finding['from']} + ${running['from']} at the lowest published plans"),
        ('How long you’re tied in',
         'Month to month. Stop whenever.',
         finding.get('term') or 'Month to month'),
        ('What a won job costs on top',
         'Nothing.',
         finding.get('metered') or 'Nothing'),
        # Not "what an extra person costs": the cheapest job system here doesn't
        # charge per person at all, it caps jobs instead. Framing it as per-seat
        # would be putting Fergus's pricing in ServiceM8's mouth.
        ('What it costs as you grow',
         'Nothing — one price, any number of people, any number of jobs.',
         running['note'].capitalize()),
        ('Whether you can see the price before you sign up',
         'On our pricing page, at the top of this one.',
         'Their pricing page redirects anyone not signed up to a page with no prices on it.'
         if finding.get('as_at') else 'Published openly'),
    ]


# ── What a tradie sees, and what Docket is told ───────────────────────────────


def offer_for(db, trade_id, at=None):
    """The offer to put in front of this tradie, or None if there isn't one.

    There's no offer for somebody who isn't paying for Level — the whole basis
    of the price is that they already are — and none for somebody already
    sending jobs to a Docket they pay for directly, because we'd be offering
    them a discount we have no way to apply to a bill we don't hold.
    """
    t = db.execute('SELECT t.*, u.email, u.closed_at FROM trades t JOIN users u ON u.id = t.user_id '
                   'WHERE t.user_id = ?', (trade_id,)).fetchone()
    if not t or t['closed_at'] or not is_subscribed(t, at):
        return None
    offer = price_for(t['tier'])
    if not offer:
        return None
    offer['comparison'] = compared_with(t['tier'])
    offer['already_connected'] = bool(t['docket_url'])
    return offer


def entitlement(db, email, at=None):
    """What Docket should charge the holder of this email address.

    The only answer Docket needs, and the whole of it. `until` is the end of the
    Level period, so Docket may hold the answer that long and must ask again
    after — which is what makes a cancelled Level plan stop the discount without
    Level having to tell anybody anything.
    """
    at = at or utcnow()
    blank = {'entitled': False, 'price': None, 'plan': None, 'until': None}
    if not (email or '').strip():
        return blank
    t = db.execute('SELECT t.*, u.closed_at FROM trades t JOIN users u ON u.id = t.user_id '
                   'WHERE lower(u.email) = lower(?)', (email.strip(),)).fetchone()
    if not t or t['closed_at'] or not is_subscribed(t, at):
        return blank
    priced = price_for(t['tier'])
    if not priced:
        return blank
    return {
        'entitled': True,
        'plan': priced['plan'],
        'price': priced['price'],
        'list_price': priced['list_price'],
        'until': t['period_end'],
        # Named so Docket can say why the price is what it is, without Docket
        # needing to know anything about how Level's plans work.
        'because': f"On Level’s {config.TIERS[t['tier']]['name'].lower()} plan",
    }


def work_waiting(db, email, at=None, limit=6):
    """What work is waiting for the holder of this address, for Docket's Level tab.

    Deliberately thin. Docket is showing a tradie a reason to click through to
    Level, not reproducing Level inside itself, so this carries only what's needed
    to decide whether to go and look: the trade, the suburb, the rough size, and
    how long is left. No customer name, no street address, no contact details, no
    description — none of that is Docket's to hold, and the platform key that
    opens this endpoint is a shared secret rather than this tradie's own login.

    `deadline` is the soonest offer expiry, which is the only urgent thing here.
    """
    at = at or utcnow()
    blank = {'on_level': False, 'waiting': 0, 'quoted': 0, 'jobs': [], 'deadline': None}
    t = db.execute('SELECT t.*, u.closed_at FROM trades t JOIN users u ON u.id = t.user_id '
                   'WHERE lower(u.email) = lower(?)', ((email or '').strip(),)).fetchone()
    if not t or t['closed_at']:
        return blank
    rows = db.execute(
        'SELECT j.title, j.suburb, j.value_band, o.expires_at, c.name AS category_name, '
        'a.name AS area_name FROM offers o JOIN jobs j ON j.id = o.job_id '
        'JOIN categories c ON c.id = j.category_id JOIN areas a ON a.id = j.area_id '
        "WHERE o.trade_id = ? AND o.status = 'active' AND o.expires_at > ? "
        'ORDER BY o.expires_at LIMIT ?', (t['user_id'], ts(at), limit)).fetchall()
    quoted = db.execute(
        "SELECT COUNT(*) AS n FROM quotes q JOIN jobs j ON j.id = q.job_id "
        "WHERE q.trade_id = ? AND j.status = 'open'", (t['user_id'],)).fetchone()['n'] or 0
    return {
        'on_level': True,
        'subscribed': is_subscribed(t, at),
        'paused': bool(t['paused']),
        'waiting': len(rows),
        'quoted': quoted,
        'deadline': rows[0]['expires_at'] if rows else None,
        'jobs': [{'title': r['title'], 'suburb': r['suburb'], 'area': r['area_name'],
                  'trade': r['category_name'], 'size': config.VALUE_BANDS[r['value_band']]['short'],
                  'closes': r['expires_at']} for r in rows],
    }


def ending_soon(db, email, days=7, at=None):
    """Is the Level plan behind this entitlement about to lapse?

    Docket can use this to warn somebody before their price changes rather than
    after, which is the difference between a heads-up and a surprise invoice.
    """
    at = at or utcnow()
    e = entitlement(db, email, at)
    if not e['entitled'] or not e['until']:
        return False
    return (parse_ts(e['until']) - at).total_seconds() <= days * 86400
