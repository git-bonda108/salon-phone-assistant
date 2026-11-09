"""The Suisse Line agent config must respect the platform limits documented in the
Suisse Speech API reference (voice agent): JSON-Schema subset, name pattern,
description and size limits, max 32 tools — and must match our backend."""

import json
import re
from pathlib import Path

from app.main import TOOLS
from app.salon import load_salon

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "suisse-line" / "tools.json").read_text())
PROMPT = (ROOT / "suisse-line" / "system-prompt.md").read_text()
SALON = load_salon(ROOT / "data" / "salon.json")

ALLOWED = {"type", "properties", "required", "enum", "description", "items", "minimum", "maximum", "maxLength"}


def walk(schema, depth=1, path="parameters"):
    assert depth <= 6, f"{path} nested deeper than 6 levels"
    for key, value in schema.items():
        assert key in ALLOWED, f"{path}: keyword '{key}' is not accepted by Suisse Line"
    for name, sub in schema.get("properties", {}).items():
        walk(sub, depth + 1, f"{path}.{name}")
    if "items" in schema:
        walk(schema["items"], depth + 1, f"{path}[]")


def agent_tools():
    return [{k: t[k] for k in ("name", "description", "parameters")} for t in CFG["tools"]]


def test_limits():
    tools = agent_tools()
    assert len(tools) <= 32
    for t in tools:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t["name"])
        assert t["name"] not in ("end_conversation", "transfer_to_human")
        assert len(t["description"]) <= 1024
        walk(t["parameters"])
    tools_chars = len(json.dumps(tools, ensure_ascii=False))
    assert len(PROMPT) <= 16_000
    assert tools_chars <= 16_000
    assert len(PROMPT) + tools_chars <= 24_000


def test_tools_match_backend_models():
    for t in CFG["tools"]:
        assert t["path"] == f"/tools/{t['name']}"
        model = TOOLS[t["name"]][0]
        fields = model.model_fields
        props = t["parameters"]["properties"]
        assert set(props) <= set(fields), f"{t['name']}: unknown params {set(props) - set(fields)}"
        required_backend = {n for n, f in fields.items() if f.is_required()}
        assert required_backend <= set(t["parameters"].get("required", [])), t["name"]
    assert {t["name"] for t in CFG["tools"]} == set(TOOLS)


def test_enums_match_salon_config():
    service_ids = sorted(s.id for s in SALON.services)
    stylist_ids = {s.id for s in SALON.stylists}
    for t in CFG["tools"]:
        props = t["parameters"]["properties"]
        if "service" in props:
            assert sorted(props["service"]["enum"]) == service_ids, t["name"]
        if "stylist" in props:
            assert set(props["stylist"]["enum"]) - {"any"} == stylist_ids, t["name"]
