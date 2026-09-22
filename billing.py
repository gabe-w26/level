"""
Subscriptions and refunds.

Two modes, chosen automatically:
  • Live  — Stripe Checkout + webhooks, when STRIPE_SECRET_KEY and all three
            STRIPE_PRICE_* ids are set. Use test-mode keys until launch.
  • Demo  — no Stripe. Picking a plan activates it at once for a 30-day month
            and renewals happen in the sweep, so the whole product runs locally.

Either way each paid month becomes a row in `payments`; the no-job guarantee
is settled against those rows (engine.evaluate_guarantees).
"""
from datetime import datetime, timedelta

import config
import referrals
from engine import RuleError, notify, parse_ts, ts, utcnow

if config.STRIPE_SECRET_KEY:
    import stripe
    stripe.api_key = config.STRIPE_SECRET_KEY


def price_cents(tier):
    return config.TIERS[tier]['price'] * 100


def _record_payment(db, trade_id, tier, amount_cents, start, end, status, invoice_id=None, at=None):
    db.execute('INSERT OR IGNORE INTO payments (trade_id, tier, amount_cents, period_start, period_end, status, '
               'stripe_invoice_id, created_at) VALUES (?,?,?,?,?,?,?,?)',
               (trade_id, tier, amount_cents, start, end, status, invoice_id, ts(at or utcnow())))


# ── Choosing, changing and cancelling a plan ──────────────────────────────────

def choose_plan(db, trade, email, tier, success_url, cancel_url, at=None):
    """Start or change a subscription. Returns a URL to redirect to (Stripe
    Checkout) or None when the change is already done."""
    if tier not in config.TIERS:
        raise RuleError('Pick one of the three plans.')
    at = at or utcnow()
    active = trade['sub_status'] == 'active' and trade['period_end'] and parse_ts(trade['period_end']) > at

    if config.CHARGING:
        if active and trade['stripe_subscription_id']:
            sub = stripe.Subscription.retrieve(trade['stripe_subscription_id'])
            stripe.Subscription.modify(
                sub['id'], items=[{'id': sub['items']['data'][0]['id'], 'price': config.STRIPE_PRICES[tier]}],
                proration_behavior='create_prorations', cancel_at_period_end=False)
            db.execute('UPDATE trades SET tier = ?, cancel_at_period_end = 0 WHERE user_id = ?', (tier, trade['user_id']))
            db.commit()
            return None
        params = dict(
            mode='subscription',
            line_items=[{'price': config.STRIPE_PRICES[tier], 'quantity': 1}],
            client_reference_id=str(trade['user_id']),
            metadata={'trade_id': str(trade['user_id']), 'tier': tier},
            subscription_data={'metadata': {'trade_id': str(trade['user_id']), 'tier': tier}},
            success_url=success_url, cancel_url=cancel_url)
        if trade['stripe_customer_id']:
            params['customer'] = trade['stripe_customer_id']
        else:
            params['customer_email'] = email
        return stripe.checkout.Session.create(**params)['url']

    # Free pilot, or demo billing: the plan simply switches on.
    kind = 'free' if config.FREE_PILOT else 'demo'
    amount = 0 if config.FREE_PILOT else price_cents(tier)
    if active:
        db.execute('UPDATE trades SET tier = ?, cancel_at_period_end = 0 WHERE user_id = ?', (tier, trade['user_id']))
    else:
        start, end = ts(at), ts(at + timedelta(days=config.DEMO_PERIOD_DAYS))
        db.execute("UPDATE trades SET tier = ?, sub_status = 'active', cancel_at_period_end = 0, "
                   'period_start = ?, period_end = ? WHERE user_id = ?', (tier, start, end, trade['user_id']))
        _record_payment(db, trade['user_id'], tier, amount, start, end, kind, at=at)
    db.commit()
    return None


def cancel(db, trade):
    """Cancel at the end of the paid month — one click, no call needed."""
    if config.BILLING_LIVE and trade['stripe_subscription_id']:
        stripe.Subscription.modify(trade['stripe_subscription_id'], cancel_at_period_end=True)
    db.execute('UPDATE trades SET cancel_at_period_end = 1 WHERE user_id = ?', (trade['user_id'],))
    db.commit()


def undo_cancel(db, trade):
    if config.BILLING_LIVE and trade['stripe_subscription_id']:
        stripe.Subscription.modify(trade['stripe_subscription_id'], cancel_at_period_end=False)
    db.execute('UPDATE trades SET cancel_at_period_end = 0 WHERE user_id = ?', (trade['user_id'],))
    db.commit()


def roll_demo_periods(db, at):
    """Demo mode only: renew (or end) subscriptions whose month has finished.
    In live mode Stripe renews and tells us via the invoice.paid webhook."""
    if config.CHARGING:
        return 0
    renewed = 0
    for t in db.execute("SELECT * FROM trades WHERE sub_status = 'active' AND period_end <= ?", (ts(at),)).fetchall():
        if t['cancel_at_period_end']:
            db.execute("UPDATE trades SET sub_status = 'cancelled', cancel_at_period_end = 0 WHERE user_id = ?",
                       (t['user_id'],))
            continue
        start = parse_ts(t['period_end'])
        while True:
            end = start + timedelta(days=config.DEMO_PERIOD_DAYS)
            if config.FREE_PILOT:
                kind, amount = 'free', 0
            elif referrals.use_demo_month(db, t['user_id'], at):
                kind, amount = 'referral', 0           # a banked free month from a referral
            else:
                kind, amount = 'demo', price_cents(t['tier'])
            _record_payment(db, t['user_id'], t['tier'], amount, ts(start), ts(end), kind, at=at)
            if end > at:
                break
            start = end
        db.execute('UPDATE trades SET period_start = ?, period_end = ? WHERE user_id = ?',
                   (ts(start), ts(end), t['user_id']))
        renewed += 1
    return renewed


