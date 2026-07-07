"""PromptClip — prompt-to-video micro-SaaS built on the generate_media contract.

Monetization model: prepaid credits. Every video render burns credits by
duration and quality. New accounts get 3 free credits (free tier = funnel);
paid packs are purchased via Stripe Checkout (set STRIPE_SECRET_KEY and
STRIPE_WEBHOOK_SECRET to go live).

Hardening in this revision:
- atomic credit debit (no double-spend under concurrent requests)
- signup abuse controls: one account per email, per-IP hourly cap
- Stripe webhook signature verification + replay/idempotency guard
- watermark on free-tier (draft) renders — every share is an ad
- referral program: +3 credits to both sides on first paid render
- prompt template gallery to drive render volume
- admin metrics endpoint (ADMIN_TOKEN) for revenue/usage tracking

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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "promptclip.db"
MEDIA_DIR = ROOT / "media"
MEDIA_DIR.mkdir(exist_ok=True)

FREE_CREDITS = 3
REFERRAL_BONUS = 3
SIGNUPS_PER_IP_PER_HOUR = 3
QUALITY_MULTIPLIER = {"draft": 1, "standard": 2, "high": 4}
CREDIT_PACKS = {  # pack id -> (credits, USD price)
    "starter": (20, 5),
    "creator": (100, 19),
    "studio": (500, 79),
}
PLANS = {  # plan id -> (USD/month, credits granted each billing cycle, watermark-free)
    "free": (0, 0, False),
    "pro": (29, 200, True),
    "business": (99, 1000, True),
}
WATERMARK = os.environ.get("WATERMARK_TEXT", "made with promptclip")

TEMPLATES = [
    {"id": "neon-city", "name": "Neon city", "prompt": "slow pan across a neon-lit city skyline at night, rain"},
    {"id": "sunrise", "name": "Golden sunrise", "prompt": "golden sunrise over misty mountains, cinematic"},
    {"id": "ocean", "name": "Ocean loop", "prompt": "calm turquoise ocean waves, aerial view, seamless loop"},
    {"id": "product", "name": "Product reveal", "prompt": "sleek product on rotating pedestal, studio lighting"},
]

app = FastAPI(title="PromptClip", version="2.0.0")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
          api_key TEXT PRIMARY KEY, email TEXT UNIQUE, credits INTEGER,
          referral_code TEXT UNIQUE, referred_by TEXT, referral_paid INTEGER DEFAULT 0,
          created REAL);
        CREATE TABLE IF NOT EXISTS renders (
          id TEXT PRIMARY KEY, api_key TEXT, prompt TEXT, cost INTEGER,
          file TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS signups_ip (ip TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS payments (
          session_id TEXT PRIMARY KEY, api_key TEXT, credits INTEGER,
          usd INTEGER, created REAL);
        """
    )
    try:
        conn.execute("ALTER TABLE accounts ADD COLUMN plan TEXT DEFAULT 'free'")
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn


def account_for(conn: sqlite3.Connection, api_key: str | None) -> sqlite3.Row:
    if not api_key:
        raise HTTPException(401, "Pass your API key in the X-Api-Key header. POST /api/signup to get one.")
    row = conn.execute("SELECT * FROM accounts WHERE api_key=?", (api_key,)).fetchone()
    if not row:
        raise HTTPException(401, "Unknown API key.")
    return row


class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    referral_code: str | None = None


