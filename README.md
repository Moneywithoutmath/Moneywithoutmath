# Media Generation Contract

A Claude-native contract for image, video, and audio generation through
Anthropic's tool-calling (`tool_use`) format — with usage metering and
monetization semantics built in.

Any app that gives Claude the [`generate_media` tool](media-generation-tool.json)
can talk to any backend that implements the contract. Executors can meter
credits, and when a user runs out, Claude itself relays the upgrade path.

## What's here

| Path | What it is |
| --- | --- |
| [`MEDIA_GENERATION_CONTRACT.md`](MEDIA_GENERATION_CONTRACT.md) | The contract (v1.1.0): request/response shapes, structured error codes, usage metering, versioning rules. |
| [`media-generation-tool.json`](media-generation-tool.json) | The strict tool definition to drop into your `tools` array. |
| [`adapter/`](adapter/) | A reference backend (Bun, zero dependencies) with Replicate and mock drivers. Point any integration's `MEDIA_GENERATION_API_URL` at it. |

## Quickstart

Run the reference backend (mock driver — no API keys, no cost):

```sh
cd adapter
bun run server.ts
# media-generation-adapter listening on :8787 (provider: mock)
```

Generate for real with Replicate:

```sh
REPLICATE_API_TOKEN=r8_... bun run server.ts
```

Give Claude the tool and execute its calls against the backend — the full
loop is in the [contract's worked example](MEDIA_GENERATION_CONTRACT.md#end-to-end-example-python).

## Why a contract?

- **Claude-native.** Built for `tool_use`/`tool_result`, `strict: true`
  schemas, and error messages Claude can act on — retry with adjusted
  parameters, or stop and tell the user to upgrade.
- **Monetization-aware.** Success payloads carry `usage`
  (credits charged/remaining, plan); `quota_exceeded` errors carry an
  `upgrade_url`. Metering is part of the wire format, not an afterthought.
- **Swappable backends.** Clients and executors upgrade independently under
  the versioning rules — one integration works with any compliant provider.

## Reference integrations

- [morphic](https://github.com/Moneywithoutmath/morphic-ai-answer-engine-generative-ui) —
  the contract as a Vercel AI SDK tool on a research agent, with inline
  media rendering and billing state in the UI.
- [creator-commerce-hub](https://github.com/Moneywithoutmath/creator-commerce-hub) —
  a Supabase edge function running the full tool-use loop behind
  Stripe-gated, credit-weighted monthly quotas.

## License

[MIT](LICENSE)
