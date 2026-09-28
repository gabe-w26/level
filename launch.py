"""
Is this thing ready to open?

A checklist written down somewhere goes stale the moment something changes, and
the one thing worse than no checklist is one that says you're ready when you
aren't. So nothing here is typed in: every item asks the running system and
reports what it finds.

Three kinds of item, because they are not the same thing:

  · **Blocking** — open without it and somebody has a bad time. Email is the
    obvious one: no password resets, no job alerts, and a waitlist collecting
    addresses it cannot write to.
  · **Risky** — you can open, but you're carrying a loss you haven't priced.
    No backup off the server is the whole of this category.
  · **Worth doing** — real, not urgent.

The honest part is that "done" here means "this system can see it is done". A
domain's DNS records, a legal review, an Apple developer account: this can't
check any of those, and says so rather than leaving them off the list and
implying they don't exist.
"""
import config

BLOCKING, RISKY, WORTH = 'blocking', 'risky', 'worth'


def _item(key, kind, title, done, detail, where=None, cannot_check=False):
    return {'key': key, 'kind': kind, 'title': title, 'done': done, 'detail': detail,
            'where': where, 'cannot_check': cannot_check}


def check(db):
    """Everything this system can actually determine about being ready."""
    import ai
    import backup
    import mailer
    import sms
    import waitlist as wl
    import integrations

    out = []

    # ── Blocking ──────────────────────────────────────────────────────────────
    out.append(_item(
        'email', BLOCKING, 'Email works', mailer.enabled(),
        'Without it: no password resets, no job alerts to trades, and the waitlist is collecting '
        'addresses nobody can write to. This is the one that stops everything else being useful.',
        'admin_setup'))

    site = integrations.get('site_url')
    out.append(_item(
        'site_url', BLOCKING, 'Web address set', bool(site),
        'Every link in every email and text is built from this. Unset, they point at wherever the '
        'server thinks it is, which is not your domain.' if not site else f'Links are built from {site}.',
        'admin_setup'))

    areas = wl.by_area(db)
    ready = [a for a in areas if a['ready']]
    out.append(_item(
        'coverage', BLOCKING, 'Somewhere with enough trades to open',
        bool(ready),
        (f'{len(ready)} area{"s" if len(ready) != 1 else ""} ready: '
         + ', '.join(a['name'] for a in ready[:3]) + ('…' if len(ready) > 3 else ''))
        if ready else
        'No area yet has both enough trades and enough homeowners. Opening one that hasn’t is how '
        'you waste the only first impression you get.',
        'admin_waitlist'))

    # ── Risky ────────────────────────────────────────────────────────────────
    st = backup.status(db)
    out.append(_item(
        'backup', RISKY, 'A backup exists somewhere that isn’t this server',
        not st['never_downloaded'] and not st['stale'],
        'Nobody has ever downloaded one. Everything here exists in one place.'
        if st['never_downloaded'] else
        (f'Last taken {st["downloaded_days_ago"]} days ago.' if st['stale']
         else f'Last taken {st["downloaded_days_ago"]} days ago.'),
        'admin_setup'))

    exp = backup.expiry()
    out.append(_item(
        'db_expiry', RISKY, 'Database expiry date recorded', bool(exp),
        (f'{exp["days"]} days left ({exp["date"]}).' if exp else
         'A free Postgres instance is deleted on a date and nothing warns you. Put the date in and '
         'every admin page counts down to it.'),
        'admin_setup'))

    out.append(_item(
        'admin2', RISKY, 'A second admin login exists',
        bool(config.BOOTSTRAP_ADMIN.get('password')),
        'Set ADMIN2_PASSWORD in Render so somebody else can always get in. There is deliberately no '
        'default — a password in source control is a published password.'
        if not config.BOOTSTRAP_ADMIN.get('password') else 'Set from the environment.'))

    # ── Worth doing ──────────────────────────────────────────────────────────
    out.append(_item(
        'billing', WORTH, 'Taking money', config.CHARGING,
        'Free pilot is on — nobody is charged, which is the right way to start.'
        if config.FREE_PILOT else
        ('Stripe keys and all three prices are set.' if config.BILLING_LIVE else
         'Stripe isn’t fully configured, so plans switch on without a payment.')))

    out.append(_item(
        'texts', WORTH, 'Text messages', sms.enabled(),
        'Optional. Trades hear about jobs by email either way; texts just make it quicker.',
        'admin_setup'))

    out.append(_item(
        'ai', WORTH, 'The “help me describe it” helper', ai.enabled(),
        'Optional. Customers write a better job description with it, which means better quotes.',
        'admin_setup'))

    return out


# Things this can't determine, listed rather than quietly omitted — a checklist
# that only shows what it can measure reads as a complete one.
CANNOT_CHECK = [
    ('A domain you own', 'Needed before email is worth setting up — sending cold mail from a '
                         'personal Gmail is the weakest option there is, and Google can suspend it.'),
    ('SPF, DKIM and DMARC on that domain', 'Admin → Deliverability checks these once the domain '
                                           'exists and tells you exactly what is missing.'),
    ('The name and any trade mark clash', 'Worth a lawyer’s hour before the name is on invoices.'),
    ('Apple and Google developer accounts', 'NZ$149/yr and NZ$25 once, only worth paying when '
                                            'there is work flowing through the app.'),
]


def summary(db):
    items = check(db)
    blocking = [i for i in items if i['kind'] == BLOCKING and not i['done']]
    risky = [i for i in items if i['kind'] == RISKY and not i['done']]
    return {'items': items, 'blocking': blocking, 'risky': risky,
            'done': sum(1 for i in items if i['done']), 'total': len(items),
            'ready': not blocking}
