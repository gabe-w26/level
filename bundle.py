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
from engine import is_subscribed, parse_ts, utcnow


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


def lead_cost(rival, jobs):
    """What quoting on `jobs` jobs adds to this rival's bill, as (low, high).

    (0, 0) when quoting is included in the subscription — or when we haven't
    confirmed the per-lead price, which is deliberately indistinguishable here.
    An unconfirmed cost is left out of the arithmetic and named on the page
    instead; inventing a number to make our own case is the one thing a price
    comparison must not do.
    """
    per = rival.get('per_lead')
    if not per or jobs <= 0:
        return (0, 0)
    low, high = (per, per) if isinstance(per, (int, float)) else (per[0], per[-1])
    return (round(low * jobs), round(high * jobs))


def elsewhere(jobs=0):
    """The cheapest way to buy the same two things from anybody else.

    Deliberately the *cheapest* rival for each half rather than the dearest, and
    the notes come along with it: a starting price that turns into per-user
    billing or per-lead credits is the thing worth knowing, and quoting only the
    headline number would be the same trick played in our favour.

    `jobs` is how many jobs a month the tradie wants to quote on, which is what
    actually decides a lead site's bill. At 0 this is the subscriptions alone —
    the number everybody advertises, and nobody pays.
    """
    out, low, high = {}, 0, 0
    for job, options in config.RIVALS.items():
        cheapest = min(options, key=lambda o: o['from'])
        leads = lead_cost(cheapest, jobs)
        out[job] = dict(cheapest,
                        others=[o for o in options if o is not cheapest],
                        leads_low=leads[0], leads_high=leads[1],
                        total_low=cheapest['from'] + leads[0],
                        total_high=cheapest['from'] + leads[1],
                        per_lead_known=bool(cheapest.get('per_lead')),
                        # A subscription they could skip isn't an entry price.
                        optional_sub=cheapest.get('sub_needed') is False)
        low += out[job]['total_low']
        high += out[job]['total_high']
    return {
        'parts': out, 'jobs': jobs,
        'total': low, 'total_high': high,
        'a_range': high > low,
        # True when some part of their bill exists but we haven't pinned it down,
        # so every total here is a floor rather than an answer.
        'incomplete': any(p.get('per_lead') is None and 'per_lead' in p
                          for p in out.values()) and jobs > 0,
    }


def compared_with(tier, jobs=0):
    """Both of ours against the cheapest of theirs, for one Level tier.

    Only ever returns a difference the numbers actually support. If we're not
    cheaper — which a price change on either side could do at any time — it says
    so rather than dressing it up, because a comparison table that can only ever
    reach one conclusion is an advert.

    Compared at the *low* end of their range, so a demand-priced lead is costed
    at its cheapest. Their bill is the one with a range in it; ours doesn't move
    with how many jobs you quote on, which is the whole argument.
    """
    ours = price_for(tier)
    if not ours:
        return None
    theirs = elsewhere(jobs)
    return {
        'ours': ours,
        'theirs': theirs,
        'jobs': jobs,
        'difference': theirs['total'] - ours['both'],
        'cheaper': ours['both'] < theirs['total'],
    }


def by_volume(tier):
    """The same comparison at each of config.JOBS_A_MONTH, for a table."""
    return [compared_with(tier, jobs) for jobs in config.JOBS_A_MONTH]


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
