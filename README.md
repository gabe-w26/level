# Level — fair work for NZ trades

A trades marketplace built on four rules:

1. **15 trades per job.** A job is offered to 15 trades who do that work, in that area, on a plan that covers the job’s size.
2. **24 hours to quote.** Trades who haven’t quoted after 24 hours lose their slot, and a new trade gets it. Passing hands the slot on immediately.
3. **6 quotes, then it closes.** First in, first served. Quote number 7 is refused, even if two trades press send at once.
4. **Fair rotation, no review bias.** Trades offered the fewest similar jobs recently go first. Reviews are shown to customers, but never used to decide who’s offered a job.

Trades pay a flat monthly price with no tokens or lead fees. The tiers stack, so each one includes every job below it:

| Plan | Price | Sees jobs |
|---|---|---|
| Up to $5k | $30/month | under $5,000 |
| Up to $50k | $50/month | under $50,000 |
| All jobs | $90/month | everything, including $50,000+ |

**No-job guarantee:** a trade who quotes on 5 jobs in a month and wins none gets that month refunded.
- If fewer than 5 jobs were offered, they need to quote on every job offered.
- If they paused during the month, they need the full 5.

## Run it locally

```bash
cd level-trades
python3 seed.py     # optional: 60 demo trades, 9 customers, live jobs and quotes
python3 app.py      # http://localhost:5050
```

Demo logins (after `seed.py`):
- Customer: `customer@level.local`, password `demo1234`
- Trade: `trade@level.local`, password `demo1234`
- Admin: `admin@level.local`, password `admin123`

**Try this:**
1. Log in as the customer and open **Replace rotten deck boards**. You'll see the distribution tracker, quotes in arrival order, the side-by-side comparison, shared contact details and messages.
2. Log in as the trade. You'll see jobs waiting with countdowns. Send a quote, or pass on a job. Check the guarantee progress and "fair share" panels.
3. Log in as admin and press **+24 hours**. Open a job's distribution log: non-quoters have expired and new trades have been offered it, as wave 2.
4. Press **+30 days**. Months end, and **Refunds** lists who qualifies under the guarantee.

## Where the rules live

| Rule | Code | Setting in `config.py` |
|---|---|---|
| 15 live slots per job, refill when a slot frees | `engine.fill_slots` | `TRADES_PER_JOB` |
| 24 h offer window, handover | `engine.sweep` | `OFFER_WINDOW_HOURS` |
| 6-quote cap (race-safe conditional UPDATE) | `engine.submit_quote` | `MAX_QUOTES` |
| Fair rotation | `engine._candidates` | `ROTATION_LOOKBACK_DAYS` |
| Stacking tiers | `engine.tiers_that_see` | `TIERS`, `VALUE_BANDS` |
| Auto-pause after 5 ignored jobs | `engine._maybe_auto_pause` | `AUTO_PAUSE_AFTER` |
| No-job guarantee | `engine.evaluate_guarantees` | `GUARANTEE_MIN_QUOTES`, `GUARANTEE_AUTO_APPROVE` |
| $30k Building Act contract docs | `engine.needs_act_docs` | `CONTRACT_THRESHOLD` |
| Plans, renewals, refunds, webhooks | `billing.py` | `STRIPE_*` |

A background thread runs the sweep every 2 minutes. A database lock stops more than one worker running it at once. You can also run it from cron with `flask --app app sweep`.

## Tests

```bash
python3 -m unittest discover tests -v
```

32 tests in two files:

- `tests/test_rules.py` — the marketplace rules: exact-15 distribution, tier stacking, 24-hour replacement with *new* trades, passing, the 6-quote cap, even rotation, reviews having zero effect, auto-pause, the $30k contract docs, and every guarantee case.
- `tests/test_accounts.py` — what real users need: password reset links (one use, and they expire), closing an account, unsubscribe, the free pilot, the budget locking once trades hold a job, and revising a quote.

## Configuration

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Required in production |
| `DATABASE_URL` | Postgres. Without it, SQLite `database.db` is used |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Creates the admin account in production |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Turns on real billing. Without them, demo billing is used |
| `STRIPE_PRICE_SMALL`, `STRIPE_PRICE_MEDIUM`, `STRIPE_PRICE_LARGE` | Monthly NZD price IDs for the three plans |
| `FREE_PILOT` | `1` (the default) means trades get jobs with no card and no charges. Set to `0`, with Stripe keys, to start charging |
| `GUARANTEE_AUTO_APPROVE=1` | Pay guarantee refunds without admin review |
| `LEGAL_ENTITY`, `LEGAL_NZBN`, `LEGAL_ADDRESS`, `TERMS_UPDATED` | Your business details, shown on the terms and privacy pages |
| `PRICE_GST_NOTE` | `+ GST` (default) or `incl. GST` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `BASE_URL` | Email notifications |
| `UPLOAD_FOLDER` | Where job photos go. Use a persistent disk in production |
| `DEMO_TOOLS` | Admin clock and demo data. On locally, off when `DATABASE_URL` is set |
| `BRAND_NAME` | Renames the product everywhere |

