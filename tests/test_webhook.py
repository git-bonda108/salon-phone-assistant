import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import sign

SECRET = "whsec_test"
EVENT = {
    "event": "call.completed", "callId": "7d1c5e2a", "agentName": "Salon Edelweiss",
    "timestamp": "2026-11-09T09:42:18Z", "phoneNumber": "079 123 45 67", "direction": "inbound",
    "status": "completed", "duration": 94, "summary": "Anna Meier booked a women's cut for Tuesday 14:00.",
    "transcript": "Assistent: Grüezi, Salon Edelweiss …",
}


def make_client(clock):
    return TestClient(create_app(Settings(database_path=":memory:", sweep_interval_seconds=0,
                                          webhook_secret=SECRET), clock=clock))


def post(c, payload, signature):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature(raw) if callable(signature) else signature
    return c.post("/webhooks/suisse-line", content=raw, headers=headers)


def test_valid_signature_is_stored_once(clock):
    c = make_client(clock)
    r = post(c, EVENT, lambda raw: "sha256=" + sign(SECRET, raw))
    assert r.status_code == 200 and r.json()["duplicate"] is False
    r = post(c, EVENT, lambda raw: sign(SECRET, raw))  # bare hex also accepted; retry is deduplicated
    assert r.json()["duplicate"] is True
    calls = c.app.state.svc.store.recent_calls()
    assert len(calls) == 1 and calls[0]["phone"] == "+41791234567"


def test_bad_or_missing_signature_rejected(clock):
    c = make_client(clock)
    assert post(c, EVENT, "sha256=deadbeef").status_code == 401
    assert post(c, EVENT, None).status_code == 401


def test_tampered_body_rejected(clock):
    c = make_client(clock)
    good = sign(SECRET, json.dumps(EVENT).encode())
    tampered = {**EVENT, "summary": "something else"}
    assert post(c, tampered, good).status_code == 401


def test_missing_call_id(clock):
    c = make_client(clock)
    assert post(c, {"event": "call.completed"}, lambda raw: sign(SECRET, raw)).status_code == 400
