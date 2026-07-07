"""PromptClip — prompt-to-video micro-SaaS built on the generate_media contract.

Monetization model: prepaid credits. Every video render burns credits by
duration and quality. New accounts get 3 free credits (free tier = funnel);
paid packs are purchased via Stripe Checkout (set STRIPE_SECRET_KEY and
STRIPE_PRICE_* env vars to go live — without them /api/checkout returns a
clear setup message instead of failing silently).

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import json
import os
import secrets
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "promptclip.db"
MEDIA_DIR = ROOT / "media"
MEDIA_DIR.mkdir(exist_ok=True)

FREE_CREDITS = 3
QUALITY_MULTIPLIER = {"draft": 1, "standard": 2, "high": 4}
CREDIT_PACKS = {  # pack id -> (credits, USD price) — tune freely
    "starter": (20, 5),
    "creator": (100, 19),
    "studio": (500, 79),
}

app = FastAPI(title="PromptClip", version="1.0.0")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts ("
        "api_key TEXT PRIMARY KEY, email TEXT, credits INTEGER, created REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS renders ("
        "id TEXT PRIMARY KEY, api_key TEXT, prompt TEXT, cost INTEGER, "
        "file TEXT, created REAL)"
    )
    return conn


def account_for(api_key: str | None) -> tuple[sqlite3.Connection, sqlite3.Row]:
    if not api_key:
        raise HTTPException(401, "Pass your API key in the X-Api-Key header. POST /api/signup to get one.")
    conn = db()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM accounts WHERE api_key=?", (api_key,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(401, "Unknown API key.")
    return conn, row


class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@app.post("/api/signup")
def signup(body: SignupIn):
    key = "pc_" + secrets.token_urlsafe(24)
    conn = db()
    conn.execute(
        "INSERT INTO accounts VALUES (?,?,?,?)",
        (key, body.email, FREE_CREDITS, time.time()),
    )
    conn.commit()
    conn.close()
    return {"api_key": key, "credits": FREE_CREDITS}


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    duration_seconds: float = Field(default=5, ge=1, le=30)
    width: int = Field(default=1280, ge=64, le=1920)
    height: int = Field(default=720, ge=64, le=1920)
    quality: str = Field(default="draft", pattern="^(draft|standard|high)$")
    seed: int = 0


def render_cost(body: GenerateIn) -> int:
    # 1 credit per started 5s block, scaled by quality tier
    blocks = int(-(-body.duration_seconds // 5))
    return blocks * QUALITY_MULTIPLIER[body.quality]


@app.post("/api/generate")
def generate(body: GenerateIn, x_api_key: str | None = Header(default=None)):
    conn, acct = account_for(x_api_key)
    cost = render_cost(body)
    if acct["credits"] < cost:
        conn.close()
        raise HTTPException(
            402,
            f"Insufficient credits (need {cost}, have {acct['credits']}). "
            "Buy a pack via POST /api/checkout.",
        )

    render_id = uuid.uuid4().hex[:12]
    out_path = MEDIA_DIR / f"{render_id}.mp4"
    label = body.prompt.replace("'", "").replace(":", r"\:")[:120]
    vf = (
        f"drawtext=text='{label}':fontcolor=white:fontsize={max(16, body.width // 40)}:"
        "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.4:boxborderw=12"
    )
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i",
            f"gradients=size={body.width}x{body.height}:duration={body.duration_seconds}"
            f":speed=0.05:seed={body.seed}",
            "-vf", vf, "-t", str(body.duration_seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        conn.close()
        raise HTTPException(500, "Render failed: " + proc.stderr[-300:])

    conn.execute("UPDATE accounts SET credits = credits - ? WHERE api_key=?", (cost, x_api_key))
    conn.execute(
        "INSERT INTO renders VALUES (?,?,?,?,?,?)",
        (render_id, x_api_key, body.prompt, cost, str(out_path), time.time()),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "render_id": render_id,
        "cost_credits": cost,
        "download": f"/media/{render_id}.mp4",
    }


@app.get("/media/{name}")
def media(name: str):
    path = (MEDIA_DIR / name).resolve()
    if not str(path).startswith(str(MEDIA_DIR.resolve())) or not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/me")
def me(x_api_key: str | None = Header(default=None)):
    conn, acct = account_for(x_api_key)
    conn.close()
    return {"email": acct["email"], "credits": acct["credits"]}


class CheckoutIn(BaseModel):
    pack: str


@app.post("/api/checkout")
def checkout(body: CheckoutIn, x_api_key: str | None = Header(default=None)):
    conn, _ = account_for(x_api_key)
    if body.pack not in CREDIT_PACKS:
        conn.close()
        raise HTTPException(400, f"Unknown pack. Choose one of {list(CREDIT_PACKS)}.")
    credits, usd = CREDIT_PACKS[body.pack]
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        conn.close()
        return {
            "status": "setup_required",
            "message": (
                "Stripe is not configured. Set STRIPE_SECRET_KEY and create a "
                f"Price for the '{body.pack}' pack ({credits} credits, ${usd}), "
                "then this endpoint returns a Checkout session URL. "
                "Wire the checkout.session.completed webhook to /api/stripe/webhook "
                "to credit the account."
            ),
            "pack": {"id": body.pack, "credits": credits, "usd": usd},
        }
    import stripe  # requires `pip install stripe`

    stripe.api_key = stripe_key
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": usd * 100,
                "product_data": {"name": f"PromptClip {body.pack} pack ({credits} credits)"},
            },
            "quantity": 1,
        }],
        metadata={"api_key": x_api_key, "credits": credits},
        success_url=os.environ.get("CHECKOUT_SUCCESS_URL", "http://localhost:8000/?paid=1"),
        cancel_url=os.environ.get("CHECKOUT_CANCEL_URL", "http://localhost:8000/"),
    )
    conn.close()
    return {"status": "ok", "checkout_url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(payload: dict):
    # Production: verify the Stripe-Signature header with STRIPE_WEBHOOK_SECRET.
    if payload.get("type") == "checkout.session.completed":
        meta = payload["data"]["object"].get("metadata", {})
        api_key, credits = meta.get("api_key"), int(meta.get("credits", 0))
        if api_key and credits:
            conn = db()
            conn.execute("UPDATE accounts SET credits = credits + ? WHERE api_key=?", (credits, api_key))
            conn.commit()
            conn.close()
    return {"received": True}


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "app" / "index.html").read_text()
