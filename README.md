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

## Going live checklist

1. `pip install stripe`, set `STRIPE_SECRET_KEY`, `CHECKOUT_SUCCESS_URL`,
   `CHECKOUT_CANCEL_URL`; point a Stripe webhook at `/api/stripe/webhook`
   (add signature verification with `STRIPE_WEBHOOK_SECRET`).
2. Swap the ffmpeg placeholder renderer in `app/main.py::generate` for a real
   text-to-video provider (Runway, Luma, Pika, Replicate…). The
   [contract](MEDIA_GENERATION_CONTRACT.md) already defines the interface —
   cost per credit should stay above provider cost per render.
3. Put it behind HTTPS (any PaaS: Fly.io, Railway, Render) with a volume for
   `promptclip.db` and `media/`.
4. Rate-limit `/api/signup` per IP (free-credit farming) and add email
   verification before scaling paid traffic.

## Growth levers (in order of ROI)

1. **Watermark free-tier renders** with your URL — every shared video is an ad.
2. **Referral credits** (+3 for both sides) — cost is marginal, growth is viral.
3. **API reseller tier** — bulk credits at discount for apps embedding you.
4. **Templates gallery** — prompt presets that lower the blank-page barrier and
   raise render volume (each preset click is a credit sale opportunity).

## Repository layout

```
app/main.py                     backend (FastAPI): accounts, credits, renders, Stripe
app/index.html                  landing page + generator UI + pricing
media-generation-tool.json      the Claude tool contract the API implements
MEDIA_GENERATION_CONTRACT.md    request/response spec
examples/generate_video.py      standalone reference executor
```
