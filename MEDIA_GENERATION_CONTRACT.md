# Media Generation Contract

A Claude-native tool contract for requesting image, video, or audio generation
through Anthropic's tool-calling (`tool_use`) format.

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

while True:
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
