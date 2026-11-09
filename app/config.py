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
    calendar_backend: str = "sqlite"            # sqlite | google | outlook
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
    # Microsoft 365 / Outlook calendar (Microsoft Graph)
    graph_auth: str = "app"                     # app (managed identity / app-only) | delegated (MSAL token cache)
    graph_calendars: dict = field(default_factory=dict)  # {"lara": {"user": "me|upn", "calendar_id": "..."}}
    graph_client_id: str = ""                   # public client id for delegated auth
    graph_token_cache: str = ""                 # MSAL cache JSON or path (delegated); Key Vault secret in Azure
    # Azure AI (all in Switzerland North, keyless via managed identity)
    azure_ai_endpoint: str = ""                 # https://<ais>.cognitiveservices.azure.com
    azure_ai_key: str = ""                      # local development only
    azure_openai_endpoint: str = ""             # usually the same account; https://<ais>.openai.azure.com
    azure_openai_deployment: str = "gpt-4.1-mini"
    azure_openai_judge_deployment: str = "gpt-5.1"
    azure_openai_api_version: str = "2025-04-01-preview"
    key_vault_url: str = ""
    review_calls: bool = True                   # run the call-reviewer agent on call.completed

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
            graph_auth=e("GRAPH_AUTH", "app"),
            graph_calendars=json.loads(e("GRAPH_CALENDARS", "{}") or "{}"),
            graph_client_id=e("GRAPH_CLIENT_ID", ""),
            graph_token_cache=e("GRAPH_TOKEN_CACHE", ""),
            azure_ai_endpoint=e("AZURE_AI_ENDPOINT", ""),
            azure_ai_key=e("AZURE_AI_KEY", ""),
            azure_openai_endpoint=e("AZURE_OPENAI_ENDPOINT", ""),
            azure_openai_deployment=e("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
            azure_openai_judge_deployment=e("AZURE_OPENAI_JUDGE_DEPLOYMENT", "gpt-5.1"),
            azure_openai_api_version=e("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            key_vault_url=e("KEY_VAULT_URL", ""),
            review_calls=e("REVIEW_CALLS", "1") == "1",
        )
