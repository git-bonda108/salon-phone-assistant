from app.config import Settings
from app.secrets import KeyVaultSecrets, load_into


class FakeVault:
    def __init__(self, data):
        self.data = dict(data)

    def get_secret(self, name):
        if name not in self.data:
            raise type("ResourceNotFoundError", (Exception,), {})()
        return type("S", (), {"value": self.data[name]})()

    def set_secret(self, name, value):
        self.data[name] = value


def test_existing_secrets_are_loaded_and_missing_own_secrets_generated():
    fake = FakeVault({"webhook-secret": "from-suisse-line", "graph-token-cache": "{}"})
    s = Settings(database_path=":memory:")
    load_into(s, KeyVaultSecrets("https://kv", client=fake, retry=False))
    assert s.webhook_secret == "from-suisse-line" and s.graph_token_cache == "{}"
    assert len(s.tool_api_key) >= 40 and fake.data["tool-api-key"] == s.tool_api_key
    assert s.admin_token and s.admin_token != s.tool_api_key
    assert "google-service-account-json" not in fake.data  # third-party secrets are never invented


def test_explicit_settings_win_and_no_vault_is_a_noop():
    s = Settings(database_path=":memory:", tool_api_key="set")
    assert load_into(s) is None and s.tool_api_key == "set"
    fake = FakeVault({"tool-api-key": "vault"})
    load_into(s, KeyVaultSecrets("https://kv", client=fake, retry=False))
    assert s.tool_api_key == "set"


def test_malformed_json_settings_do_not_crash_startup(monkeypatch):
    monkeypatch.setenv("GRAPH_CALENDARS", "{}}")  # what azd produced from '${GRAPH_CALENDARS={}}'
    monkeypatch.setenv("GOOGLE_CALENDAR_IDS", "")
    s = Settings.from_env()
    assert s.graph_calendars == {} and s.google_calendar_ids == {}
    monkeypatch.setenv("GRAPH_CALENDARS", '{"lara": {"user": "me", "calendar_id": "x"}}')
    assert Settings.from_env().graph_calendars["lara"]["calendar_id"] == "x"
