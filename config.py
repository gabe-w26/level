"""
Product rules and settings.

Every rule the business model depends on lives here so it can be tuned in one
place: how many trades see a job, the quote cap, the redistribution window,
the three subscription tiers and the no-job refund guarantee.
"""
import os

BRAND = os.environ.get('BRAND_NAME', 'Level')
TAGLINE = 'Fair work for NZ trades'
SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', 'help@level.co.nz')

# A second admin, so someone else can always get in.
#
# There is deliberately NO default password. There used to be one, written here
# in the source — and this repository is public, so the password to a live admin
# account was readable by anyone who found the file. A password in source control
# is a published password, however short-lived you intend it to be.
#
# To create this account, set ADMIN2_PASSWORD in the hosting dashboard. Without
# it nothing is created and nothing is logged in to.
BOOTSTRAP_ADMIN = {
    'username': os.environ.get('ADMIN2_USERNAME', 'admin 2'),
    'password': os.environ.get('ADMIN2_PASSWORD', ''),
    'name': os.environ.get('ADMIN2_NAME', 'Thomas'),
}

# Passwords that were once a default in this file. Any account still using one is
# wide open, so `schema` shouts about it on every start until it is changed.
LEAKED_PASSWORDS = ['Sanjay2026']

# ── Job distribution ──────────────────────────────────────────────────────────
TRADES_PER_JOB = 15          # live slots per job (trades with an open offer + trades who quoted)
MAX_QUOTES = 3               # the customer never gets more than this; first in, first served
OFFER_WINDOW_HOURS = 4       # hours a trade has to quote before their slot is handed on. The clock
                             # runs around the clock: jobs keep moving overnight and at weekends.
JOB_OPEN_DAYS = 14           # after this a job stops being redistributed
ROTATION_LOOKBACK_DAYS = 30  # fair rotation counts offers received over this window
AUTO_PAUSE_AFTER = 5         # consecutive offers left to expire before a trade's leads pause
QUIET_AFTER_HOURS = 24       # tell the customer if their job still has no quotes after this

# ── How much email we'll send in a day ────────────────────────────────────────
# A free Gmail account will send about 500 messages a day over SMTP. Go past it
# and Google locks the mailbox for 24 hours — which takes password resets and
# job alerts down with it, not just the outreach. So we stop first, and outreach
# stops well before anything a person is waiting on.
MAIL_DAILY_CAP = int(os.environ.get('MAIL_DAILY_CAP', '400'))
MAIL_OUTREACH_SHARE = 0.6    # outreach may use this much of the cap; the rest is kept
                             # for password resets, job alerts and quote notifications

# ── Subscription tiers (they stack: a higher tier sees everything below it) ──
TIERS = {
    'small':  {'rank': 1, 'name': 'Up to $5k',  'price': 30, 'covers': 'Jobs up to $5,000'},
    'medium': {'rank': 2, 'name': 'Up to $50k', 'price': 50, 'covers': 'Jobs up to $50,000'},
    'large':  {'rank': 3, 'name': 'All jobs',   'price': 90, 'covers': 'Every job, including $50,000+'},
}
TIER_ORDER = ['small', 'medium', 'large']

# The value band a customer picks when posting. A job is visible to every tier
# whose rank is at least the band's rank.
VALUE_BANDS = {
    'small':  {'rank': 1, 'label': 'Under $5,000',       'short': '$0–5k'},
    'medium': {'rank': 2, 'label': '$5,000 – $50,000',   'short': '$5–50k'},
    'large':  {'rank': 3, 'label': 'Over $50,000',       'short': '$50k+'},
}

# How subscription prices are shown. NZ business pricing is usually quoted
# excluding GST — change to 'incl. GST' if the $30/$50/$90 already includes it.
PRICE_GST_NOTE = os.environ.get('PRICE_GST_NOTE', '+ GST')

# ── No-job guarantee ──────────────────────────────────────────────────────────
GUARANTEE_MIN_QUOTES = 5     # quotes needed in the month to qualify (lowered if fewer jobs were offered)
GUARANTEE_AUTO_APPROVE = os.environ.get('GUARANTEE_AUTO_APPROVE', '1') == '1'   # no claim form, no review

# ── Building Act: residential work at or over this value needs a written
# contract, disclosure statement and the prescribed checklist.
CONTRACT_THRESHOLD = 30000

# ── Billing ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICES = {
    'small':  os.environ.get('STRIPE_PRICE_SMALL', ''),
    'medium': os.environ.get('STRIPE_PRICE_MEDIUM', ''),
    'large':  os.environ.get('STRIPE_PRICE_LARGE', ''),
}
BILLING_LIVE = bool(STRIPE_SECRET_KEY and all(STRIPE_PRICES.values()))
DEMO_PERIOD_DAYS = 30        # length of a billing month when Stripe isn't configured

# Pilot mode: trades pick a plan and get jobs, but no card is asked for and
# nothing is charged. The no-job guarantee doesn't apply while it's on, because
# there is nothing to refund. Set FREE_PILOT=0 (with Stripe keys) to charge.
FREE_PILOT = os.environ.get('FREE_PILOT', '1') == '1'
CHARGING = BILLING_LIVE and not FREE_PILOT

