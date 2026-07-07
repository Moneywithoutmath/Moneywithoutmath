# PromptClip — Operator's Launch Guide

Everything left to do is on *your* side, because it needs your accounts and
your identity. This guide is ordered: finish each phase before starting the
next. Times are realistic; nothing here requires programming beyond copying
the snippets.

**Total to first paying customer: roughly one working day plus Stripe's
verification wait.**

---

## Phase 0 — Accounts you need (≈30 min, mostly signups)

| Account | Why | Cost |
|---|---|---|
| [fly.io](https://fly.io) | hosting | free tier fine to start; ~$5–10/mo after |
| [Stripe](https://stripe.com) | payments | free; 2.9% + $0.30 per charge |
| [Replicate](https://replicate.com) | the real AI video model | pay-per-render (see Phase 3) |
| Domain registrar (Namecheap, Cloudflare…) | your domain | ~$10/yr |
| [UptimeRobot](https://uptimerobot.com) | free monitoring | free |

Stripe requires business/identity verification before live payouts — start
that first, it can take 1–2 days, and do the rest while you wait.

---

## Phase 1 — Deploy the app (≈30 min)

Install the fly CLI and launch from the repo root:

```bash
# 1. install flyctl
curl -L https://fly.io/install.sh | sh

# 2. log in
fly auth login

# 3. from the repo root — uses the fly.toml already in the repo
fly launch --copy-config --no-deploy
# pick an app name (this becomes promptclip.fly.dev) and region near your users

# 4. create the persistent volume (database + rendered videos live here)
fly volumes create promptclip_data --size 10

# 5. set the two env vars that point the app at the volume
fly secrets set DB_PATH=/srv/data/promptclip.db MEDIA_DIR=/srv/data/media

# 6. set your admin token — generate a strong one, save it in a password manager
fly secrets set ADMIN_TOKEN=$(openssl rand -hex 24)

# 7. deploy
fly deploy
```

Verify: open `https://<your-app>.fly.dev/healthz` → `{"ok":true}`, then the
landing page, sign up, and render a test video (it will use the placeholder
renderer for now — that's expected until Phase 3).

Dashboard: `https://<your-app>.fly.dev/admin`, paste your ADMIN_TOKEN.

---

## Phase 2 — Turn on payments (≈45 min + verification wait)

### 2.1 Stripe keys

Stripe Dashboard → Developers → API keys → copy the **secret key** (`sk_live_…`;
use `sk_test_…` first to rehearse).

```bash
fly secrets set STRIPE_SECRET_KEY=sk_live_...
fly secrets set CHECKOUT_SUCCESS_URL=https://<your-app>.fly.dev/?paid=1
fly secrets set CHECKOUT_CANCEL_URL=https://<your-app>.fly.dev/
```

### 2.2 Webhook (CRITICAL — do not skip)

Stripe Dashboard → Developers → Webhooks → **Add endpoint**:

- URL: `https://<your-app>.fly.dev/api/stripe/webhook`
- Events: `checkout.session.completed` and `invoice.paid`

Copy the signing secret (`whsec_…`) it shows you:

```bash
fly secrets set STRIPE_WEBHOOK_SECRET=whsec_...
```

> Without `STRIPE_WEBHOOK_SECRET` the app accepts unsigned webhooks — fine in
> dev, an unlimited-free-credits hole in production. The code refuses nothing
> without it, so treat this as part of "payments on", not an optional extra.

### 2.3 Rehearse with test mode

With `sk_test_...` set, buy a pack with Stripe's test card
`4242 4242 4242 4242` (any future date, any CVC). Confirm:

1. Checkout completes and redirects back;
2. credits appear on the account (`/api/me`);
3. the payment shows in `/admin` revenue;
4. Stripe Dashboard → Webhooks shows the event delivered with `200`.

Then swap to live keys and repeat once with a real card for $5 (refund
yourself after).

---

## Phase 3 — Swap in the real AI renderer (≈30 min)

This is the switch from demo to product.

1. Sign up at replicate.com → Account → API tokens → create one.
2. Pick a text-to-video model. Browse https://replicate.com/collections/text-to-video.
   Practical picks as of mid-2026 (check current pricing on each model page):
   - budget/fast: a WAN-family or LTX-family model (~$0.05–0.15 per short clip)
   - quality: minimax/video-01 or kling-family (~$0.25–0.50 per clip)
3. On the model page, copy the full version string (`owner/name:versionhash`).

```bash
fly secrets set RENDER_PROVIDER=replicate \
  REPLICATE_API_TOKEN=r8_... \
  REPLICATE_MODEL=owner/name:versionhash
```

4. Render one test video and check the output quality yourself.

### 3.1 Check your margin (the only spreadsheet you need)

Your smallest pack sells 20 credits for $5 → **$0.25/credit**. Your largest
sells 500 for $79 → **$0.158/credit**. A draft 5-second render costs 1 credit.

Rule: **provider cost per render must stay below your worst (largest-pack)
credit price.** If your chosen model costs $0.20/render, you're fine on the
starter pack and *underwater* on the studio pack. Two levers, both one-line
edits in `app/main.py`:

- raise pack prices in `CREDIT_PACKS`
- raise the per-render cost in `QUALITY_MULTIPLIER` / `render_cost()`

Redeploy after editing: `git commit -am "pricing" && fly deploy`.

> Note: some Replicate models take fixed input names that differ from
> `prompt/width/height/num_frames`. If your chosen model errors, compare its
> API schema on the model page with `ReplicateProvider.render()` in
> `app/providers.py` and rename the input keys to match. It's a 5-line edit.

---

## Phase 4 — Domain, branding, legal (≈1–2 h)

### 4.1 Domain

```bash
fly certs add promptclip.yourdomain.com
# then at your registrar add the CNAME fly shows you, wait for the cert
fly secrets set CHECKOUT_SUCCESS_URL=https://promptclip.yourdomain.com/?paid=1 \
  CHECKOUT_CANCEL_URL=https://promptclip.yourdomain.com/
```

Update the Stripe webhook endpoint URL to the new domain too.

### 4.2 Watermark = your ad

```bash
fly secrets set WATERMARK_TEXT="promptclip.yourdomain.com"
```

Every free-tier video now carries your URL — this is your main organic
acquisition channel, make it the real domain.

### 4.3 Legal minimum

Before charging real money you need a Terms of Service and Privacy Policy
page (Stripe checks for them on business review, and the EU/UK require them).
Fastest route: a generator like getterms.io or termly, save the output as
`app/terms.html` / `app/privacy.html`, and add two routes to `app/main.py`:

```python
@app.get("/terms", response_class=HTMLResponse)
def terms():
    return (ROOT / "app" / "terms.html").read_text()

@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return (ROOT / "app" / "privacy.html").read_text()
```

Also decide your content policy (the render prompt goes to Replicate, whose
own terms ban abuse material — link their policy in yours).

---

## Phase 5 — Ops: backups & monitoring (≈30 min, prevents disasters)

### 5.1 Backups — the database IS the business

`promptclip.db` holds every account, credit balance, and payment record.
Losing it means losing your customers' money. Set up a nightly off-site copy.

Simplest robust approach — Litestream (continuous SQLite replication to any
S3-compatible bucket, e.g. Cloudflare R2 which has a free tier):

Add to the `Dockerfile` (before `CMD`):

```dockerfile
ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz /tmp/ls.tar.gz
RUN tar -xzf /tmp/ls.tar.gz -C /usr/local/bin && rm /tmp/ls.tar.gz
COPY litestream.yml /etc/litestream.yml
CMD ["litestream", "replicate", "-exec", "uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Create `litestream.yml` in the repo root:

```yaml
dbs:
  - path: /srv/data/promptclip.db
    replicas:
      - type: s3
        bucket: promptclip-backup
        path: db
        endpoint: ${R2_ENDPOINT}        # e.g. https://<acct>.r2.cloudflarestorage.com
        access-key-id: ${R2_ACCESS_KEY}
        secret-access-key: ${R2_SECRET_KEY}
```

```bash
fly secrets set R2_ENDPOINT=... R2_ACCESS_KEY=... R2_SECRET_KEY=...
fly deploy
```

Test the restore once (`litestream restore`) — a backup you haven't restored
from is a hope, not a backup.

### 5.2 Monitoring

- UptimeRobot: HTTP monitor on `https://yourdomain/healthz`, alert to your
  email/phone. 2 minutes to set up, free.
- `fly logs` for live logs; `fly status` for machine health.
- Check `/admin` daily — MRR, renders, signups. A day of zero renders with
  nonzero signups means the renderer is broken: check `fly logs` for
  `ProviderError`.

### 5.3 Media cleanup (disk fills up)

Rendered videos accumulate on the volume. Add a cron-style cleanup — simplest
is a fly scheduled machine or just this in a weekly `fly ssh console`:

```bash
find /srv/data/media -mtime +30 -delete   # drop renders older than 30 days
```

(State "videos stored 30 days" in your ToS.)

---

## Phase 6 — Launch & growth (ongoing)

### Week 1 — soft launch
1. Render 5–10 genuinely good videos with your best model; post them with the
   watermark visible (TikTok/X/Reels — vertical 9:16 preset).
2. Product Hunt / Hacker News "Show HN" post — lead with the free credits.
3. Personally onboard the first ~20 users; watch `/admin` and `fly logs` for
   anything breaking.

### Week 2+ — the levers, in ROI order
1. **Watch plan mix on `/admin`** — the free bar is your upsell pool. If free
   users render a lot but don't convert, the watermark isn't visible enough or
   pro is priced too high.
2. **Push the referral code** — it's already live (+3 credits both sides on
   first purchase); surface it in every success screen and receipt email.
3. **Add templates weekly** — edit `TEMPLATES` in `app/main.py`; every preset
   is a one-click credit sale. Watch which templates get used and make more
   like them.
4. **API/B2B outreach** — the raw API with `X-Api-Key` is your second product.
   Any app that wants "generate video" without building it is a customer for
   bulk business-plan credits.

### Pricing discipline
- Re-check margin (Phase 3.1) every time the provider changes prices.
- Raise prices before adding features. Prepaid credit buyers are far less
  price-sensitive than subscribers.

---

## Quick reference — every secret in one place

| Secret | Set in | Purpose |
|---|---|---|
| `ADMIN_TOKEN` | Phase 1 | dashboard + metrics auth |
| `DB_PATH`, `MEDIA_DIR` | Phase 1 | persistent volume paths |
| `STRIPE_SECRET_KEY` | Phase 2 | payments |
| `STRIPE_WEBHOOK_SECRET` | Phase 2 | **credit-granting security** |
| `CHECKOUT_SUCCESS_URL`, `CHECKOUT_CANCEL_URL` | Phase 2/4 | Stripe redirects |
| `RENDER_PROVIDER`, `REPLICATE_API_TOKEN`, `REPLICATE_MODEL` | Phase 3 | real AI renders |
| `WATERMARK_TEXT` | Phase 4 | free-tier ad |
| `R2_*` | Phase 5 | backups |
| `CORS_ORIGINS` | when B2B customers need browser calls | CORS allowlist |
| `RENDER_WORKERS` | scale-up | parallel renders (default 2) |

## When you outgrow this setup (good problem)

Signals and responses, in order:
- **Renders queue up** → raise `RENDER_WORKERS`, then `fly scale count 2`.
- **SQLite write contention** (>~50 req/s) → migrate to Postgres (fly
  provides it); the SQL in `app/main.py` is standard and ports directly.
- **Media bandwidth costs** → serve `media/` from R2/S3 + CDN instead of the
  app volume.
- **Chargebacks/fraud** → enable Stripe Radar rules; require 3DS on packs
  above $20.
