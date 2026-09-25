# Switching it on, and the first fortnight

Two halves. The first is an hour of settings, because a lot of what's built is
sitting behind a switch that hasn't been flicked. The second is getting ten real
jobs, which is the only thing that matters after that.

A marketplace fails when one side shows up and the other doesn't. Level's harder
side is **jobs**, not tradies: there are 492 tradie contacts ready to email and
zero homeowners. Almost everything below spends its effort on jobs.

---

## Part one — the switches

In this order. The first one is security, the second unlocks most of the rest.

### 1. Change the `admin 2` password — today

This repository is public, and until recently `config.py` carried a default
password for that account in plain text. It is out of the file now, but it is
still in the git history, so anyone who looks can read it.

Log in, change it in Settings, or delete the account in **Admin → Team**. A red
banner sits on the admin dashboard until you do, and the deploy log shouts about
it on every start.

### 2. Email — this is the big one

**Admin → Setup → Email.** A Gmail address and an
[App Password](https://myaccount.google.com/apppasswords) (not your normal
password — you need 2-step verification on first).

Nothing sends until this is done. Not the free-lead emails, not password resets,
not job alerts, not the nudges. Every one of those is queued and going nowhere.

Send yourself the test email from that page before you believe it.

**One thing to know:** a free Gmail sends about 500 a day over SMTP, and going
past it locks the mailbox for 24 hours — which would take password resets down
with it. Level stops at 400 and spends at most 240 of those on free leads. You
can watch the day's count on that same page. Once you're sending from your own
domain, raise `MAIL_DAILY_CAP`.

### 3. A domain

Buy one, point it at Render, then put it in **Admin → Setup → Your web address**.
`leveltrades.co.nz` was free when this was last checked.

Emails from a real domain get opened. Emails from `level-wcyc.onrender.com` get
binned, and every link in every message uses this setting.

### 4. Text messages — optional, worth it

**Admin → Setup → Text messages.** Twilio SID, token and number.

Turns on: a text the moment a matching job appears (the single biggest thing for
speed-to-quote), and phone confirmation on a posted job, which holds a job back
until the poster confirms their number. Without it, neither exists.

### 5. The AI helper — optional

**Admin → Setup.** An Anthropic key turns on two things: "Help me describe it"
on the job form, and the check that reads a job before it goes out and says
whether it's pointed at the right trade. With no key, jobs are distributed
exactly as they always were and nothing is blocked.

### 6. Docket — two settings, then it's automatic

**In Docket:** set `LEVEL_PLATFORM_KEY` in the environment to a long random
string (24+ characters — `openssl rand -base64 32` will do it).

**In Level:** **Admin → Setup → Docket**, the Docket address and that same key.

That's it, for everybody, forever. Any tradie who signs up to Docket with the
same email address they use on Level will find their won jobs in it, and they
set up nothing. A tradie who doesn't use Docket never notices.

### 7. Download a backup

**Admin → Setup.** Press it once so you know where the button is. It writes one
automatically every night and keeps a fortnight; the button is for the day you
want one in your hand.

### 8. Post one job yourself

From a friend's address, and walk it all the way through. Watch it arrive, quote
on it from a second account, accept the quote, post a progress update. Ten
minutes, and you'll find anything that feels wrong before a stranger does.

---

## Part two — week one: get jobs, not tradies

**The trap:** emailing 400 tradies first. They sign up, see nothing, and never
come back. A tradie who sees an empty board once is hard to win twice.

- **Ask ten people you know** who have a job they've been putting off — a leaking
  tap, a fence panel, a deck that wants a coat. Post them, or send them the link.
  Ten real jobs is enough to start.
- **Print the homeowner flyer** (Admin → Outreach → Homeowner flyer) and put it
  where people who own houses look: the dairy noticeboard, the hardware store,
  the school newsletter. Wellington suburbs with old housing stock are the best
  bet — Karori, Berhampore, Island Bay, Newtown, Petone.
