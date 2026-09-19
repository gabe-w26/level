# Research notes (September 2026)

This summarises three research passes: Builderscrack itself, international lead marketplaces, and NZ regulation. Figures marked *unverified* came from third-party sources.

## Builderscrack today

- **Ownership:** bought by hipages (ASX: HPG) in December 2021 for A$11.8m. It is hipages' NZ business.
- **Scale:** claims about 978k jobs, 16k "verified tradies" and 4.8/5. hipages filings show about **6,400 paying tradies** (H1 FY26).
- **Pricing:**
  - Since June 2025, 100% of NZ tradies are on **subscription plus tokens**.
  - Tokens are spent when the homeowner accepts a connection. They expire after 2–3 months, and there are automatic "overage" charges.
  - Revenue per tradie rose from A$883 (FY22) to A$1,615 (H1 FY26), roughly A$130 a month.
- **Homeowner caps:** a homeowner can connect with up to **3 tradies at a time**. Jobs go out in round-robin batches. The "double handshake" contact flow is patented (NZ #594581).
- **Trust tools:** NZBN, photo ID and live LBP/EWRB/PGDB register checks, and verified-job reviews scored on four dimensions.

### What tradies complain about

Based on Trustpilot reviews at 1 and 2 stars, which are mostly from tradies. The trade app is rated 1.0/5 on iOS.

1. Paying for leads that don't become work: about 40–50% of complaints.
2. Tokens charged when the homeowner withdraws, ghosts or misdescribes the job: about 25%.
3. The 2024–25 switch to tokens, and surprise overage charges: about 20%.
4. Aggressive billing and collections, including Equifax reports and debt-collector threats: about 15%.
5. Hard to get support or refunds; pricing unclear at sign-up.
6. A race to the bottom on price; the review system hurts new tradies.

### What homeowners complain about

1. Unreliable tradies.
2. Deposit theft. RNZ (26 August 2025) reported a builder who passed the checks, had no LBP licence, and took more than $60k.
3. Weak handling of disputes.
4. Confusing terminology.

## How Level answers that

| Pain | Level |
|---|---|
| Paying per lead or token | Flat $30/$50/$90 a month. No tokens, nothing expires |
| Charged for jobs that get pulled | Nothing extra is ever charged per job |
| No work, still paying | The no-job guarantee refunds the month |
| Lock-in and collections | Month to month, one-click cancel, a reminder 7 days before renewal |
| New trades buried by reviews | Rotation ignores reviews. Stars only show from 3 reviews ("New to Level" until then) |
| Customers who ghost | Trades see how often a customer has replied to quotes before they quote |
| Misdescribed or fake jobs | Trades can report a job. Reported quotes don't count toward the guarantee |
| Comparing on price only | Structured quotes (includes, excludes, warranty, start date, GST) and a side-by-side table |
| Unlicensed trades on big jobs | A licence note per category. Badges only show for checked details, with the date |
| $30k+ jobs with no paperwork | Building Act flag: the trade promises the documents and the customer acknowledges before accepting |
| Deposit theft | Guidance on paying against progress. Milestone payments are on the roadmap |
| Phone spam | Trades see the suburb only. Contact details go only to trades the customer chooses |

## International patterns worth knowing

- **Caps work.** Capping at 3 trades per job is the norm at Rated People, hipages and Builderscrack. Uncapped models draw the most anger.
- **"Charge on unlock" is the #1 complaint everywhere**, at Bark, Rated People, hipages and Thumbtack. So are expiring credits and lock-in: Checkatrade, Bark and hipages have all shortened credit life in 2025. Oneflare's credit model ended in 2026 when it closed and was folded into Airtasker.
- **First-to-respond speed races** (hipages' "first 3") are disliked. Level's first-in-first-served cap of 6, with a 24-hour window, is gentler, but it is still a race for the last slots. Keep an eye on quote quality.
- **Where the industry is heading:** AI job briefs (Thumbtack 2026, Yelp), homeowners choosing which trades to contact (Angi 2025), and escrow payments (Airtasker, TradeMatch).
- **Integrations:** Fergus (NZ) has a public API with webhooks. Xero moved to paid developer tiers in March 2026.

## NZ rules that shaped features

- **Building Act Part 4A.** Residential work of $30k or more (incl. GST) needs a written contract, a disclosure statement and the prescribed checklist. There is a 12-month defect repair period, and implied warranties apply to all residential work. → the $30k flag.
- **Fair Trading Act.**
  - "Verified" and "refund" claims must be literally true, with conditions stated.
  - Unfair contract terms rules cover small trade contracts (under $250k).
  - Section 28B requires "in trade" disclosure.
  - → Badges are generated only from checked data, the guarantee terms are published, and profiles show NZBN and "In trade".
- **Privacy Act 2020 / IPP3A** (in force 1 May 2026). Share contact details only with trades the customer chooses. → the contact-sharing model.
- **GST.** Business pricing is normally shown "+ GST". Needs a decision.
- **FSP Act.** Holding customer money is a financial service. → escrow only via Stripe Connect, and not in v1.
- **Registers.**
  - LBP has an official API (MBIE must approve access).
  - EWRB and PGDB have public search but no API.
  - NZBN and the Companies Office have free APIs.

## Key sources

- Builderscrack how it works (trade): https://builderscrack.co.nz/how-it-works/trade
- hipages FY25 results: https://announcements.asx.com.au/asxpdf/20250822/pdf/06n5s617jxwy2l.pdf
- Trustpilot, 1 star: https://nz.trustpilot.com/review/builderscrack.co.nz?stars=1
- RNZ, builder fraud: https://www.rnz.co.nz/news/top/571047/it-really-ruined-me-builder-takes-thousands-of-dollars-then-vanishes
- LBP Part 4A summary: https://www.lbp.govt.nz/for-lbps/skills-maintenance/codewords/123-residential-consumer-rights-and-remedies/
- Unfair contract terms: https://www.comcom.govt.nz/business/your-rights-as-a-business/unfair-contract-terms/
- LBP API: https://portal.api.business.govt.nz/api/lbp
- IPP3A: https://www.privacy.org.nz/resources-and-learning/a-z-topics/ipp3a/
- FTC order against HomeAdvisor: https://www.ftc.gov/news-events/news/press-releases/2023/01/ftc-order-requires-homeadvisor-pay-72-million-stop-deceptively-marketing-its-leads-home-improvement
