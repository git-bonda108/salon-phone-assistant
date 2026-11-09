"""Web app: access model, demo seed, live recovery scenario, rate limits."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.web_api import RateLimiter


def demo_app(clock, **kw):
    return create_app(Settings(database_path=":memory:", sweep_interval_seconds=0, public_demo=True, demo_seed=True,
                               admin_token="owner", **kw), clock=clock)


def test_private_by_default(clock):
    app = create_app(Settings(database_path=":memory:", sweep_interval_seconds=0, admin_token="owner"), clock=clock)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200  # the shell page itself holds no data
        for path in ("/api/overview", "/api/calendar", "/api/waitlist", "/api/reviews", "/api/activity"):
            assert c.get(path).status_code == 401, path
        assert c.get("/api/overview", headers={"X-Admin-Token": "owner"}).status_code == 200
        assert c.post("/api/demo/recovery", json={}).status_code == 401


def test_public_demo_seeds_and_replays_recovery(clock):
    with TestClient(demo_app(clock)) as c:
        import time
        for _ in range(50):  # seeding runs as a background task on start-up
            o = c.get("/api/overview").json()
            if o["upcoming_7d"] and o["calls_reviewed"] >= 4:
                break
            time.sleep(0.1)
        assert o["public_demo"] and o["upcoming_7d"] > 10 and o["waitlist"] >= 2
        cal = c.get("/api/calendar").json()
        assert cal["hours"], "default calendar day must be an opening day"
        r = c.post("/api/demo/recovery", json={}).json()
        assert r["ok"] and [s["step"] for s in r["steps"]][:3] == ["Booked", "Cancelled by caller", "Matched & held"]
        assert c.get("/api/overview").json()["stats"]["slots_recovered"] == 1
        again = c.post("/api/demo/recovery", json={}).json()
        assert again["ok"] is False
        assert c.post("/api/demo/reset", json={}).json()["ok"]
        assert c.post("/api/demo/recovery", json={}).json()["ok"]  # playable again after reset


def test_static_assets_are_whitelisted(clock):
    with TestClient(demo_app(clock)) as c:
        assert c.get("/static/app.js").status_code == 200
        assert c.get("/static/architecture.svg").headers["content-type"].startswith("image/svg")
        assert c.get("/static/..%2Fmain.py").status_code == 404
        assert c.get("/static/main.py").status_code == 404


def test_rate_limiter():
    rl = RateLimiter(per_client=3, window_s=60, daily_cap=4)
    assert [rl.allow("a") for _ in range(4)] == [True, True, True, False]
    assert rl.allow("b") is True and rl.allow("c") is False  # global daily cap reached


def test_public_agent_calls_are_rate_limited(clock):
    app = demo_app(clock)
    app.state.limiter.per_client = 2
    with TestClient(app) as c:
        codes = [c.post("/agents/prompt-lab", json={"message": "Hallo"}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert c.post("/agents/prompt-lab", json={"message": "Hallo"}, headers={"X-Admin-Token": "owner"}).status_code == 200
