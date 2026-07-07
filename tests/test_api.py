"""End-to-end API tests. Run: python3 -m pytest tests/ -v"""
import os
import time

os.environ["ADMIN_TOKEN"] = "test-admin"

import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(m, "MEDIA_DIR", tmp_path / "media")
    m.MEDIA_DIR.mkdir()
    with TestClient(m.app) as c:
        yield c


def signup(client, email="a@x.com", **kw):
    res = client.post("/api/signup", json={"email": email, **kw})
    assert res.status_code == 200, res.text
    return res.json()


def wait_job(client, key, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers={"X-Api-Key": key}).json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.5)
    raise TimeoutError


def test_signup_and_free_credits(client):
    acct = signup(client)
    assert acct["credits"] == m.FREE_CREDITS
    me = client.get("/api/me", headers={"X-Api-Key": acct["api_key"]}).json()
    assert me["plan"] == "free"


def test_duplicate_email_rejected(client):
    signup(client)
    assert client.post("/api/signup", json={"email": "a@x.com"}).status_code == 409


def test_invalid_email_rejected(client):
    assert client.post("/api/signup", json={"email": "not-an-email"}).status_code == 422


def test_generate_debits_and_renders(client):
    acct = signup(client)
    key = acct["api_key"]
    res = client.post(
        "/api/generate",
        json={"prompt": "test's 100% render: ok", "duration_seconds": 2,
              "width": 320, "height": 240},
        headers={"X-Api-Key": key},
    )
    assert res.status_code == 202
    job = wait_job(client, key, res.json()["job_id"])
    assert job["status"] == "succeeded"
    assert client.get(job["download"]).status_code == 200
    me = client.get("/api/me", headers={"X-Api-Key": key}).json()
    assert me["credits"] == m.FREE_CREDITS - 1


def test_paywall_blocks_expensive_render(client):
    key = signup(client)["api_key"]
    res = client.post(
        "/api/generate",
        json={"prompt": "x", "duration_seconds": 30, "quality": "high"},
        headers={"X-Api-Key": key},
    )
    assert res.status_code == 402


def test_job_isolation(client):
    k1 = signup(client, "a@x.com")["api_key"]
    k2 = signup(client, "b@x.com")["api_key"]
    job_id = client.post(
        "/api/generate",
        json={"prompt": "x", "duration_seconds": 1, "width": 320, "height": 240},
        headers={"X-Api-Key": k1},
    ).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}", headers={"X-Api-Key": k2}).status_code == 404


def test_webhook_credits_and_idempotency(client):
    key = signup(client)["api_key"]
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "metadata":
                 {"api_key": key, "credits": 100, "usd": 19}}},
    }
    assert client.post("/api/stripe/webhook", json=event).status_code == 200
    replay = client.post("/api/stripe/webhook", json=event).json()
    assert replay.get("duplicate") is True
    me = client.get("/api/me", headers={"X-Api-Key": key}).json()
    assert me["credits"] == m.FREE_CREDITS + 100


def test_subscription_invoice_grants_plan(client):
    key = signup(client)["api_key"]
    event = {
        "type": "invoice.paid",
        "data": {"object": {"id": "in_1", "subscription_details":
                 {"metadata": {"api_key": key, "plan": "pro"}}}},
    }
    client.post("/api/stripe/webhook", json=event)
    me = client.get("/api/me", headers={"X-Api-Key": key}).json()
    assert me["plan"] == "pro"
    assert me["credits"] == m.FREE_CREDITS + m.PLANS["pro"][1]


def test_referral_bonus_on_first_payment(client):
    ref = signup(client, "ref@x.com")
    buyer = signup(client, "buyer@x.com", referral_code=ref["referral_code"])
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_2", "metadata":
                 {"api_key": buyer["api_key"], "credits": 20, "usd": 5}}},
    }
    client.post("/api/stripe/webhook", json=event)
    ref_me = client.get("/api/me", headers={"X-Api-Key": ref["api_key"]}).json()
    buyer_me = client.get("/api/me", headers={"X-Api-Key": buyer["api_key"]}).json()
    assert ref_me["credits"] == m.FREE_CREDITS + m.REFERRAL_BONUS
    assert buyer_me["credits"] == m.FREE_CREDITS + 20 + m.REFERRAL_BONUS


def test_admin_requires_token(client):
    assert client.get("/api/admin/summary").status_code == 403
    assert client.get(
        "/api/admin/summary", headers={"X-Admin-Token": "wrong"}
    ).status_code == 403
    res = client.get("/api/admin/summary", headers={"X-Admin-Token": "test-admin"})
    assert res.status_code == 200
    assert "mrr_usd" in res.json()["tiles"]


def test_media_path_traversal_blocked(client):
    assert client.get("/media/..%2Fpromptclip.db").status_code == 404


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}
