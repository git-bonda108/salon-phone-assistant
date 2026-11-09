from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TZ = ZoneInfo("Europe/Zurich")


class Clock:
    """Controllable clock: tests move time forward to expire offers etc."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    # Monday 9 November 2026, 10:00 — salon closed today, opens Tuesday.
    return Clock(datetime(2026, 11, 9, 10, 0, tzinfo=TZ))


@pytest.fixture
def app(clock):
    return create_app(Settings(database_path=":memory:", sweep_interval_seconds=0), clock=clock)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def svc(app):
    return app.state.svc


def tool(client, name, **args):
    r = client.post(f"/tools/{name}", json=args)
    assert r.status_code == 200, r.text
    return r.json()