## Deploying to Render

1. Create a Postgres database, then a web service from this folder.
   - Build command: `pip install -r requirements.txt`
   - Start command: use the one in the `Procfile`
2. Set the environment variables:
   - `DATABASE_URL`: the database's *Internal* URL
   - `SECRET_KEY`
   - `ADMIN_EMAIL` and `ADMIN_PASSWORD`
3. Attach a disk and point `UPLOAD_FOLDER` at it, so photos survive deploys.
4. Set up Stripe:
   - Create one product with three monthly NZD prices, and put their IDs in `STRIPE_PRICE_*`.
   - Add a webhook to `https://YOUR-DOMAIN/stripe/webhook` with these events: `checkout.session.completed`, `invoice.paid`, `customer.subscription.updated`, `customer.subscription.deleted`.
   - Put its signing secret in `STRIPE_WEBHOOK_SECRET`.
   - Test the whole flow with test-mode keys first. The Stripe code hasn't been run against a real account yet.

## Setting up email

Nothing is emailed until `SMTP_HOST` is set. Until then, every message is printed in the terminal — including password reset links, so you can still test the flow.

Two easy options:

- **Gmail:** turn on 2-step verification, create an App Password, then set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`, `SMTP_PASS=<app password>`. Fine for a pilot; Gmail limits you to about 500 messages a day.
- **Resend or Postmark:** free to start, better delivery, and you can send from your own domain. Use the SMTP details they give you.

Also set `BASE_URL` to your live web address, so links in emails point at the real site.

## Going live: the checklist

1. **Decide on GST** and set `PRICE_GST_NOTE` to match.
2. **Have a lawyer read `/terms` and `/privacy`.** They're written for this business in plain English, but they're a starting point, not legal advice. Set `LEGAL_ENTITY`, `LEGAL_NZBN` and `LEGAL_ADDRESS` first so they show your real details.
3. **Push the code to a private GitHub repo.**
4. **Deploy** using the Render steps below. Set `SECRET_KEY`, `DATABASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `BASE_URL`, the `SMTP_*` variables and `FREE_PILOT=1`.
5. **Check `/health`** returns `"status": "ok"` and `"database": "postgres"`.
6. **Confirm the demo tools are off**: `/demo/login/admin` must return "page not found" on the live site.
7. **Point a domain at it** and turn on HTTPS (Render does this for you).
8. **Recruit trades before customers.** A job with no trades in its area gets no quotes. Get 15 or more trades signed up per trade type and area before you send customers there.
9. **Turn on charging when you're ready:** add the Stripe keys and prices, set `FREE_PILOT=0`, and tell trades before their first bill.

## Before launch: decisions and advice this code can't give you

- **Patent.** Builderscrack holds NZ patent #594581 on its "double handshake": the trade requests contact, then the homeowner accepts. Level's flow (the trade quotes, then the customer chooses to share details) is different, but have a patent attorney compare the two.
- **Guarantee and terms.** Under the Fair Trading Act, "refund" claims must match the conditions. Unfair contract terms rules have covered small trade contracts since August 2022. Have a lawyer review the trade terms and guarantee wording. Terms and privacy pages aren't built yet.
- **Guarantee cost when jobs are scarce.** Trades offered no jobs qualify automatically. In a trade or area with few jobs, most of the month's subscriptions will be refunded. Launch trades and areas where demand is proven, or require a minimum number of offers.
- **GST.** Decide whether $30/$50/$90 include GST, and set `PRICE_GST_NOTE` to match.
- **Licence registers.** The LBP register (Building Act s307) and the PGDB terms limit how their data can be used. Confirm that verification for a marketplace is allowed before automating it.
- **Name.** "Level" is a placeholder. Check the trademark and a `.co.nz` domain.

## Roadmap (researched, not built yet)

Ordered by impact. See `docs/RESEARCH.md` for the reasoning.

1. Customer phone verification by SMS, to stop fake and dead jobs.
2. Instant SMS or push alerts for new jobs. Email already works once SMTP is set.
3. Automatic LBP checks via `api.business.govt.nz` (needs MBIE approval) and NZBN API lookups.
4. AI job-brief helper: turns a rough description and photos into a structured brief, and suggests the value band.
5. Pre-filled disclosure statement and contract template for quotes of $30k+.
6. Defect reporting for 12 months after a job is completed, matching the Building Act defect repair period.
7. Milestone payments through Stripe Connect. Don't hold customer money yourself: that makes you a financial service provider under the FSP Act.
8. Fergus and Xero integrations, and a native app.
9. Privacy extras: auto-delete contact details after a job closes, and an IPP3A notice when the site contact is someone else.
