# Getting Level into the App Store and Google Play

The app is ready to build. What's left needs accounts in your name.

## What you need to sign up for

| Account | Cost | Where |
|---|---|---|
| Apple Developer Program | $149 NZD a year | https://developer.apple.com/programs/enroll/ — enrol as an **organisation** (needs a D-U-N-S number, free, takes ~1–2 weeks) so the store shows your business name, or as an individual to start sooner |
| Google Play Console | $25 USD once | https://play.google.com/console/signup — new personal accounts must run a closed test with 12 testers for 14 days before going public; organisation accounts skip that |
| Expo (EAS) | Free tier is enough | https://expo.dev/signup |

## One-time setup

```bash
cd level-trades/mobile
export PATH="$HOME/.level-node:$PATH"        # see SETUP.md
npx eas-cli login
npx eas-cli init                              # creates the project, fills extra.eas.projectId in app.json
```

Bundle ID / package: `nz.level.app` (set in `app.json`). Change it before the
first upload if you want something else — it can't be changed afterwards.

## Build and submit

```bash
npx eas-cli build --platform ios --profile production       # EAS asks for your Apple login and makes the certificates
npx eas-cli build --platform android --profile production
npx eas-cli submit --platform ios        # needs ascAppId in eas.json (App Store Connect → App Information → Apple ID)
npx eas-cli submit --platform android    # first Android upload must be done by hand in Play Console
```

Push on iOS: EAS creates the APNs key during the iOS build if you say yes. For
Android push, upload an FCM V1 service-account key under Expo → Project →
Credentials.

Test on your phone before submitting: `eas build --profile preview` gives an
installable build (TestFlight for iOS after the first production upload).

## App Store Connect listing

- **Name:** Level — NZ Tradies
- **Subtitle (30):** Fair quotes from local trades
- **Category:** Lifestyle (secondary: Business)
- **Age rating:** 4+ (no objectionable content; users can message each other — answer "No" to unrestricted web access)
- **Price:** Free. No in-app purchases.
- **Promotional text:** Post a job free and get quotes from local New Zealand tradies — or get fair, local jobs if you're a tradie.
- **Description:**

  > Level is a fairer way to find a tradie in New Zealand.
  >
  > Post your job in a couple of minutes — what needs doing, your suburb and a few photos. We offer it to 15 local trades who do that work. They have 4 working hours to quote, and if they don't, the job goes to someone new. You get up to three quotes, first in first served, so you're never swamped with calls.
  >
  > Compare quotes side by side, message trades, share your contact details only with the ones you like, and accept the one you want. Trades see your suburb, never your address, until you choose them.
  >
  > For tradies: see jobs in your trades and areas the moment they're posted, with a live countdown, quote from the job site, and message customers. Jobs are shared out fairly — no bidding, no lead fees.
  >
  > Level is made in Aotearoa.

- **Keywords (100):** tradie,tradesman,builder,plumber,electrician,quotes,NZ,handyman,renovation,roofer,painter,jobs
- **Support URL:** https://level-wcyc.onrender.com (or your domain) — needs a way to contact you; the site footer has help@level.co.nz
- **Privacy policy URL:** https://level-wcyc.onrender.com/privacy
- **Copyright:** 2026 (your legal entity)

### Screenshots to take

Apple needs 6.9" iPhone screenshots (1320×2868, e.g. iPhone 17 Pro Max Simulator);
the 6.5" set is optional. Google needs at least 2 phone screenshots. Take these with
demo data (`python3 seed.py`, then run the app against it):

1. Welcome — "I need a tradie / I'm a tradie"
2. Customer: Post a job (trade and area picked, a photo added)
3. Customer: Job with 3–4 quotes and the quote meter
4. Customer: Quote detail with Accept / Share buttons
5. Trade: Jobs tab with countdowns
6. Trade: Send a quote form
7. Messages thread

In the Simulator: `xcrun simctl io booted screenshot shot.png`.

## App Privacy ("nutrition label") answers

Data is **linked to the user**, **not used for tracking**, no third-party advertising or analytics.

| Data type | Collected | Why |
|---|---|---|
| Contact info — Name | Yes | App functionality (shown to trades/customers you choose) |
| Contact info — Email address | Yes | App functionality, account |
| Contact info — Phone number | Yes | App functionality (shared with chosen trades), phone check |
| Contact info — Physical address | Yes (optional) | App functionality (shared only with the trade you choose) |
| User content — Photos | Yes | App functionality (job photos) |
| User content — Other user content | Yes | Job descriptions, quotes, messages, reviews |
| Identifiers — User ID | Yes | App functionality |
| Identifiers — Device ID | Yes | Push notification token only |
| Location, Health, Financial, Browsing, Contacts, Diagnostics | **No** | — |

Google Play Data safety: same list; data is encrypted in transit (HTTPS); users
can request deletion (in-app: Account → Delete my account).

## Review notes (paste into "App Review Information")

> Level is a marketplace connecting New Zealand homeowners with local tradespeople.
>
> Demo accounts (live data on the production site):
> • Customer: customer-review@level.co.nz / (password) — has a job with quotes
> • Tradie: trade-review@level.co.nz / (password) — has open job offers
>
> Account deletion: Account tab → "Delete my account" → enter password → confirm.
>
> There are no in-app purchases. Posting jobs is free. Tradespeople are on a free
> pilot; any business subscription is a B2B arrangement handled outside the app,
> and the app contains no pricing, payment links or calls to action to purchase.
>
> Push notifications tell tradies about new job offers (the window to quote is short)
> and customers about new quotes and messages.

Create those two review accounts on the live site before submitting (a customer
with a posted job, and a trade with categories/areas set and a plan chosen so it
gets offers), and keep them working while the app is in review.

## Compliance built in

- **3.1.1 / 3.1.3 — no in-app purchases, no steering to outside payment.** The app
  never shows prices, plans or payment links. A trade whose account isn't ready
  sees "Finish setting up on the website"; the link is only shown while the free
  pilot is on (`FREE_PILOT=1`). Once charging starts the server stops sending
  the link (`api_mobile.trade_status`).
- **5.1.1(v) — account deletion in the app.** Account → Delete my account runs
  the same close-and-scrub as the website (`accounts.close`) and removes the
  phone's sign-in and push tokens.
- **5.1.1 — no sign-in needed to look around.** The welcome screen explains how
  Level works without an account.
- **4.2 — a real app, not a website in a wrapper.** Every screen is native and
  uses the JSON API; only Terms, Privacy and website-only settings open in the
  browser.
- **Permissions:** camera and photos (job photos) and notifications only. The
  microphone permission from the image picker is switched off; location and
  audio are blocked on Android.
- `ITSAppUsesNonExemptEncryption: false` is set (HTTPS only), so no export
  compliance questions on each upload.

## Before you submit — checklist

- [ ] Apple Developer + Google Play accounts approved
- [ ] `eas init` run; `extra.eas.projectId` filled
- [ ] `ascAppId` in `eas.json`
- [ ] Production site has its web address (Admin → Setup → Your web address) set to the real domain, so links in the app and emails point there
- [ ] Terms and privacy pages show your legal entity (`LEGAL_ENTITY`, `LEGAL_NZBN`, `LEGAL_ADDRESS` on Render)
- [ ] Privacy page mentions the app, push notifications and photo uploads
- [ ] Review demo accounts created on the live site
- [ ] Screenshots taken
- [ ] Deploy the server changes (`api_mobile.py`, `push.py`, schema) to production before the app goes live — the app talks to `/api/mobile` on the live site
