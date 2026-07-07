---
name: media-generation-contract
description: >
  Work with the Claude-native media generation contract and its monetized
  stack. Use when the task involves generate_media, MEDIA_GENERATION_API_URL,
  the adapter service, media credits/quotas, the generate-media edge
  function, the studio or pricing pages, or launching/operating the media
  generation business (Replicate, Stripe plans, credit weights, overage
  packs). Covers implementing executors, integrating clients, pricing
  changes, and debugging quota or billing issues.
---

# Media Generation Contract — working guide

One contract, three repos. Read the source docs before changing anything —
this file tells you where truth lives and which invariants must hold.

## The stack

```
clients (morphic tool, creator-commerce-hub studio)
   └─► generate-media edge function (Claude tool loop + credit quotas)
          └─► adapter service (contract executor, Fly.io)
                 └─► Replicate (or mock driver)
```

| Concern | Source of truth |
| --- | --- |
| Wire format, error codes, versioning | `MEDIA_GENERATION_CONTRACT.md` (this repo, v1.1.0) |
| Tool definition Claude receives | `media-generation-tool.json` (this repo) |
| Reference executor + drivers | `adapter/` (this repo; Bun, mock + Replicate) |
| Metered edge function, migrations, studio/pricing UI | repo `creator-commerce-hub` |
| AI-SDK tool + inline rendering | repo `morphic-ai-answer-engine-generative-ui` |
| Unit economics, credit weights, tier design | `creator-commerce-hub/PRICING.md` |
| Deployment + operations runbook | `LAUNCH_GUIDE.md` (this repo) |

## Invariants — do not break these

1. **Contract compatibility is semver.** Additive/optional → minor bump;
   renames, type changes, new required fields → major. Executors ignore
   unknown request fields; clients tolerate extra response fields; unknown
   error codes degrade to `provider_error` handling. Update the changelog
   table in the contract doc with every change.
2. **Identity comes from the Supabase JWT**, never a request body. The
   edge function derives the user via `supabase.auth.getUser(token)`; the
   usage table's RLS keys on `auth.uid()`. Any new endpoint follows suit.
3. **Credits are charged atomically, reserve-before-generate.** All quota
   writes go through the `charge_media_generation` RPC (advisory lock),
   then settle to actual output or refund on failure. Never reintroduce
   read-then-write quota checks.
4. **Credits are weighted by media type** (image 1, audio 5, video 25 by
   default) because backend cost spans ~100x. When generation models
   change, re-derive weights so `weight x $/credit >= backend cost/asset`
   (table in PRICING.md). Weights and tier limits are env vars — pricing
   changes must not require code deploys.
5. **Errors are structured JSON** (`status`, `code`, `message`, and
   `upgrade_url` on `quota_exceeded`) so Claude relays the upgrade path
   instead of retrying. Plain-text errors are legal but worse — don't
   regress structured ones.
6. **Bound every loop and every call**: tool-use loops have iteration caps,
   backend calls have timeouts (callers 120s, adapter polls under 110s),
   prompts have length caps. Anything unbounded against a paid API is a
   cost bug.

## Common tasks

- **Add a generation provider**: new driver in `adapter/providers/`
  returning `ContractSuccess | ContractError`, dispatch in `server.ts`,
  map policy rejections to `content_policy`, add tests mirroring
  `server.test.ts`, then re-check credit weights against the provider's
  pricing.
- **Change pricing/tiers**: env vars on the edge function
  (`FREE/PRO_TIER_MONTHLY_CREDITS`, `CREDIT_WEIGHT_*`) — update PRICING.md
  margin math in the same change. Suggested pro price logic: price >=
  all-video worst case.
- **Wire overage packs**: design is in PRICING.md — relax the
  `asset_count > 0` check constraint, one-time Stripe price, webhook
  inserts a negative usage row. Needs Stripe product IDs.
- **Debug "quota exceeded" wrongly**: check the month-window sum in
  `media_generation_usage`, unreleased reservations (rows never settled or
  refunded — generation crashed mid-flight), and plan resolution from
  `subscriptions` (webhook only sets plan='pro' when healthy).
- **Debug 401s**: caller must send the user's session JWT (not the anon
  key) as the Authorization bearer; the anon key goes in the `apikey`
  header.
- **Launch/deploy anything**: follow `LAUNCH_GUIDE.md` step order — it is
  dependency-sorted and each verification item maps to a failure mode.

## Verification expectations

Before committing: morphic changes must pass `bun typecheck && bun lint &&
bun format:check && bun run test`; creator-commerce-hub must pass
`bun run lint && bun run build`; adapter changes must pass `bun test` in
`adapter/`. Edge functions aren't covered by repo tsc — review the diff and
exercise the function against curl where possible.
