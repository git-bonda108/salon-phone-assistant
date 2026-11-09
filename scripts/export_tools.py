"""Regenerate suisse-line/tools.json from the tool registry (app/tools.py).

    python scripts/export_tools.py           # write the file
    python scripts/export_tools.py --check   # exit 1 if the committed file is out of date (used in CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.salon import load_salon  # noqa: E402
from app.tools import suisse_line_tools  # noqa: E402

OUT = ROOT / "suisse-line" / "tools.json"


def render() -> str:
    return json.dumps(suisse_line_tools(load_salon(ROOT / "data" / "salon.json")), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        ok = OUT.read_text(encoding="utf-8") == text
        print("tools.json is up to date" if ok else "tools.json is OUT OF DATE: run python scripts/export_tools.py")
        sys.exit(0 if ok else 1)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
