# Media Generation Contract

**Version 1.1.0**

A Claude-native tool contract for requesting image, video, or audio generation
through Anthropic's tool-calling (`tool_use`) format.

## Versioning

The contract follows semantic versioning:

- **Patch** — wording/documentation changes; no wire impact.
- **Minor** — additive, backward-compatible changes: new optional request
  fields, new optional response fields (such as `usage`), new error codes.
- **Major** — breaking changes: removed/renamed fields, changed types,
  new required fields.

Compatibility rules for implementers: executors MUST ignore request fields
they don't recognize, clients MUST tolerate extra fields in responses, and
clients receiving an unknown error `code` SHOULD treat it like
`provider_error`. This lets executors and clients upgrade independently
within a major version.

| Version | Changes |
| ------- | -------- |
| 1.0.0   | Initial contract: `generate_media` tool, asset response, `is_error` results. |
| 1.1.0   | Optional `usage` object on success; structured error payloads with standard codes (`quota_exceeded` + `upgrade_url`, etc.). |

## Files

- `media-generation-tool.json` — the tool definition (`name`, `description`,
  `input_schema`, `strict: true`) to place in the `tools` array of a
  `messages.create()` call.
- This document — the request/response contract and an end-to-end example.

## Request contract

Claude emits a `tool_use` block shaped by `media-generation-tool.json`:

```json
{
  "type": "tool_use",
  "id": "toolu_01ABC...",
  "name": "generate_media",
  "input": {
    "media_type": "image",
    "prompt": "a lighthouse at sunset, watercolor style",
    "format": "png",
    "width": 1024,
    "height": 1024,
    "quality": "high",
    "count": 1
  }
}
```

`media_type` constrains which of `width`/`height` (image, video) and
`duration_seconds` (video, audio) are meaningful; the executing service
should ignore fields that don't apply to the requested `media_type` and may
reject the call with an error result if a required combination is invalid
(e.g. `media_type: "audio"` with a `width` set, or `format: "mp4"` with
`media_type: "image"`).

## Response contract (`tool_result`)

The client executes the generation job and returns a `tool_result` block.
On success, return a JSON payload identifying the produced asset(s):

```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01ABC...",
  "content": [
    {
      "type": "text",
      "text": "{\"status\":\"success\",\"assets\":[{\"url\":\"https://cdn.example.com/gen/abc123.png\",\"format\":\"png\",\"width\":1024,\"height\":1024,\"seed\":482910}]}"
    }
  ]
}
```

On failure, set `is_error: true` and explain what went wrong so Claude can
retry with adjusted parameters:

```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01ABC...",
  "is_error": true,
  "content": [
    { "type": "text", "text": "Generation failed: prompt violates content policy." }
  ]
}
```

### Asset object fields

| Field      | Type   | Notes                                              |
| ---------- | ------ | --------------------------------------------------- |
| `url`      | string | Location of the generated file.                     |
| `format`   | string | Matches the requested `format`.                      |
| `width`    | int    | Present for image/video assets.                      |
| `height`   | int    | Present for image/video assets.                      |
| `duration_seconds` | number | Present for video/audio assets.            |
| `seed`     | int    | Seed actually used, for reproducibility.             |

### Usage object (optional — metered/monetized executors)

Executors that charge for generation SHOULD include a `usage` object in the
success payload so Claude can tell the user where they stand:

```json
{
  "status": "success",
  "assets": [ ... ],
  "usage": { "credits_charged": 1, "credits_remaining": 41, "plan": "pro" }
}
```

| Field               | Type          | Notes                                          |
| ------------------- | ------------- | ----------------------------------------------- |
| `credits_charged`   | int           | Credits consumed by this call.                  |
| `credits_remaining` | int or null   | Credits left in the current period; `null` = unlimited. |
| `plan`              | string        | Billing tier the request executed under.        |

### Structured errors (recommended)

Plain-text `is_error` results remain valid, but executors SHOULD return a
structured JSON error so Claude can react precisely — retry with different
parameters, or direct the user to upgrade:

```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01ABC...",
  "is_error": true,
  "content": [
    {
      "type": "text",
      "text": "{\"status\":\"error\",\"code\":\"quota_exceeded\",\"message\":\"Monthly generation limit reached (5/5 used).\",\"upgrade_url\":\"https://app.example.com/pricing\"}"
    }
  ]
}
```