# ── Refunds ───────────────────────────────────────────────────────────────────

def _invoice_payment_intent(invoice_id):
    inv = stripe.Invoice.retrieve(invoice_id)
    pi = inv.get('payment_intent')                  # API versions before 2025-03-31
    if pi:
        return pi if isinstance(pi, str) else pi['id']
    for p in stripe.InvoicePayment.list(invoice=invoice_id).auto_paging_iter():   # newer API versions
        payment = p.get('payment') or {}
        if payment.get('payment_intent'):
            return payment['payment_intent']
    raise RuntimeError(f'No payment found for invoice {invoice_id}')


def refund_claim(db, claim_id, at=None):
    at = at or utcnow()
    claim = db.execute('SELECT * FROM guarantee_claims WHERE id = ?', (claim_id,)).fetchone()
    if not claim or claim['status'] not in ('pending', 'approved'):
        raise RuleError('That claim has already been settled.')
    payment = db.execute('SELECT * FROM payments WHERE id = ?', (claim['payment_id'],)).fetchone()
    refund_id = 'demo'
    if config.BILLING_LIVE and payment['stripe_invoice_id']:
        refund = stripe.Refund.create(payment_intent=_invoice_payment_intent(payment['stripe_invoice_id']),
                                      amount=claim['amount_cents'], reason='requested_by_customer',
                                      metadata={'claim_id': str(claim_id)})
        refund_id = refund['id']
    db.execute("UPDATE guarantee_claims SET status = 'refunded', stripe_refund_id = ?, decided_at = ? WHERE id = ?",
               (refund_id, ts(at), claim_id))
    notify(db, claim['trade_id'], f'Refunded: ${claim["amount_cents"] / 100:.2f} for the month ending '
                                  f'{claim["period_end"][:10]}. It takes 5–10 days to reach your card.', '/trade/plan', at)
    db.commit()


def deny_claim(db, claim_id, note, at=None):
    at = at or utcnow()
    claim = db.execute('SELECT * FROM guarantee_claims WHERE id = ?', (claim_id,)).fetchone()
    db.execute("UPDATE guarantee_claims SET status = 'denied', note = ?, decided_at = ? WHERE id = ?",
               (note, ts(at), claim_id))
    notify(db, claim['trade_id'], f'Your refund for the month ending {claim["period_end"][:10]} was declined: {note}',
           '/trade/plan', at)
    db.commit()


# ── Stripe webhooks ───────────────────────────────────────────────────────────

def _unix(value):
    return ts(datetime.utcfromtimestamp(int(value)))


def _tier_for_price(price_id):
    for tier, pid in config.STRIPE_PRICES.items():
        if pid and pid == price_id:
            return tier
    return None


def _line_price_id(line):
    price = line.get('price')
    if price:                                       # older API versions
        return price['id'] if not isinstance(price, str) else price
    details = (line.get('pricing') or {}).get('price_details') or {}
    return details.get('price')


def _invoice_subscription(inv):
    if inv.get('subscription'):                     # older API versions
        s = inv['subscription']
        return s if isinstance(s, str) else s['id']
    parent = inv.get('parent') or {}
    return ((parent.get('subscription_details') or {}).get('subscription'))


_STATUS = {'active': 'active', 'trialing': 'active', 'past_due': 'past_due', 'unpaid': 'past_due',
           'canceled': 'cancelled', 'incomplete_expired': 'cancelled'}


def handle_webhook(db, payload, signature):
    event = stripe.Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
    kind, obj = event['type'], event['data']['object']

    if kind == 'checkout.session.completed':
        trade_id = int(obj.get('client_reference_id') or obj['metadata']['trade_id'])
        db.execute('UPDATE trades SET stripe_customer_id = ?, stripe_subscription_id = ? WHERE user_id = ?',
                   (obj['customer'], obj['subscription'], trade_id))
        db.commit()
        referrals.apply_credits(db, trade_id)      # banked free months come off the next invoices

    elif kind == 'invoice.paid':
        sub_id = _invoice_subscription(obj)
        trade = db.execute('SELECT * FROM trades WHERE stripe_subscription_id = ? OR stripe_customer_id = ?',
                           (sub_id, obj['customer'])).fetchone()
        if trade and obj['lines']['data']:
            line = obj['lines']['data'][0]
            tier = _tier_for_price(_line_price_id(line)) or trade['tier']
            start, end = _unix(line['period']['start']), _unix(line['period']['end'])
            db.execute("UPDATE trades SET tier = ?, sub_status = 'active', period_start = ?, period_end = ?, "
                       'stripe_subscription_id = COALESCE(stripe_subscription_id, ?) WHERE user_id = ?',
                       (tier, start, end, sub_id, trade['user_id']))
            _record_payment(db, trade['user_id'], tier, obj['amount_paid'], start, end, 'paid', invoice_id=obj['id'])

    elif kind in ('customer.subscription.updated', 'customer.subscription.deleted'):
        status = 'cancelled' if kind.endswith('deleted') else _STATUS.get(obj['status'], 'past_due')
        items = obj['items']['data']
        tier = _tier_for_price(items[0]['price']['id']) if items else None
        db.execute('UPDATE trades SET sub_status = ?, cancel_at_period_end = ?, tier = COALESCE(?, tier) '
                   'WHERE stripe_subscription_id = ?',
                   (status, 1 if obj.get('cancel_at_period_end') else 0, tier, obj['id']))
    db.commit()
    return kind
