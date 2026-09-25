"""
Will these emails land in the inbox, or the spam folder?

Outreach is the whole growth plan, and a free lead that lands in spam is worse
than not sending it: the tradie never sees it, and the complaint quietly damages
every other email — password resets included.

This checks the things that actually decide it, before anything goes out:

  1. **Is the From address allowed to send?** SPF, DKIM and DMARC records in DNS,
     looked up live. This is the single biggest factor and the easiest to get
     wrong.
  2. **Does the From address match the account we log in as?** Sending as
     hello@yourdomain through a gmail.com account fails SPF and DKIM alignment
     and goes straight to spam. It is the commonest mistake and nothing stops
     you making it.
  3. **One-click unsubscribe.** Since February 2024 Gmail and Yahoo expect bulk
     senders to support it. Having the link in the body is not enough.
  4. **The obvious content traps** — a subject that opens with "Free", shouting
     in capitals, a wall of links.

What it cannot tell you is whether a particular mailbox will accept you, which
is why `/admin/deliverability` also sends a real one to an address you choose.
Send it to mail-tester.com and you get a score out of ten with the specifics.

DNS is looked up over HTTPS rather than with a resolver library, so this needs
nothing installed and works the same on a laptop and on Render.
"""
import json
import re
import urllib.parse
import urllib.request

import config
import integrations

DOH = 'https://dns.google/resolve'
TIMEOUT = 6

# Words that make a filter suspicious when they open a subject line. This is not
# a spam-word superstition list — these are the ones that measurably hurt.
RISKY_OPENERS = ('free', 'earn', 'cash', 'guarantee', 'winner', 'urgent', 'act now')


def _txt(name):
    """TXT records for a name, or [] if we can't find out."""
    try:
        url = f'{DOH}?name={urllib.parse.quote(name)}&type=TXT'
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode())
    except Exception:
        return []
    out = []
    for answer in data.get('Answer') or []:
        value = (answer.get('data') or '').strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('" "', '')
        out.append(value)
    return out


def _domain_of(address):
    match = re.search(r'@([A-Za-z0-9.-]+)', address or '')
    return match.group(1).lower().rstrip('>') if match else ''


def from_address():
    """The address recipients will see, and the one we actually log in as."""
    shown = integrations.get('mail_from') or ''
    login = integrations.get('smtp_user') or ''
    seen = _domain_of(shown) or _domain_of(login)
    return {'shown': shown or f'{config.BRAND} <{login}>', 'login': login,
            'domain': seen, 'login_domain': _domain_of(login)}


# ── The checks ──────────────────────────────────────────────────────────────

def check_spf(domain):
    records = [r for r in _txt(domain) if r.lower().startswith('v=spf1')]
    if not records:
        return fail('SPF', f'No SPF record on {domain}.',
                    'Without one, most filters treat the mail as unverified. Add a TXT record on '
                    f'{domain} saying which servers may send as you.')
    if len(records) > 1:
        return fail('SPF', f'{domain} has {len(records)} SPF records.',
                    'More than one is invalid and filters treat it as none at all. Merge them into one.')
    record = records[0]
    if '-all' in record:
        note = 'Strict (-all): anything else is rejected.'
    elif '~all' in record:
        note = 'Soft fail (~all), which is the usual setting.'
    else:
        note = 'Ends with ?all or +all, which tells filters almost nothing. Use ~all.'
    return ok('SPF', f'Found on {domain}.', note + f'  {record[:120]}')


def check_dkim(domain):
    """Look for a DKIM key at the selectors the common providers use.

    There is no way to list selectors, so this can only say "found one" — never
    "there isn't one". It says so rather than pretending.
    """
    selectors = ['google', 'default', 'selector1', 'selector2', 's1', 's2',
                 'resend', 'sendgrid', 'mail', 'k1', 'smtp']
    for selector in selectors:
        if any('p=' in r for r in _txt(f'{selector}._domainkey.{domain}')):
            return ok('DKIM', f'Signing key found ({selector}).',
                      'Mail from this domain can be cryptographically signed, which is what filters '
                      'most want to see.')
    return warn('DKIM', 'No signing key found at the usual names.',
                f'We checked {len(selectors)} common selectors on {domain} and found none — but a key '
                'can live anywhere, so this is "we couldn’t find it", not "there isn’t one". Your email '
                'provider’s dashboard will say.')


