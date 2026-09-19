# Putting Level online

Two parts: the code goes to GitHub, then Render builds and runs it. Render reads
`render.yaml`, so it creates the website and the database for you.

## 1. GitHub

Create an empty **private** repo at <https://github.com/new> named `level-trades`.
Don't add a README, licence or .gitignore — the code already has them.

Then, from the project folder:

```bash
git remote add origin https://github.com/YOUR-USERNAME/level-trades.git
git push -u origin main
```

## 2. Render

1. Go to <https://dashboard.render.com> → **New** → **Blueprint**.
2. Connect your GitHub account if it asks, then pick the `level-trades` repo.
3. Render reads `render.yaml` and offers to create two things: a web service called
   **level** and a database called **level-db**. Approve it.
4. It asks for the values it can't guess. Fill in:

   | Setting | What to put |
   |---|---|
   | `ADMIN_EMAIL` | The email you'll use to log into the admin area |
   | `ADMIN_PASSWORD` | A long password you haven't used elsewhere |
   | `SUPPORT_EMAIL` | The address customers and trades can write to |
   | `LEGAL_ENTITY` | Your business's legal name, shown on the terms page |
   | `LEGAL_ADDRESS` | Your business address |
   | `SMTP_*`, `MAIL_FROM` | Leave blank until you set up email (see below) |
   | `BASE_URL` | Leave blank. Only needed when you use your own domain |

5. Click **Apply**. The first build takes about five minutes.

### What it costs

- Web service (Starter): **US$7/month**. Needed so the site never sleeps and so job
  photos survive restarts.
- Postgres (Basic 256MB): **about US$6/month**.

### Check it worked

- Open `https://YOUR-SITE.onrender.com/health`. It should say
  `"status": "ok"` and `"database": "postgres"`.
- Open `/demo/login/admin`. It **must** say page not found — demo logins switch
  themselves off in production.
- Log in at `/login` with the admin email and password you set.

## 3. Email

Nothing is emailed until `SMTP_HOST` is set. Add these in Render →
your service → **Environment**, then redeploy:

**Gmail** (quickest): turn on 2-step verification, create an App Password, then set
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`,
`SMTP_PASS=<the app password>`, `MAIL_FROM=Level <you@gmail.com>`.

**Resend** (better for real use): sign up free, verify your domain, then use the
SMTP details they give you and set `MAIL_FROM=Level <hello@yourdomain.co.nz>`.

Test it by using "Forgot your password" on the live site.

## 4. Your own domain

In Render → your service → **Settings** → **Custom Domains**, add your domain and
follow the DNS instructions. Then set `BASE_URL=https://yourdomain.co.nz` so email
links point at it. HTTPS is set up for you.

## 5. Backups

Render's paid Postgres keeps daily backups for 7 days. To take one yourself:

```bash
pg_dump "<the external database URL from Render>" > level-backup.sql
```

## Turning on charging

While `FREE_PILOT=1`, trades get jobs without a card and the refund guarantee is
switched off. When you're ready to charge:

1. In Stripe, create one product with three monthly NZD prices ($30, $50, $90).
2. Add `STRIPE_SECRET_KEY`, `STRIPE_PRICE_SMALL`, `STRIPE_PRICE_MEDIUM`,
   `STRIPE_PRICE_LARGE` in Render.
3. Add a webhook to `https://yourdomain/stripe/webhook` for these events:
   `checkout.session.completed`, `invoice.paid`, `customer.subscription.updated`,
   `customer.subscription.deleted`. Put its signing secret in `STRIPE_WEBHOOK_SECRET`.
4. Test the whole thing with Stripe **test** keys first.
5. Set `FREE_PILOT=0` — and email your trades before their first bill.

## Updating the site later

```bash
git add -A
git commit -m "What changed"
git push
```

Render rebuilds and deploys within a few minutes.
