"""Automated conversation tests against the *real* Suisse voice agent.

Uses the Suisse Speech API realtime agent (wss://api.suisse-speech.ch/v1/agent/stream)
in text mode: we start a session with our system prompt + live tools, type the
caller's lines with `text.input`, answer the agent's `tool.call`s from the
backend (in-process, fresh database per scenario) and then assert which tools
were called and what ended up in the calendar.

    export SUISSE_API_KEY=sv_...          # account with the voice agent enabled
    python scripts/agent_eval.py          # all scenarios in tests/conversations/
    python scripts/agent_eval.py booking_swiss_german

STATUS: written against the protocol in the public API reference (v1.9.0).
It needs an API key with the voice agent released for the account, so it is
not part of CI; the deterministic tool tests (pytest) and
scripts/simulate_call.py cover the backend without a key.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("SUISSE_AGENT_URL", "wss://api.suisse-speech.ch/v1/agent/stream")
PROMPT = (ROOT / "suisse-line" / "system-prompt.md").read_text(encoding="utf-8")
GREETING = (ROOT / "suisse-line" / "greeting.txt").read_text(encoding="utf-8").strip()
TOOLS = [{k: t[k] for k in ("name", "description", "parameters")}
         for t in json.loads((ROOT / "suisse-line" / "tools.json").read_text())["tools"]]
QUIET_S = 2.5  # no new reply for this long after reply.done => the agent's turn is over


async def run_scenario(sc: dict, api_key: str) -> dict:
    backend = TestClient(create_app(Settings(database_path=":memory:", sweep_interval_seconds=0)))
    for setup in sc.get("setup", []):  # e.g. pre-existing bookings
        backend.post(f"/tools/{setup['tool']}", json=setup["args"])
    tool_log: list[dict] = []
    transcript: list[str] = []

    async with websockets.connect(URL, additional_headers={"X-API-Key": api_key}, max_size=2**20) as ws:
        await ws.send(json.dumps({
            "type": "session.start", "instructions": PROMPT, "tools": TOOLS, "greeting": GREETING,
            "language": sc.get("language", "de-CH"), "dialect": "zurich",
            "languages": ["de-CH", "en-US", "fr-CH", "it-CH"][: sc.get("n_languages", 4)],
            "turn_taking": {"mode": "manual", "interruptions": False},
            "transcripts": True, "end_conversation": True, "max_duration_s": 600,
        }))
        await ws.send(json.dumps({"type": "context.add", "text": f"Caller ID: {sc['caller_phone']}"}))

        async def agent_turn():
            last_activity = time.monotonic()
            replying = False
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    if not replying and time.monotonic() - last_activity > QUIET_S:
                        return
                    continue
                if isinstance(raw, bytes):
                    continue  # audio frames — not needed in text tests
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "reply.started":
                    replying = True
                elif t == "reply.done":
                    replying = False
                    last_activity = time.monotonic()
                    if ev.get("transcript"):
                        transcript.append(f"Lina:   {ev['transcript']}")
                elif t == "tool.call":
                    out = backend.post(f"/tools/{ev['name']}", json=ev["arguments"]).json()
                    tool_log.append({"name": ev["name"], "arguments": ev["arguments"], "ok": out.get("ok")})
                    await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"], "output": out}))
                    last_activity = time.monotonic()
                elif t == "error":
                    raise RuntimeError(f"agent error: {ev}")
                elif t in ("session.end_requested", "session.complete"):
                    return

        await agent_turn()  # greeting
        for line in sc["turns"]:
            transcript.append(f"Caller: {line}")
            await ws.send(json.dumps({"type": "text.input", "text": line}))
            await agent_turn()
        await ws.send(json.dumps({"type": "session.end"}))

    return check(sc, backend, tool_log, transcript)


def check(sc: dict, backend: TestClient, tool_log: list[dict], transcript: list[str]) -> dict:
    failures = []
    called = [t["name"] for t in tool_log]
    for name in sc.get("expect_tools", []):
        if name not in called:
            failures.append(f"expected tool {name} was not called (called: {called})")
    for name in sc.get("forbid_tools", []):
        if name in called:
            failures.append(f"tool {name} must not be called")
    exp = sc.get("expect_appointments")
    if exp:
        appts = backend.post("/tools/find_appointments", json={"phone": exp["phone"]}).json()["appointments"]
        if len(appts) != exp["count"]:
            failures.append(f"expected {exp['count']} appointment(s), found {len(appts)}")
        for key, value in exp.get("match", {}).items():
            if appts and not str(appts[0].get(key, "")).startswith(value):
                failures.append(f"appointment {key}={appts[0].get(key)!r}, expected {value!r}")
    return {"name": sc["name"], "passed": not failures, "failures": failures,
            "tools": tool_log, "transcript": transcript}


def main() -> int:
    key = os.environ.get("SUISSE_API_KEY")
    if not key:
        print("Set SUISSE_API_KEY (Suisse Speech API key with the voice agent enabled).")
        return 2
    files = sorted((ROOT / "tests" / "conversations").glob("*.json"))
    today = date.today()
    placeholders = {"{next_sat}": (today + timedelta(days=(5 - today.weekday()) % 7 or 7)).isoformat()}
    wanted = set(sys.argv[1:])
    results = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for k, v in placeholders.items():
            text = text.replace(k, v)
        sc = json.loads(text)
        if wanted and sc["name"] not in wanted:
            continue
        res = asyncio.run(run_scenario(sc, key))
        results.append(res)
        print(f"\n{'PASS' if res['passed'] else 'FAIL'}  {res['name']}")
        print("\n".join("   " + line for line in res["transcript"]))
        for t in res["tools"]:
            print(f"   ↳ {t['name']} {json.dumps(t['arguments'], ensure_ascii=False)} ok={t['ok']}")
        for fail in res["failures"]:
            print(f"   ✗ {fail}")
    out = ROOT / "var" / "agent_eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed — report: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
