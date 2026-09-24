# The first fortnight

A marketplace fails when one side shows up and the other doesn't. Level's harder side is
**jobs**, not tradies: you have 492 tradie contacts ready to email, and zero homeowners.
So the plan below spends most of its effort on getting real jobs posted, and only invites
tradies once there's something for them to quote on.

Everything here uses what's already built. Nothing needs more code.

---

## Before day one (about an hour)

1. **Turn on email.** Admin → Setup → a Gmail app password. Nothing sends without it: no
   outreach, no password resets, no job alerts. Send yourself the test email.
2. **Buy a domain** and put it in Setup → "Your web address" once it points at Render.
   `leveltrades.co.nz` is free. Emails from a real domain get opened; emails from
   `level-wcyc.onrender.com` get binned.
3. **Download a backup** (Admin → Setup) so you know where the button is.
4. **Post one job yourself**, from a friend's address, and walk through it end to end.
   Watch it arrive, quote on it from a second account, accept the quote. Ten minutes, and
   you'll find anything that feels wrong before a stranger does.

---

## Week one — get jobs, not tradies

**The trap:** emailing 400 tradies first. They sign up, see nothing, and never come back.
A tradie who sees an empty board once is hard to win twice.

**Do this instead:**

- **Ask ten people you know** who have a job they've been putting off — a leaking tap, a
  fence panel, a deck that needs a coat. Post them, or send them the link. Ten real jobs
  is enough to start.
- **Print the homeowner flyer** (Admin → Outreach → Homeowner flyer) and put it where
  people who own houses look: the dairy noticeboard, the hardware store, the community
  centre, the school newsletter. Wellington suburbs with old housing stock are the best
  bet — Karori, Berhampore, Island Bay, Newtown, Petone.
- **Post in the local Facebook groups** using the wording in the spreadsheet's "Message
  templates" tab. Check each group's rules first; many only allow business posts on a set
  day.
- **Neighbourly is offline** as of late September 2026. Everyone who advertised locally
  there is looking for somewhere else — that's an opening, and it closes when its
  replacement launches.

**Target:** 10 live jobs. Not 100.

---

## Week two — bring in the tradies, job by job

Now the outreach list earns its keep. For each real job:

1. Admin → Outreach → the job → **Send leads**.
2. It goes to the matching businesses in that trade and area — each gets their own email,
   with their own sign-up link and unsubscribe.
3. Anyone who joins lands on a board with an actual job on it.

**Rules to hold to:**
- One email per real job. Never invent a job to fill a quiet week — that's a Fair Trading
  problem, and tradies talk to each other.
- Stop at three unanswered emails. The system enforces this.
- If someone replies "unsubscribe", mark it the same day.
- Watch the opt-out rate on Admin → Numbers. Above about 5% means the email or the list is
  wrong — stop and look.

---

## What to watch, and what it means

Admin → **Numbers**, every few days:

| Number | Good sign | If it's bad |
|---|---|---|
| Jobs that got a quote | Above 80% | Too few tradies in that trade or area. Send leads for that job. |
| Hours to first quote | Under 4 | Tradies aren't seeing it. Check text alerts are on. |
| Offers that lapsed | Under 40% | Tradies are ignoring jobs. Ring two and ask why — it's usually the wrong trade or area. |
| Hire rate | Above 40% | Quotes are too dear, too slow, or too vague. Read a few. |
| Opt-out rate on outreach | Under 5% | The email reads like spam, or the list is off. |
| Repeat customers | Any at all | The first repeat customer is the real proof it works. |

**No competitor publishes a hire rate.** Once yours is steady over a few dozen jobs, publishing
it becomes a thing nobody else can copy without exposing their own.

---

## Month one

- **Ask the first five customers what nearly stopped them.** Not "was it good" — "what made
  you hesitate". That's the list of what to fix.
- **Ring the first five tradies.** Ask what they'd pay, and what would make them cancel.
- **Get the legal pack answered** (`docs/LEGAL_REVIEW.md`) before spending on advertising.
  The patent question and the name clash both want an answer while changing course is cheap.
- **Check the 90-day date on the free database** and decide whether to pay for it or move.

---

## When to start charging

Not until a tradie has won work they'd have missed. Their first invoice through Level is
the argument for the subscription; anything earlier is asking them to buy a promise.

When you do:
- set `FREE_PILOT=0` in Render;
- the no-job guarantee switches itself on, and refunds happen automatically;
- tell every trade a fortnight beforehand, in plain words, with the date.

Nothing in this plan needs another line of code. It needs ten real jobs.
