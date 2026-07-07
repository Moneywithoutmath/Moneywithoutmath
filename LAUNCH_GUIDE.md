# Launch Guide — from repos to revenue

Everything built so far lives on branch `claude/media-generation-contract-w9q001`
in three repos. This guide is the exact sequence to take it live. Steps are
ordered by dependency — do them top to bottom. Estimated total time: 2–3 hours.

**The stack you are launching:**

```
User ──► creator-commerce-hub (/studio, /pricing, credits meter)
              │  Supabase session JWT
              ▼
        generate-media edge function ── Claude (claude-opus-4-8) tool loop
              │  quota check (atomic, credit-weighted)      │
              │  Stripe plan from subscriptions table       ▼
              ▼                                    adapter service (Fly.io)
        media_generation_usage table                        │
                                                            ▼
                                                   Replicate (flux-schnell, …)
```

---

## Step 0 — Accounts and tools

You need accounts on: **Supabase** (database + auth + edge functions),
**Stripe** (billing), **Replicate** (generation models), **Anthropic**
(Claude API), **Fly.io** (adapter hosting), and a static host for the app
(**Cloudflare Pages**, Netlify, or Vercel — any of them; the app builds to
plain static files).

Install the CLIs locally:

```sh
# Supabase CLI
brew install supabase/tap/supabase   # or: npm i -g supabase
# Fly CLI
curl -L https://fly.io/install.sh | sh
# Bun (builds the app, runs the adapter locally)
curl -fsSL https://bun.sh/install | bash
```

## Step 1 — Merge the three branches

