"""Request authentication: API key for live tools, HMAC for webhooks."""

from __future__ import annotations

import hashlib
import hmac


def check_api_key(expected: str, x_api_key: str | None, authorization: str | None) -> bool:
    if not expected:
        return True  # dev mode — the README insists on setting TOOL_API_KEY in production
    supplied = x_api_key or ""
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return hmac.compare_digest(supplied.encode(), expected.encode())


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    """Accepts 'sha256=<hex>' or bare hex, as webhook providers differ."""
    if not header_value:
        return False
    value = header_value.strip()
    if value.lower().startswith("sha256="):
        value = value[7:]
    return hmac.compare_digest(value.lower(), sign(secret, body))
