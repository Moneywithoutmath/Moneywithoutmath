# PromptClip 🎬 — prompt-to-video micro-SaaS

Turn the [Claude-native media generation contract](MEDIA_GENERATION_CONTRACT.md)
into revenue: a complete, launch-ready web app + API that sells video
generations on a **prepaid credits** model.

## Why this is the maximum-monetization shape

- **Sell generations, not code.** Usage-based credits scale revenue with usage
  and have near-zero marginal cost once a render provider is plugged in.
- **Free tier as funnel.** 3 free credits on signup convert visitors to users
  with zero friction; the paywall (HTTP 402) appears exactly when value is proven.
- **Prepaid, not subscription.** No churn management, no refund exposure on
  unused time, positive cash flow up-front.
- **Two revenue surfaces from one codebase**: the web UI for consumers and the
  raw API (`X-Api-Key`) for developers who embed it — B2C and B2B2C.

## Quick start

```bash
pip install fastapi uvicorn
apt-get install -y ffmpeg          # local render engine
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — sign up, get 3 free credits, render a video.

## API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/signup` | POST | — | Create account, returns API key + 3 credits |
| `/api/me` | GET | `X-Api-Key` | Credits balance |
| `/api/generate` | POST | `X-Api-Key` | Render a video; debits credits |
| `/api/checkout` | POST | `X-Api-Key` | Stripe Checkout session for a credit pack |
| `/api/stripe/webhook` | POST | Stripe | Credits the account after payment |

Pricing engine: **1 credit per started 5 s block × quality multiplier**
(draft ×1, standard ×2, high ×4). Packs: $5/20, $19/100, $79/500 —
gross margin rises with pack size while the per-credit price falls, the
classic prepaid ladder.

## Built-in revenue protection & growth

- **Atomic credit debit** — no double-spend under concurrent requests; failed
  renders auto-refund.
- **Signup abuse controls** — one account per email, 3 signups/IP/hour cap
  (free-credit farming blocked).
- **Stripe webhook hardening** — signature verification via
  `STRIPE_WEBHOOK_SECRET` plus idempotency (replayed events credit nothing).
- **Watermarked free-tier renders** (`WATERMARK_TEXT`) — every shared draft
  video is an ad.
- **Referral program** — +3 credits to both sides when a referred user makes
  their first purchase (paid-gated so it can't be farmed).
- **Template gallery** (`/api/templates`) — lowers the blank-page barrier,
  raises render volume.
- **Admin metrics** (`/api/admin/metrics`, `ADMIN_TOKEN` + `X-Admin-Token`) —
  accounts, renders, credits burned, revenue, paying customers.

## Market-grade architecture

- **Async job pipeline** — `POST /api/generate` returns `202` with a job id;
  poll `GET /api/jobs/{id}`. Renders run on background workers
  (`RENDER_WORKERS`, default 2); interrupted jobs are re-queued on restart;
  failures auto-refund. This is the same UX contract Runway/Pika/Luma expose.
- **Pluggable providers** (`app/providers.py`) — `RENDER_PROVIDER=local`
  (ffmpeg dev renderer) or `replicate` (any text-to-video model on
  replicate.com via `REPLICATE_API_TOKEN` + `REPLICATE_MODEL`). Adding
  Runway/Luma/Pika is one class implementing `render(params, out_path)`.
- **Social formats** — 16:9 YouTube, 9:16 TikTok/Reels, 1:1 feed presets in
  the UI; arbitrary dimensions via API.
- **Per-user library** — `GET /api/history` and an in-app gallery of past
  renders with status and downloads.

## Going live checklist

1. `pip install stripe`, set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `CHECKOUT_SUCCESS_URL`, `CHECKOUT_CANCEL_URL`; point a Stripe webhook at
   `/api/stripe/webhook`. **Never run in production without the webhook
   secret** — an unverified endpoint mints unlimited credits.
2. Swap the ffmpeg placeholder renderer in `app/main.py::generate` for a real
   text-to-video provider (Runway, Luma, Pika, Replicate…). The
   [contract](MEDIA_GENERATION_CONTRACT.md) already defines the interface —
   cost per credit should stay above provider cost per render.
3. Put it behind HTTPS (any PaaS: Fly.io, Railway, Render) with a volume for
   `promptclip.db` and `media/`. Move renders to a background queue (e.g. an
   RQ/Celery worker) once a real provider makes them slower than ~10 s.
4. Set `ADMIN_TOKEN` and `WATERMARK_TEXT` to your domain.
5. At scale: migrate SQLite → Postgres, media → S3/CDN, add email verification.

## Repository layout

```
app/main.py                     backend (FastAPI): accounts, credits, renders, Stripe
app/index.html                  landing page + generator UI + pricing
media-generation-tool.json      the Claude tool contract the API implements
MEDIA_GENERATION_CONTRACT.md    request/response spec
examples/generate_video.py      standalone reference executor
```
