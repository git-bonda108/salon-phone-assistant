"""Secrets from Azure Key Vault (keyless, managed identity).

On start-up, when ``KEY_VAULT_URL`` is set, settings that are empty are filled from
Key Vault. Our own secrets (tool API key, admin token, webhook secret) are
**generated on first start** if missing, so no secret is ever typed, pasted or
committed. The owner copies the tool key and webhook secret from Key Vault into the
Suisse Line portal once.

Third-party secrets (Google service-account JSON, Outlook token cache, Suisse Line
signing secret if Suisse Line generates it) are added to Key Vault by the owner.
"""

from __future__ import annotations

import logging
import secrets as _secrets
import time

from .config import Settings

log = logging.getLogger("secrets")

# settings field -> Key Vault secret name, generate if missing?
MAPPING: dict[str, tuple[str, bool]] = {
    "tool_api_key": ("tool-api-key", True),
    "admin_token": ("admin-token", True),
    "webhook_secret": ("webhook-secret", True),
    "notify_signing_secret": ("notify-signing-secret", True),
    "google_service_account_json": ("google-service-account-json", False),
    "graph_token_cache": ("graph-token-cache", False),
    "notify_webhook_url": ("notify-webhook-url", False),
}


def _retry(fn, what: str, attempts: int = 7, delays=(3, 5, 10, 15, 20, 30)):
    """A role assignment created in the same deployment can take minutes to apply: retry 403s and network errors."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundError" or i == attempts - 1:
                raise
            delay = delays[min(i, len(delays) - 1)]
            log.warning("Key Vault %s failed (%s), retrying in %ss", what, exc.__class__.__name__, delay)
            time.sleep(delay)


class KeyVaultSecrets:
    def __init__(self, vault_url: str, client=None, retry: bool = True):
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        self.client = client
        self.retry = retry

    def get(self, name: str) -> str | None:
        try:
            if self.retry:
                return _retry(lambda: self.client.get_secret(name).value, f"read {name}")
            return self.client.get_secret(name).value
        except Exception as exc:  # ResourceNotFoundError and friends
            if exc.__class__.__name__ != "ResourceNotFoundError":
                log.warning("Key Vault read %s failed: %s", name, exc)
            return None

    def set(self, name: str, value: str) -> None:
        if self.retry:
            _retry(lambda: self.client.set_secret(name, value), f"write {name}")
        else:
            self.client.set_secret(name, value)


def load_into(settings: Settings, vault: KeyVaultSecrets | None = None) -> KeyVaultSecrets | None:
    if not settings.key_vault_url and vault is None:
        return None
    vault = vault or KeyVaultSecrets(settings.key_vault_url)
    for field, (name, generate) in MAPPING.items():
        if getattr(settings, field):
            continue
        value = vault.get(name)
        if value is None and generate:
            value = _secrets.token_urlsafe(32)
            vault.set(name, value)
            log.info("generated secret %s in Key Vault", name)
        if value:
            setattr(settings, field, value)
    return vault