- **Post in the local Facebook groups** using the wording in the spreadsheet's
  "Message templates" tab. Check each group's rules first; many only allow
  business posts on a set day.
- **Neighbourly went offline** in late September 2026. Everyone who advertised
  locally there is looking for somewhere else. That's an opening, and it closes
  when its replacement launches.

**Target: 10 live jobs. Not 100.**

---

## Week two — the tradies come to the jobs

Most of this now happens without you.

**What runs on its own:** a job that can't fill its fifteen slots emails the
matching businesses who aren't on Level yet — up to three rounds, six hours
apart, reaching wider across the region each time. It stops when the slots fill,
the job closes, or the list runs dry. Three emails per business, ever, and an
opt-out is permanent.

**What you still do by hand:** Admin → Outreach → pick a job → **Send leads**,
when you want to push one particular job at one particular set of businesses.

**Rules to hold to:**
- Never invent a job to fill a quiet week. That's a Fair Trading problem, and
  tradies talk to each other.
- If someone replies "unsubscribe", mark it the same day.
- Watch the opt-out rate on **Admin → Numbers**. Above about 5% means the email
  or the list is wrong — stop and look.

**And check the tickets.** Tradies can upload Site Safe cards, first aid
certificates, insurance and memberships, and each one you confirm lifts their
score and shows on their profile. They sit waiting until you look at them.
Admin → the trade → Tickets.

---

## What to watch, and what it means

**Admin → Numbers**, every few days.

| Number | Good sign | If it's bad |
|---|---|---|
| Jobs that got a quote | Above 80% | Too few tradies in that trade or area. The top-up handles it; check it's actually sending |
| Hours to first quote | Under 4 | Tradies aren't seeing it. Check text alerts are on |
| Offers that lapsed | Under 40% | Tradies are ignoring jobs. Ring two and ask — usually the wrong trade or area |
| Hire rate | Above 40% | Quotes are too dear, too slow, or too vague. Read a few |
| Opt-out rate on outreach | Under 5% | The email reads like spam, or the list is off |
| "Short jobs with nobody left to ask" | 0 | The prospect list has run dry for that trade and area — go and find more businesses |
| Trust: middle score | Rising | A low one usually means checks are waiting on *you*, not that the trades are bad |
| Repeat customers | Any at all | The first repeat customer is the real proof it works |

**No competitor publishes a hire rate.** Once yours is steady over a few dozen
jobs, publishing it becomes something nobody else can copy without exposing
their own.

---

## Month one

- **Ask the first five customers what nearly stopped them.** Not "was it good" —
  "what made you hesitate". That's the list of what to fix.
- **Ring the first five tradies.** Ask what they'd pay, and what would make them
  cancel.
- **Get the legal pack answered** (`docs/LEGAL_REVIEW.md`) before spending on
  advertising. The patent question and the name clash both want an answer while
  changing course is cheap.
- **Check the 90-day date on the free database** and decide whether to pay for it
  or move.

---

## When to start charging

Not until a tradie has won work they'd have missed. Their first invoice through
Level is the argument for the subscription; anything earlier is asking them to
buy a promise.

When you do:
- set `FREE_PILOT=0` in Render;
- the no-job guarantee switches itself on, and refunds happen automatically;
- tell every trade a fortnight beforehand, in plain words, with the date.

---

## What is *not* ready

Be clear-eyed about these.

- **The phone app can't ship.** It needs an Apple Developer account ($149/yr) and
  a Google Play one ($25 one-off), and `eas.json` still has an empty `projectId`.
- **Android has never been built or run.** The app has only ever been tested in
  the iOS Simulator. That's half the market, untested.
- **Stripe is wired but off.** Nothing can be charged until the keys are in and
  `FREE_PILOT=0`.
- **The legal questions are open** — NZ patent 594581, and the `level.co.nz` name
  clash.

Nothing in Part Two needs another line of code. It needs ten real jobs.
