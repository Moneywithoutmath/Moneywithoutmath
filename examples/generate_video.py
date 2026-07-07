#!/usr/bin/env python3
"""Reference implementation of the generate_media contract for media_type: "video".

Acts as the client-side executor for a `tool_use` block emitted by Claude:
it takes the tool input JSON on stdin (or a path as argv[1]), renders a video
with ffmpeg, and prints the `tool_result` payload JSON on stdout.

This is a local stand-in for a real video-generation service — it renders the
prompt text over a procedurally animated background so the end-to-end
tool_use -> generation -> tool_result flow can be exercised without external
API credentials.
"""
import json
import subprocess
import sys
from pathlib import Path


def generate_video(tool_input: dict, out_dir: Path = Path("out")) -> dict:
    if tool_input.get("media_type") != "video":
        return {"status": "error", "message": "this executor only handles media_type: video"}
    fmt = tool_input.get("format", "mp4")
    if fmt not in ("mp4", "webm"):
        return {"status": "error", "message": f"format {fmt!r} is not valid for video"}

    width = tool_input.get("width", 1280)
    height = tool_input.get("height", 720)
    duration = tool_input.get("duration_seconds", 5)
    prompt = tool_input.get("prompt", "")
    seed = tool_input.get("seed", 0)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"generated.{fmt}"

    label = prompt.replace("'", "").replace(":", r"\:")
    vf = (
        f"drawtext=text='{label}':fontcolor=white:fontsize={max(16, width // 40)}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.4:boxborderw=12"
    )
    codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p"] if fmt == "mp4" else ["-c:v", "libvpx-vp9"]
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"gradients=size={width}x{height}:duration={duration}:speed=0.05:seed={seed}",
        "-vf", vf,
        "-t", str(duration),
        *codec,
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"status": "error", "message": proc.stderr[-500:]}

    return {
        "status": "success",
        "assets": [
            {
                "url": str(out_path),
                "format": fmt,
                "width": width,
                "height": height,
                "duration_seconds": duration,
                "seed": seed,
            }
        ],
    }


if __name__ == "__main__":
    raw = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(generate_video(json.loads(raw)), indent=2))
