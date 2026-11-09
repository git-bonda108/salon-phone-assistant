"""Outbound notifications (slot offers, booking confirmations).

Suisse Line is the voice; the *delivery channel* for a waitlist offer is a
choice per salon, so notifications go to a webhook. Typical targets:

  * an n8n / Make / Zapier scenario that starts a Suisse Line **outbound
    call** with the "slot offer" agent (suisse-line/outbound-offer-agent.md),
  * or sends an SMS / WhatsApp with a call-back prompt.

Without a webhook URL the notification is only written to the outbox
(visible on the admin dashboard) — handy for demos and tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from .store import Store

log = logging.getLogger("notifier")


class Notifier:
    def __init__(self, store: Store, webhook_url: str = "", signing_secret: str = "", timeout: float = 5.0):
        self.store = store
        self.webhook_url = webhook_url
        self.signing_secret = signing_secret
        self.timeout = timeout

    def send(self, kind: str, payload: dict) -> bool:
        body = {"type": kind, **payload}
        if not self.webhook_url:
            log.info("notification (outbox only): %s %s", kind, payload)
            self.store.add_outbox(kind, body, delivered=False, error="no webhook configured")
            return False
        raw = json.dumps(body, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        if self.signing_secret:
            sig = hmac.new(self.signing_secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-Signature"] = f"sha256={sig}"
        try:
            resp = httpx.post(self.webhook_url, content=raw, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:  # never let a notification failure break a phone call
            log.warning("notification failed: %s", exc)
            self.store.add_outbox(kind, body, delivered=False, error=str(exc)[:300])
            return False
        self.store.add_outbox(kind, body, delivered=True)
        return True