def check_dmarc(domain):
    records = [r for r in _txt(f'_dmarc.{domain}') if r.lower().startswith('v=dmarc1')]
    if not records:
        return warn('DMARC', f'No DMARC record on {domain}.',
                    'Gmail and Yahoo expect one from bulk senders. Start with '
                    f'"v=DMARC1; p=none; rua=mailto:you@{domain}" — it changes nothing about delivery '
                    'and starts sending you reports on who is passing and failing.')
    policy = re.search(r'p=(\w+)', records[0])
    return ok('DMARC', f'Found (p={policy.group(1) if policy else "?"}).', records[0][:120])


def check_alignment(sender):
    """The commonest own-goal: a From address the login can't vouch for."""
    if not sender['login']:
        return fail('From address', 'No SMTP username set.', 'Nothing can send yet.')
    if not sender['domain']:
        return fail('From address', 'Couldn’t read the From address.', '')
    if sender['domain'] == sender['login_domain']:
        return ok('From address', f'From and login both use {sender["domain"]}.',
                  'SPF and DKIM line up, which is what a filter checks first.')
    return fail('From address',
                f'You send as {sender["domain"]} but log in as {sender["login_domain"]}.',
                f'This fails SPF and DKIM alignment and is close to a guaranteed spam folder. Either '
                f'clear "Send from" in Setup so it matches {sender["login_domain"]}, or send through a '
                f'provider that has verified {sender["domain"]}.')


def check_gmail_for_bulk(sender):
    if sender['login_domain'] in ('gmail.com', 'googlemail.com', 'hotmail.com', 'outlook.com', 'yahoo.com'):
        return warn('Sending account',
                    f'Sending from a personal {sender["login_domain"]} account.',
                    'Fine for a handful of password resets. For cold outreach it is the weakest option: '
                    'filters treat bulk mail from consumer accounts harshly, you cannot set up DKIM for '
                    'your own brand, and Google may suspend the account for it. Move to a provider on '
                    'your own domain before the outreach run.')
    return ok('Sending account', f'Sending from {sender["login_domain"] or "a custom domain"}.', '')


def check_subject(subject):
    # Capitals first: it is the worse signal, and a subject can trip both.
    if subject and subject == subject.upper() and len(subject) > 8:
        return fail('Subject line', 'It is in capitals.', 'Shouting is a strong spam signal.')
    first = (subject or '').strip().split(' ')[0].lower().strip(':,')
    if first in RISKY_OPENERS:
        return warn('Subject line', f'It opens with “{first}”.',
                    'Opening on a word like that is one of the oldest filter signals there is. Put the '
                    'trade and the suburb first — that is the part a tradie cares about anyway.')
    return ok('Subject line', 'Nothing obviously risky.', subject[:90] if subject else '')


def check_unsubscribe():
    import mailer
    msg = mailer._build('someone@example.com', 'Someone', 'Test', 'Body',
                        unsubscribe_url='https://example.com/stop')
    if msg.get('List-Unsubscribe-Post'):
        return ok('One-click unsubscribe', 'Set up properly.',
                  'Gmail and Yahoo have expected this from bulk senders since February 2024.')
    return fail('One-click unsubscribe', 'Missing the List-Unsubscribe-Post header.',
                'The link in the body is not enough. Without this header Gmail and Yahoo treat us as a '
                'bulk sender who won’t let people leave.')


def ok(name, headline, detail=''):
    return {'name': name, 'level': 'ok', 'headline': headline, 'detail': detail}


def warn(name, headline, detail=''):
    return {'name': name, 'level': 'warn', 'headline': headline, 'detail': detail}


def fail(name, headline, detail=''):
    return {'name': name, 'level': 'fail', 'headline': headline, 'detail': detail}


def report(sample_subject=None):
    """Everything we can tell without sending anything."""
    sender = from_address()
    checks = [check_alignment(sender), check_gmail_for_bulk(sender), check_unsubscribe()]
    if sender['domain']:
        checks += [check_spf(sender['domain']), check_dkim(sender['domain']),
                   check_dmarc(sender['domain'])]
    if sample_subject:
        checks.append(check_subject(sample_subject))
    worst = 'ok'
    for check in checks:
        if check['level'] == 'fail':
            worst = 'fail'
            break
        if check['level'] == 'warn':
            worst = 'warn'
    return {'sender': sender, 'checks': checks, 'worst': worst,
            'fails': sum(1 for c in checks if c['level'] == 'fail'),
            'warns': sum(1 for c in checks if c['level'] == 'warn')}