@app.post("/api/signup")
def signup(body: SignupIn, request: Request):
    conn = db()
    try:
        ip = request.client.host if request.client else "unknown"
        cutoff = time.time() - 3600
        conn.execute("DELETE FROM signups_ip WHERE created < ?", (cutoff,))
        recent = conn.execute(
            "SELECT COUNT(*) c FROM signups_ip WHERE ip=? AND created >= ?", (ip, cutoff)
        ).fetchone()["c"]
        if recent >= SIGNUPS_PER_IP_PER_HOUR:
            raise HTTPException(429, "Too many signups from this address. Try again later.")

        referred_by = None
        if body.referral_code:
            ref = conn.execute(
                "SELECT api_key FROM accounts WHERE referral_code=?", (body.referral_code,)
            ).fetchone()
            if not ref:
                raise HTTPException(400, "Unknown referral code.")
            referred_by = body.referral_code

        key = "pc_" + secrets.token_urlsafe(24)
        my_code = secrets.token_hex(4)
        try:
            conn.execute(
                "INSERT INTO accounts (api_key, email, credits, referral_code, referred_by, created)"
                " VALUES (?,?,?,?,?,?)",
                (key, body.email.lower(), FREE_CREDITS, my_code, referred_by, time.time()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account already exists for this email.")
        conn.execute("INSERT INTO signups_ip VALUES (?,?)", (ip, time.time()))
        conn.commit()
        return {"api_key": key, "credits": FREE_CREDITS, "referral_code": my_code}
    finally:
        conn.close()


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    duration_seconds: float = Field(default=5, ge=1, le=30)
    width: int = Field(default=1280, ge=64, le=1920)
    height: int = Field(default=720, ge=64, le=1920)
    quality: str = Field(default="draft", pattern="^(draft|standard|high)$")
    seed: int = 0


def render_cost(body: GenerateIn) -> int:
    blocks = int(-(-body.duration_seconds // 5))  # 1 credit per started 5s block
    return blocks * QUALITY_MULTIPLIER[body.quality]


def drawtext_escape(s: str) -> str:
    # escape ffmpeg drawtext specials: backslash, quote, colon, percent
    return (
        s.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")
    )


@app.post("/api/generate")
def generate(body: GenerateIn, x_api_key: str | None = Header(default=None)):
    conn = db()
    try:
        acct = account_for(conn, x_api_key)
        cost = render_cost(body)
        # atomic check-and-debit: fails cleanly under concurrency, no double-spend
        cur = conn.execute(
            "UPDATE accounts SET credits = credits - ? WHERE api_key=? AND credits >= ?",
            (cost, x_api_key, cost),
        )
        if cur.rowcount == 0:
            raise HTTPException(
                402,
                f"Insufficient credits (need {cost}, have {acct['credits']}). "
                "Buy a pack via POST /api/checkout.",
            )
        conn.commit()

        render_id = uuid.uuid4().hex[:12]
        out_path = MEDIA_DIR / f"{render_id}.mp4"
        label = drawtext_escape(body.prompt[:120])
        filters = [
            f"drawtext=text='{label}':fontcolor=white:fontsize={max(16, body.width // 40)}:"
            "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.4:boxborderw=12"
        ]
        watermark_free = PLANS.get(acct["plan"] or "free", PLANS["free"])[2]
        if not watermark_free:  # watermark free-plan renders: shares become ads
            filters.append(
                f"drawtext=text='{drawtext_escape(WATERMARK)}':fontcolor=white@0.7:"
                f"fontsize={max(12, body.width // 64)}:x=w-text_w-16:y=h-text_h-12"
            )
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i",
                f"gradients=size={body.width}x{body.height}:duration={body.duration_seconds}"
                f":speed=0.05:seed={body.seed}",
                "-vf", ",".join(filters), "-t", str(body.duration_seconds),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path),
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            # refund on failure
            conn.execute("UPDATE accounts SET credits = credits + ? WHERE api_key=?", (cost, x_api_key))
            conn.commit()
            raise HTTPException(500, "Render failed (credits refunded): " + proc.stderr[-300:])

        conn.execute(
            "INSERT INTO renders VALUES (?,?,?,?,?,?)",
            (render_id, x_api_key, body.prompt, cost, str(out_path), time.time()),
        )
        conn.commit()
        return {
            "status": "success",
            "render_id": render_id,
            "cost_credits": cost,
            "download": f"/media/{render_id}.mp4",
        }
    finally:
        conn.close()


@app.get("/api/templates")
def templates():
    return {"templates": TEMPLATES}


@app.get("/media/{name}")
def media(name: str):
    path = (MEDIA_DIR / name).resolve()
    if not str(path).startswith(str(MEDIA_DIR.resolve())) or not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/me")
def me(x_api_key: str | None = Header(default=None)):
    conn = db()
    try:
        acct = account_for(conn, x_api_key)
        return {
            "email": acct["email"],
            "credits": acct["credits"],
            "referral_code": acct["referral_code"],
            "plan": acct["plan"] or "free",
        }
    finally:
        conn.close()


class CheckoutIn(BaseModel):
    pack: str


def grant_referral_bonus(conn: sqlite3.Connection, api_key: str):
    """On first payment, reward both the buyer and whoever referred them."""
    acct = conn.execute("SELECT * FROM accounts WHERE api_key=?", (api_key,)).fetchone()
    if not acct or acct["referral_paid"] or not acct["referred_by"]:
        return
    conn.execute(
        "UPDATE accounts SET credits = credits + ?, referral_paid = 1 WHERE api_key=?",
        (REFERRAL_BONUS, api_key),
    )
    conn.execute(
        "UPDATE accounts SET credits = credits + ? WHERE referral_code=?",
        (REFERRAL_BONUS, acct["referred_by"]),
    )


@app.post("/api/checkout")
def checkout(body: CheckoutIn, x_api_key: str | None = Header(default=None)):
    conn = db()
    try:
        account_for(conn, x_api_key)
    finally:
        conn.close()
    if body.pack not in CREDIT_PACKS:
        raise HTTPException(400, f"Unknown pack. Choose one of {list(CREDIT_PACKS)}.")
    credits, usd = CREDIT_PACKS[body.pack]
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        return {
            "status": "setup_required",
            "message": (
                "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET; "
                "point a Stripe webhook at /api/stripe/webhook to credit accounts after payment."
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
        metadata={"api_key": x_api_key, "credits": credits, "usd": usd},
        success_url=os.environ.get("CHECKOUT_SUCCESS_URL", "http://localhost:8000/?paid=1"),
        cancel_url=os.environ.get("CHECKOUT_CANCEL_URL", "http://localhost:8000/"),
    )
    return {"status": "ok", "checkout_url": session.url}


class SubscribeIn(BaseModel):
    plan: str


@app.post("/api/subscribe")
def subscribe(body: SubscribeIn, x_api_key: str | None = Header(default=None)):
    conn = db()
    try:
        account_for(conn, x_api_key)
    finally:
        conn.close()
    if body.plan not in PLANS or body.plan == "free":
        raise HTTPException(400, f"Choose one of {[p for p in PLANS if p != 'free']}.")
    usd, credits, _ = PLANS[body.plan]
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        return {
            "status": "setup_required",
            "message": (
                "Stripe is not configured. With STRIPE_SECRET_KEY set, this returns a "
                "subscription-mode Checkout session; each invoice.paid webhook grants "
                f"the plan's monthly credits ({credits}/mo for {body.plan})."
            ),
            "plan": {"id": body.plan, "usd_per_month": usd, "credits_per_month": credits},
        }
    import stripe

    stripe.api_key = stripe_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": usd * 100,
                "recurring": {"interval": "month"},
                "product_data": {"name": f"PromptClip {body.plan} plan"},
            },
            "quantity": 1,
        }],
        metadata={"api_key": x_api_key, "plan": body.plan},
        subscription_data={"metadata": {"api_key": x_api_key, "plan": body.plan}},
        success_url=os.environ.get("CHECKOUT_SUCCESS_URL", "http://localhost:8000/?subscribed=1"),
        cancel_url=os.environ.get("CHECKOUT_CANCEL_URL", "http://localhost:8000/"),
    )
    return {"status": "ok", "checkout_url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if webhook_secret:
        import stripe

        try:
            event = stripe.Webhook.construct_event(
                payload, request.headers.get("stripe-signature", ""), webhook_secret
            )
        except Exception:
            raise HTTPException(400, "Invalid webhook signature.")
        event = json.loads(json.dumps(event))  # normalize to dict
    else:
        # Dev mode only. NEVER run in production without STRIPE_WEBHOOK_SECRET —
        # an unverified webhook endpoint mints unlimited free credits.
        event = json.loads(payload)

    if event.get("type") == "invoice.paid":
        # recurring subscription cycle: grant the plan's monthly credits
        obj = event["data"]["object"]
        meta = obj.get("subscription_details", {}).get("metadata", {}) or obj.get("metadata", {})
        api_key, plan = meta.get("api_key"), meta.get("plan")
        invoice_id = obj.get("id", "")
        if api_key and plan in PLANS and invoice_id:
            usd, credits, _ = PLANS[plan]
            conn = db()
            try:
                try:
                    conn.execute(
                        "INSERT INTO payments VALUES (?,?,?,?,?)",
                        (invoice_id, api_key, credits, usd, time.time()),
                    )
                except sqlite3.IntegrityError:
                    return {"received": True, "duplicate": True}
                conn.execute(
                    "UPDATE accounts SET credits = credits + ?, plan = ? WHERE api_key=?",
                    (credits, plan, api_key),
                )
                grant_referral_bonus(conn, api_key)
                conn.commit()
            finally:
                conn.close()

    if event.get("type") == "checkout.session.completed":
        obj = event["data"]["object"]
        session_id = obj.get("id", "")
        meta = obj.get("metadata", {})
        api_key = meta.get("api_key")
        credits = int(meta.get("credits", 0))
        usd = int(meta.get("usd", 0))
        if api_key and credits and session_id:
            conn = db()
            try:
                try:  # idempotency: a replayed event credits nothing
                    conn.execute(
                        "INSERT INTO payments VALUES (?,?,?,?,?)",
                        (session_id, api_key, credits, usd, time.time()),
                    )
                except sqlite3.IntegrityError:
                    return {"received": True, "duplicate": True}
                conn.execute(
                    "UPDATE accounts SET credits = credits + ? WHERE api_key=?",
                    (credits, api_key),
                )
                grant_referral_bonus(conn, api_key)
                conn.commit()
            finally:
                conn.close()
    return {"received": True}


@app.get("/api/admin/metrics")
def admin_metrics(x_admin_token: str | None = Header(default=None)):
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(403, "Set ADMIN_TOKEN and pass it in X-Admin-Token.")
    conn = db()
    try:
        day_ago = time.time() - 86400
        return {
            "accounts": conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"],
            "renders_total": conn.execute("SELECT COUNT(*) c FROM renders").fetchone()["c"],
            "renders_24h": conn.execute(
                "SELECT COUNT(*) c FROM renders WHERE created >= ?", (day_ago,)
            ).fetchone()["c"],
            "credits_burned": conn.execute(
                "SELECT COALESCE(SUM(cost),0) s FROM renders"
            ).fetchone()["s"],
            "revenue_usd": conn.execute(
                "SELECT COALESCE(SUM(usd),0) s FROM payments"
            ).fetchone()["s"],
            "paying_customers": conn.execute(
                "SELECT COUNT(DISTINCT api_key) c FROM payments"
            ).fetchone()["c"],
        }
    finally:
        conn.close()


@app.get("/api/admin/summary")
def admin_summary(x_admin_token: str | None = Header(default=None)):
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(403, "Set ADMIN_TOKEN and pass it in X-Admin-Token.")
    conn = db()
    try:
        now = time.time()
        days = []
        for i in range(13, -1, -1):
            start = now - (i + 1) * 86400
            end = now - i * 86400
            days.append({
                "day": time.strftime("%b %d", time.gmtime(end)),
                "revenue_usd": conn.execute(
                    "SELECT COALESCE(SUM(usd),0) s FROM payments WHERE created>=? AND created<?",
                    (start, end)).fetchone()["s"],
                "renders": conn.execute(
                    "SELECT COUNT(*) c FROM renders WHERE created>=? AND created<?",
                    (start, end)).fetchone()["c"],
                "signups": conn.execute(
                    "SELECT COUNT(*) c FROM accounts WHERE created>=? AND created<?",
                    (start, end)).fetchone()["c"],
            })
        plan_mix = {
            row["plan"] or "free": row["c"]
            for row in conn.execute(
                "SELECT plan, COUNT(*) c FROM accounts GROUP BY plan"
            ).fetchall()
        }
        mrr = sum(PLANS[p][0] * n for p, n in plan_mix.items() if p in PLANS)
        return {
            "tiles": {
                "mrr_usd": mrr,
                "revenue_usd": conn.execute(
                    "SELECT COALESCE(SUM(usd),0) s FROM payments").fetchone()["s"],
                "paying_customers": conn.execute(
                    "SELECT COUNT(DISTINCT api_key) c FROM payments").fetchone()["c"],
                "accounts": conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"],
                "credits_burned": conn.execute(
                    "SELECT COALESCE(SUM(cost),0) s FROM renders").fetchone()["s"],
            },
            "plan_mix": plan_mix,
            "days": days,
        }
    finally:
        conn.close()


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (ROOT / "app" / "dashboard.html").read_text()


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "app" / "index.html").read_text()