| `code`            | Meaning                                    | Claude's expected reaction               |
| ----------------- | ------------------------------------------ | ----------------------------------------- |
| `invalid_request` | Bad parameter combination.                 | Correct the parameters and retry.         |
| `content_policy`  | Prompt rejected by the provider's policy.  | Rephrase or inform the user.              |
| `quota_exceeded`  | Billing quota exhausted.                   | Do not retry; tell the user to upgrade at `upgrade_url`. |
| `timeout`         | Generation exceeded the executor timeout.  | Retry with lower `quality`/`duration_seconds`, or inform the user. |
| `provider_error`  | Upstream generation service failed.        | Retry once, then inform the user.         |

`upgrade_url` is only meaningful with `quota_exceeded`; other fields may be
added by executors as long as `status`, `code`, and `message` are present.

## End-to-end example (Python)

Claude may emit several `tool_use` blocks in one response (e.g. an image and
an audio track for the same scene); every matching `tool_result` must be
returned together in a single user message. The loop below handles that and
keeps calling the API until Claude finishes with a normal text response.

```python
import json
from anthropic import Anthropic

client = Anthropic()

with open("media-generation-tool.json") as f:
    media_tool = json.load(f)

def generate_media(input: dict) -> dict:
    # Replace with a call to your actual image/video/audio generation service.
    return {
        "status": "success",
        "assets": [
            {
                "url": "https://cdn.example.com/gen/abc123." + input["format"],
                "format": input["format"],
                "width": input.get("width"),
                "height": input.get("height"),
            }
        ],
    }

messages = [{"role": "user", "content": "Generate a 1024x1024 png of a lighthouse at sunset."}]

MAX_TOOL_ITERATIONS = 5  # bound the loop: generation calls cost real money

for _ in range(MAX_TOOL_ITERATIONS + 1):
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        tools=[media_tool],
        messages=messages,
    )

    if response.stop_reason != "tool_use":
        break

    # Echo the assistant turn, then answer every tool_use block in ONE user message.
    messages.append({"role": "assistant", "content": response.content})
    tool_results = []
    for block in response.content:
        if block.type == "tool_use" and block.name == "generate_media":
            try:
                result = generate_media(block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [{"type": "text", "text": json.dumps(result)}],
                })
            except Exception as exc:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": [{"type": "text", "text": f"Generation failed: {exc}"}],
                })
    messages.append({"role": "user", "content": tool_results})

final_text = next((b.text for b in response.content if b.type == "text"), "")
print(final_text)
```

## Implementation guidance

The contract is deliberately synchronous: one `tool_use` in, one
`tool_result` out. Implementers should account for the following limitations:

- **Timeouts.** Generation (especially video) can take minutes. Put a
  timeout on the call to the generation service (120s is a reasonable
  default) and return the timeout as an `is_error` result so Claude can
  lower `quality`, shorten `duration_seconds`, or tell the user. Jobs that
  routinely exceed a practical timeout need an async job/polling API, which
  is out of scope for this contract.
- **Bound the tool-use loop.** Claude may retry after errors or generate
  several variations. Cap iterations (see `MAX_TOOL_ITERATIONS` above) so a
  pathological conversation cannot run up generation and token costs.
- **Cross-field validation is the executor's job.** JSON Schema alone cannot
  express the `media_type`/`format` pairing or which dimensions apply, so
  validate before dispatching and reject bad combinations with an `is_error`
  result naming the allowed values — Claude will correct and retry.
- **Keep execution server-side.** The generation service URL and API key
  belong on a server (or edge function), never in client-side code.

## Reference integrations

- `morphic-ai-answer-engine-generative-ui` — `lib/tools/media-generation.ts`
  exposes the contract as a Vercel AI SDK tool on the researcher agent,
  activated when `MEDIA_GENERATION_API_URL` is configured.
- `creator-commerce-hub` — `supabase/functions/generate-media/index.ts` runs
  this contract's tool-use loop end-to-end behind an HTTP endpoint, with a
  typed client in `src/lib/generate-media.ts`.

Both forward the tool input unchanged to a backend that accepts the request
JSON and responds with the asset payload described above, so one
contract-compliant service can serve every integration.
