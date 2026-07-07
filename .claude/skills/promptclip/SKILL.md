---
name: promptclip
description: Operate, extend, and launch PromptClip — the prompt-to-video micro-SaaS in this repo. Use when the user asks to deploy, configure payments/Stripe, swap render providers, adjust pricing/plans, debug renders or webhooks, check metrics, or continue the launch process.
---

# PromptClip — operating skill

PromptClip is a prompt-to-video micro-SaaS monetized with prepaid credits,
subscriptions, and referrals. This skill is the map of the system; the two
authoritative documents are:

- **`LAUNCH_GUIDE.md`** — the phase-by-phase operator guide (deploy → Stripe →
  real renderer → domain/legal → backups → growth). For any "what do I do
  next / how do I launch / set up X" question, read it first and follow the
  matching phase.
- **`MEDIA_GENERATION_CONTRACT.md`** + `media-generation-tool.json` — the
  Claude tool-use contract the render API implements.

## Architecture (repo map)

| Path | Role |
|---|---|
| `app/main.py` | FastAPI backend: accounts, credits, async job queue, Stripe, admin metrics |
| `app/providers.py` | pluggable render engines: `local` (ffmpeg) / `replicate` |
| `app/index.html` | landing page + generator UI + pricing + referral + library |
| `app/dashboard.html` | `/admin` revenue dashboard (token-gated) |
| `tests/test_api.py` | 12-test pytest suite — run after ANY backend change |
| `Dockerfile`, `fly.toml`, `requirements.txt` | deploy packaging |

## Money model (edit points)

All pricing lives at the top of `app/main.py`:

- `CREDIT_PACKS` — one-time packs (id → credits, USD)
- `PLANS` — subscriptions (id → USD/mo, credits/mo, watermark-free)
- `render_cost()` / `QUALITY_MULTIPLIER` — credits per render:
  1 credit per started 5 s block × draft 1 / standard 2 / high 4
- `FREE_CREDITS`, `REFERRAL_BONUS`, `SIGNUPS_PER_IP_PER_HOUR`, `TEMPLATES`

Margin rule: provider cost per render must stay below the largest-pack
credit price (currently $0.158/credit). Re-check whenever the provider or
pack prices change.

## Invariants — do not break these

1. **Credit debit is atomic** (`UPDATE … WHERE credits >= cost`, check
   rowcount). Never split into read-then-write.
2. **Failed renders refund** (both in the worker and provider errors).
3. **Stripe webhook must verify signatures** in production
   (`STRIPE_WEBHOOK_SECRET`) and stay **idempotent** via the `payments`
   table primary key — a replayed event must credit nothing.
4. **Jobs are owner-scoped** — `/api/jobs/{id}` and `/api/history` filter by
   `api_key`; never return another user's job.
5. **`/media` path traversal guard** stays (resolve + prefix check).
6. **Admin auth** uses `secrets.compare_digest`, never `==`/`!=`.
7. SQLite runs in **WAL mode with busy_timeout** — keep that in `db()`.

After any change to `app/main.py` or `app/providers.py`:
`python3 -m pytest tests/ -v` — all 12 must pass before commit.

## Common operations

- **Run locally**: `uvicorn app.main:app --port 8000`
  (needs `pip install fastapi uvicorn` + `apt-get install ffmpeg`).
- **Dashboard**: `/admin` with `ADMIN_TOKEN` env set; JSON at
  `/api/admin/summary` (header `X-Admin-Token`).
- **Switch renderer**: env `RENDER_PROVIDER=replicate` +
  `REPLICATE_API_TOKEN` + `REPLICATE_MODEL=owner/name:version`. If a model's
  input schema differs, adjust the input dict in
  `ReplicateProvider.render()` — keep the create/poll/download flow.
- **Add a provider**: new class in `app/providers.py` implementing
  `render(params, out_path)`, raise `ProviderError` with a user-safe message
  on failure, register it in `get_provider()`.
- **Deploy**: `fly deploy` (secrets reference table at the bottom of
  `LAUNCH_GUIDE.md`).
- **Demo/seed data for the dashboard**: renders and payments are plain rows
  in `renders`/`payments`; a seeding example exists in the session history —
  write into the tables via `app.main.db()`.

## Debugging quick table

| Symptom | Look at |
|---|---|
| renders stuck in `queued` | worker threads (started at import); `RENDER_WORKERS`; restart re-queues stale jobs |
| render `failed` | job `error` column; `fly logs` for `ProviderError`; credits auto-refunded |
| credits not granted after payment | Stripe webhook delivery + `STRIPE_WEBHOOK_SECRET`; `payments` table for the session/invoice id |
| `database is locked` | a connection opened outside `db()` missing WAL/busy_timeout |
| 402 on generate | working as intended — that's the paywall |
