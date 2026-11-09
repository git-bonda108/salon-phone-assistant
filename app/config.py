"""Settings from environment variables (see .env.example)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    salon_config: str = str(ROOT / "data" / "salon.json")
    database_path: str = str(ROOT / "var" / "app.db")
    calendar_backend: str = "sqlite"            # sqlite | google
    google_service_account_json: str = ""       # key JSON or path to it
    google_calendar_ids: dict = field(default_factory=dict)  # {"lara": "...@group.calendar.google.com"}
    tool_api_key: str = ""                      # required header for /tools/* (X-Api-Key or Bearer)
    webhook_secret: str = ""                    # HMAC-SHA256 secret for Suisse Line webhooks
    webhook_signature_header: str = "X-Signature"
    notify_webhook_url: str = ""                # n8n / Make / Zapier URL for offers & confirmations
    notify_signing_secret: str = ""
    admin_token: str = ""
    offer_ttl_minutes: int = 20
    sweep_interval_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            salon_config=e("SALON_CONFIG", cls.salon_config),
            database_path=e("DATABASE_PATH", cls.database_path),
            calendar_backend=e("CALENDAR_BACKEND", "sqlite").lower(),
            google_service_account_json=e("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            google_calendar_ids=json.loads(e("GOOGLE_CALENDAR_IDS", "{}") or "{}"),
            tool_api_key=e("TOOL_API_KEY", ""),
            webhook_secret=e("WEBHOOK_SECRET", ""),
            webhook_signature_header=e("WEBHOOK_SIGNATURE_HEADER", "X-Signature"),
            notify_webhook_url=e("NOTIFY_WEBHOOK_URL", ""),
            notify_signing_secret=e("NOTIFY_SIGNING_SECRET", ""),
            admin_token=e("ADMIN_TOKEN", ""),
            offer_ttl_minutes=int(e("OFFER_TTL_MINUTES", "20")),
            sweep_interval_seconds=int(e("SWEEP_INTERVAL_SECONDS", "60")),
        )