# ── Your business details, shown in the terms, privacy page and footer ────────
LEGAL_ENTITY = os.environ.get('LEGAL_ENTITY', 'Level')
LEGAL_NZBN = os.environ.get('LEGAL_NZBN', '')
LEGAL_ADDRESS = os.environ.get('LEGAL_ADDRESS', 'New Zealand')
TERMS_UPDATED = os.environ.get('TERMS_UPDATED', '18 September 2026')

# ── Job options ───────────────────────────────────────────────────────────────
TIMING = {
    'asap':     'As soon as possible',
    'weeks':    'In the next few weeks',
    'months':   'In the next few months',
    'flexible': "I'm flexible / still planning",
}
PROPERTY_TYPES = {
    'house':      'House',
    'unit':       'Apartment or unit',
    'rural':      'Lifestyle block or rural',
    'commercial': 'Commercial property',
}

# (slug, name, licence note shown to customers — None when no licence is required)
CATEGORIES = [
    ('builder',        'Builder',                    'Restricted building work must be done or supervised by a Licensed Building Practitioner (LBP).'),
    ('carpenter',      'Carpenter & joiner',         None),
    ('electrician',    'Electrician',                'Electrical work must be done by a registered electrical worker.'),
    ('plumber',        'Plumber',                    'Sanitary plumbing must be done by a registered plumber.'),
    ('gasfitter',      'Gasfitter',                  'Gasfitting must be done by a registered gasfitter.'),
    ('drainlayer',     'Drainlayer',                 'Drainlaying must be done by a registered drainlayer.'),
    ('roofer',         'Roofer',                     'Roofing that is restricted building work needs an LBP with a roofing licence.'),
    ('bricklayer',     'Bricklayer & blocklayer',    'Structural brick and block work is restricted building work and needs an LBP.'),
    ('plasterer',      'Plasterer & GIB stopper',    'External plastering that is restricted building work needs an LBP.'),
    ('painter',        'Painter & decorator',        None),
    ('tiler',          'Tiler',                      None),
    ('flooring',       'Flooring',                   None),
    ('kitchen-bath',   'Kitchen & bathroom',         None),
    ('landscaper',     'Landscaper',                 None),
    ('fencer',         'Fencer',                     None),
    ('concreter',      'Concreter',                  None),
    ('glazier',        'Glazier',                    None),
    ('heat-pumps',     'Heat pumps & ventilation',   'Installers need a refrigerant handling certificate; wiring must be done by a registered electrician.'),
    ('insulation',     'Insulation',                 None),
    ('arborist',       'Arborist',                   None),
    ('demolition',     'Demolition',                 None),
    ('earthworks',     'Earthworks & excavation',    None),
    ('scaffolding',    'Scaffolding',                None),
    ('handyman',       'Handyman',                   None),
    ('designer',       'Architect & designer',       'Design of restricted building work needs an LBP with a design licence or a registered architect.'),
]

# (slug, name, region) — trades pick every area they cover
AREAS = [
    ('northland',          'Northland',                          'Northland'),
    ('akl-north-shore',    'Auckland – North Shore',             'Auckland'),
    ('akl-rodney',         'Auckland – Rodney & Hibiscus Coast', 'Auckland'),
    ('akl-west',           'Auckland – West',                    'Auckland'),
    ('akl-central',        'Auckland – Central',                 'Auckland'),
    ('akl-east',           'Auckland – East',                    'Auckland'),
    ('akl-south',          'Auckland – South & Franklin',        'Auckland'),
    ('hamilton',           'Hamilton',                           'Waikato'),
    ('waikato',            'Waikato – wider region',             'Waikato'),
    ('coromandel',         'Coromandel & Thames',                'Waikato'),
    ('tauranga',           'Tauranga & Western Bay',             'Bay of Plenty'),
    ('rotorua',            'Rotorua & Taupō',                    'Bay of Plenty'),
    ('eastern-bop',        'Eastern Bay of Plenty',              'Bay of Plenty'),
    ('gisborne',           'Gisborne',                           'Gisborne'),
    ('hawkes-bay',         "Hawke's Bay",                        "Hawke's Bay"),
    ('taranaki',           'Taranaki',                           'Taranaki'),
    ('manawatu',           'Palmerston North & Manawatū',        'Manawatū-Whanganui'),
    ('whanganui',          'Whanganui & Ruapehu',                'Manawatū-Whanganui'),
    ('wellington',         'Wellington City',                    'Wellington'),
    ('hutt',               'Hutt Valley',                        'Wellington'),
    ('porirua-kapiti',     'Porirua & Kāpiti',                   'Wellington'),
    ('wairarapa',          'Wairarapa',                          'Wellington'),
    ('nelson-tasman',      'Nelson & Tasman',                    'Nelson-Tasman'),
    ('marlborough',        'Marlborough',                        'Marlborough'),
    ('west-coast',         'West Coast',                         'West Coast'),
    ('christchurch',       'Christchurch',                       'Canterbury'),
    ('selwyn-waimak',      'Selwyn & Waimakariri',               'Canterbury'),
    ('south-canterbury',   'Mid & South Canterbury',             'Canterbury'),
    ('dunedin',            'Dunedin',                            'Otago'),
    ('queenstown',         'Queenstown Lakes',                   'Otago'),
    ('central-otago',      'Central Otago',                      'Otago'),
    ('southland',          'Southland',                          'Southland'),
]
