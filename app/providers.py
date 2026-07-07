"""Render provider layer — swap engines without touching the API.

The active provider is chosen by the RENDER_PROVIDER env var:

  local      (default) ffmpeg procedural renderer; zero-cost, used for dev
             and as an automatic fallback when a real provider fails.
  replicate  any text-to-video model hosted on replicate.com.
             Requires REPLICATE_API_TOKEN; model picked by REPLICATE_MODEL
             (a "owner/name:version" string).

Every provider implements render(params, out_path) and either writes a video
file to out_path or raises ProviderError with a user-safe message.
"""
import os
import subprocess
import time
import urllib.request
import json


class ProviderError(Exception):
    pass


def _drawtext_escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")


class LocalFFmpegProvider:
    name = "local"

    def render(self, params: dict, out_path: str) -> None:
        label = _drawtext_escape(params["prompt"][:120])
        filters = [
            f"drawtext=text='{label}':fontcolor=white:fontsize={max(16, params['width'] // 40)}:"
            "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.4:boxborderw=12"
        ]
        if params.get("watermark"):
            filters.append(
                f"drawtext=text='{_drawtext_escape(params['watermark'])}':fontcolor=white@0.7:"
                f"fontsize={max(12, params['width'] // 64)}:x=w-text_w-16:y=h-text_h-12"
            )
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i",
                f"gradients=size={params['width']}x{params['height']}"
                f":duration={params['duration_seconds']}:speed=0.05:seed={params.get('seed', 0)}",
                "-vf", ",".join(filters), "-t", str(params["duration_seconds"]),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path,
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise ProviderError("Render failed: " + proc.stderr[-300:])


class ReplicateProvider:
    """Text-to-video via the Replicate predictions API."""

    name = "replicate"

    def __init__(self):
        self.token = os.environ.get("REPLICATE_API_TOKEN")
        self.model = os.environ.get("REPLICATE_MODEL", "")
        if not self.token or ":" not in self.model:
            raise ProviderError(
                "Set REPLICATE_API_TOKEN and REPLICATE_MODEL (owner/name:version)."
            )

    def _api(self, method: str, url: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.load(res)

    def render(self, params: dict, out_path: str) -> None:
        version = self.model.split(":", 1)[1]
        pred = self._api(
            "POST",
            "https://api.replicate.com/v1/predictions",
            {
                "version": version,
                "input": {
                    "prompt": params["prompt"],
                    "width": params["width"],
                    "height": params["height"],
                    "num_frames": int(params["duration_seconds"] * 24),
                    "seed": params.get("seed", 0),
                },
            },
        )
        deadline = time.time() + 600
        while pred["status"] not in ("succeeded", "failed", "canceled"):
            if time.time() > deadline:
                raise ProviderError("Provider timed out after 10 minutes.")
            time.sleep(3)
            pred = self._api("GET", pred["urls"]["get"])
        if pred["status"] != "succeeded":
            raise ProviderError(f"Provider error: {pred.get('error') or pred['status']}")
        output = pred["output"]
        video_url = output[0] if isinstance(output, list) else output
        urllib.request.urlretrieve(video_url, out_path)


def get_provider():
    kind = os.environ.get("RENDER_PROVIDER", "local")
    if kind == "replicate":
        return ReplicateProvider()
    return LocalFFmpegProvider()