Each repo has one branch with all the work. Open and merge a PR for each
(or merge directly if you're solo):

| Repo | Branch | Contains |
| --- | --- | --- |
| `Moneywithoutmath/Moneywithoutmath` | `claude/media-generation-contract-w9q001` | Contract v1.1.0, adapter service, README, LICENSE, this guide |
| `Moneywithoutmath/creator-commerce-hub` | same | Edge function, migrations, studio, pricing, meter, lint fixes |
| `Moneywithoutmath/morphic-ai-answer-engine-generative-ui` | same | generateMedia tool + inline rendering |

```sh
# fastest path, per repo:
git checkout main
git merge claude/media-generation-contract-w9q001
git push origin main
```

## Step 2 — Supabase project

### 2.1 Create the project and capture keys

Create a project at [supabase.com/dashboard](https://supabase.com/dashboard).
From **Project Settings → API**, note four values:

- `Project URL` → this is `VITE_SUPABASE_URL` and `SUPABASE_URL`
- `anon` key → `VITE_SUPABASE_ANON_KEY`
- `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` (server-side only — never in the frontend)
- Project ref (the subdomain, e.g. `abcd1234`)

### 2.2 Apply the migrations

From the creator-commerce-hub repo root:

```sh
supabase login
supabase link --project-ref <your-project-ref>
supabase db push
```

This creates `media_generation_usage` (with RLS) and the
`charge_media_generation` atomic-charge function.

**The `subscriptions` table** predates the repo's migrations folder (it was
created by the Blink platform). If `supabase db push` doesn't create it and
the table doesn't exist yet, run this in the SQL editor:

```sql
create table if not exists public.subscriptions (
  id text primary key,
  user_id text not null unique,
  stripe_customer_id text,
  stripe_subscription_id text,
  price_id text,
  plan text not null default 'free',
  status text not null default 'inactive',
  current_period_end timestamptz,
  updated_at timestamptz not null default now()
);
create index if not exists subscriptions_customer_idx
  on public.subscriptions (stripe_customer_id);
alter table public.subscriptions enable row level security;
create policy "Users can read own subscription"
  on public.subscriptions for select
  using (auth.uid()::text = user_id);
```

### 2.3 Enable email sign-in

**Authentication → Providers → Email**: enable. Keep "Confirm email" on —
magic links double as confirmation.

**Authentication → URL Configuration**:
- Site URL: `https://yourdomain.com`
- Redirect URLs: add `https://yourdomain.com/studio` and
  `http://localhost:5173/studio` (local dev)

## Step 3 — Deploy the adapter (the generation backend)

The adapter lives in this repo under `adapter/` with a `Dockerfile` and
`fly.toml` already in place.

### 3.1 Get provider credentials

- Replicate API token: [replicate.com/account/api-tokens](https://replicate.com/account/api-tokens)
- Mint a shared secret the edge function will use to call the adapter:

```sh
openssl rand -hex 32   # save this — it is MEDIA_ADAPTER_API_KEY
```

### 3.2 Deploy

```sh
cd adapter
fly launch --copy-config --no-deploy    # accept the app name or pick one
fly secrets set \
  REPLICATE_API_TOKEN=r8_your_token \
  MEDIA_ADAPTER_API_KEY=<the hex secret>
fly deploy
```

### 3.3 Smoke test

```sh
ADAPTER_URL=https://<your-app>.fly.dev
KEY=<the hex secret>

# should return {"status":"success","assets":[{"url":"https://replicate.delivery/..."}]}
curl -s -X POST "$ADAPTER_URL" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"media_type":"image","prompt":"a ceramic mug on a sunlit table","format":"png"}'

# should return a structured invalid_request error
curl -s -X POST "$ADAPTER_URL" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"media_type":"image","prompt":"x","format":"mp4"}'
```

**Cost guardrail (do this now, not later):** set a spend limit in
Replicate → Account → Billing. Start at $50/month; raise it when revenue
justifies it. This is your hard floor against abuse bugs.

Video/audio are opt-in. When you want them, pick models on Replicate and:

```sh
fly secrets set \
  REPLICATE_VIDEO_MODEL=owner/model-name \
  REPLICATE_AUDIO_MODEL=owner/model-name
```

Then re-check `PRICING.md` weights against those models' prices.

## Step 4 — Deploy the edge functions

From the creator-commerce-hub repo root:

```sh
# secrets shared by the functions
supabase secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  MEDIA_GENERATION_API_URL=https://<your-app>.fly.dev \
  MEDIA_GENERATION_API_KEY=<the hex secret> \
  MEDIA_UPGRADE_URL=https://yourdomain.com/pricing \
  STRIPE_SECRET_KEY=sk_live_... \
  STRIPE_WEBHOOK_SECRET=whsec_placeholder_until_step_5

# deploy
supabase functions deploy generate-media
supabase functions deploy create-checkout
supabase functions deploy stripe-webhook --no-verify-jwt
```

Two things that will bite you if skipped:

- **`--no-verify-jwt` on stripe-webhook only.** Stripe's servers don't carry
  a Supabase JWT; that function verifies the Stripe signature instead.
  `generate-media` and `create-checkout` keep JWT verification ON.
- **Anthropic spend cap:** set a monthly usage limit in the Anthropic
  console (Settings → Limits). Each generation request costs ~$0.01–0.05 in
  Claude tokens for the tool loop.

Optional pricing knobs (defaults shown, all documented in `PRICING.md`):

```sh
supabase secrets set \
  FREE_TIER_MONTHLY_CREDITS=25 PRO_TIER_MONTHLY_CREDITS=1000 \
  CREDIT_WEIGHT_IMAGE=1 CREDIT_WEIGHT_AUDIO=5 CREDIT_WEIGHT_VIDEO=25
```

## Step 5 — Stripe

### 5.1 Product and price

Stripe Dashboard → **Product catalog → Add product**:
- Name: `Pro`, recurring **$29/month**
- Copy the price ID (`price_...`) — it becomes `VITE_STRIPE_PRICE_ID` in Step 6.

($29 is the margin-safe price from `PRICING.md` — profitable even if a pro
user spends all 1000 credits on video. If you'd rather launch at $19, raise
`CREDIT_WEIGHT_VIDEO` to 40 first.)

### 5.2 Webhook

**Developers → Webhooks → Add endpoint**:
- URL: `https://<project-ref>.supabase.co/functions/v1/stripe-webhook`
- Events: `checkout.session.completed`, `customer.subscription.created`,
  `customer.subscription.updated`, `customer.subscription.deleted`,
  `invoice.payment_failed`, `invoice.payment_succeeded`
- Copy the signing secret and replace the placeholder:

```sh
supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_the_real_one
supabase functions deploy stripe-webhook --no-verify-jwt   # redeploy to pick it up
```

Test it: Dashboard → the endpoint → **Send test event** →
`checkout.session.completed` should return 200.

## Step 6 — Deploy the app

From the creator-commerce-hub repo root, with these environment variables
set in your host's build settings (Cloudflare Pages / Netlify / Vercel):

```
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
VITE_STRIPE_PRICE_ID=price_...
```

Build command: `bun install && bun run build` — output directory: `dist`.

Local verification first:

```sh
VITE_SUPABASE_URL=... VITE_SUPABASE_ANON_KEY=... VITE_STRIPE_PRICE_ID=... \
  bun run build && bun run preview
# open http://localhost:4173/studio and /pricing
```

Point your domain at the deployment, and make sure it matches the Site URL
you set in Supabase auth (Step 2.3) and `MEDIA_UPGRADE_URL` (Step 4).

## Step 7 — (Optional) Light up morphic

If you also run the morphic answer engine, add to its `.env.local`:

```
MEDIA_GENERATION_API_URL=https://<your-app>.fly.dev
MEDIA_GENERATION_API_KEY=<the hex secret>
```

The researcher agent then offers `generateMedia` in adaptive mode and renders
results inline. Note morphic has **no quota layer** — it calls the adapter
directly, so only run it with trusted users or put the metered edge function
between them (point `MEDIA_GENERATION_API_URL` at a wrapper instead).

## Step 8 — Publish the contract

1. GitHub → `Moneywithoutmath/Moneywithoutmath` → Settings → General →
   Danger Zone → **Change visibility → Public**.
2. Tag the release:

```sh
git tag v1.1.0 && git push origin v1.1.0
```

3. Create a GitHub Release from the tag; paste the contract's changelog table.

Every builder who adopts the contract is a prospective customer for your
hosted backend — that's the distribution flywheel.

## Step 9 — End-to-end verification

Run through this list in order; each item catches a different wiring mistake.

1. **Adapter direct** (Step 3.3 curls) — proves Replicate + auth.
2. **Sign-in**: open `/studio`, request a magic link, confirm the email
   arrives and lands you back signed in. *(Failure → Step 2.3 redirect URLs.)*
3. **Meter loads**: shows `25 of 25 credits left · FREE`. *(Failure → edge
   function logs: `supabase functions logs generate-media`.)*
4. **Generate an image**: prompt "a product hero shot of a ceramic mug".
   Asset renders, meter drops to 24. *(Failure → check
   `MEDIA_GENERATION_API_URL`/`_KEY` secrets.)*
5. **Quota**: set `FREE_TIER_MONTHLY_CREDITS=2`, redeploy, generate twice —
   third attempt shows the upgrade banner linking to `/pricing`. Set it back.
6. **Checkout**: on `/pricing` click Upgrade → Stripe checkout (test mode) →
   pay with `4242 4242 4242 4242` → redirected to `/studio?upgraded=1` →
   meter shows `PRO` and 1000 credits. *(Failure → webhook logs in Stripe
   dashboard + `supabase functions logs stripe-webhook`.)*
7. **Failed payment safety**: in Stripe test mode, send an
   `invoice.payment_failed` test event — the subscriptions row must flip to
   `plan='free'`.

## Step 10 — Operate it sustainably

**Weekly (5 minutes)** — revenue vs. cost pulse, in the Supabase SQL editor:

```sql
-- credits consumed this month by plan
select coalesce(s.plan, 'free') as plan,
       count(distinct u.user_id) as users,
       sum(u.asset_count)        as credits,
       sum(u.asset_count) filter (where u.media_type = 'video') as video_credits
from media_generation_usage u
left join subscriptions s on s.user_id = u.user_id
where u.created_at >= date_trunc('month', now())
group by 1;
```

Sanity rule from `PRICING.md`: monthly Replicate spend should stay under
~35% of Stripe MRR. If it creeps up, raise `CREDIT_WEIGHT_VIDEO` or the pro
price — both are env changes, no deploy.

**Monthly:**
- Check Replicate/Anthropic spend against their caps; raise caps only with revenue.
- `supabase functions logs generate-media | grep -i error` — investigate anything recurring.
- Review free→pro conversion (Stripe) and tune `FREE_TIER_MONTHLY_CREDITS`:
  too high and nobody upgrades, too low and nobody sticks. 25 is a starting
  point, not gospel.

**When you change generation models:** re-derive credit weights so
`weight x $/credit >= backend cost per asset` (table in `PRICING.md`).

**Key rotation (quarterly or on any suspicion):** rotate
`MEDIA_ADAPTER_API_KEY` (one `openssl rand`, set on both Fly and Supabase),
Replicate token, and Anthropic key. All are single-point env swaps.

**Backups:** Supabase Pro plan includes daily backups — worth it the day you
have paying users, since `subscriptions` + `media_generation_usage` are your
billing records.

## What to build next (in order of return)

1. **Overage packs** — the design is in `PRICING.md`; needs one migration,
   one Stripe one-time price, and ~30 lines in the webhook. Captures revenue
   from pro users who hit 1000 credits instead of rate-limiting your best
   customers.
2. **Asset persistence** — Replicate URLs expire (~1 hour). Copy generated
   files to Supabase Storage in the edge function and return your own URLs;
   this also makes user galleries possible.
3. **Annual plan** — a second Stripe price ($290/year) and one more entry on
   the pricing page; the webhook already handles it.
4. **A customer-facing status/usage API** — expose `GET usage` history so
   power users can track spend; it's one more branch in the edge function.
