"""Diagram as code: renders docs/architecture/system-architecture.svg.

    python scripts/render_architecture.py

Hand-laid coordinates (no diagram tool) so the drawing stays exact and reviewable in diffs.
Update the BUILT / NEXT tags here when a component ships.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'system-architecture.svg'
from xml.sax.saxutils import escape as e

W, H = 1600, 1060
INK, SLATE, MUTED, LINE = "#0B1220", "#334155", "#64748B", "#CBD5E1"
LAKE, RED, PINE, AMBER = "#1B5FD1", "#E2231A", "#13804B", "#C77700"
out = []
add = out.append

def text(x, y, s, size=12.5, weight=400, fill=SLATE, anchor="start", ls=None, family="Inter, 'Segoe UI', system-ui, sans-serif"):
    extra = f' letter-spacing="{ls}"' if ls else ""
    add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>{e(s)}</text>')

def box(x, y, w, h, title, lines=(), tag=None, fill="#FFFFFF", stroke=LINE, title_size=15, bar=None):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
    if bar:
        add(f'<rect x="{x}" y="{y+12}" width="4" height="{h-24}" rx="2" fill="{bar}"/>')
    text(x + 18, y + 28, title, size=title_size, weight=650, fill=INK)
    for i, l in enumerate(lines):
        text(x + 18, y + 50 + i * 19, l)
    if tag:
        status(x + w - 14, y + 14, tag)

def status(rx, y, tag):
    built = tag == "BUILT"
    w = 50 if built else 46
    add(f'<rect x="{rx-w}" y="{y}" width="{w}" height="18" rx="9" fill="{"#E7F5EE" if built else "#FDF3E1"}"/>')
    text(rx - w / 2, y + 12.5, tag, size=9.5, weight=700, fill=PINE if built else AMBER, anchor="middle", ls="0.6")

def pill(x, y, w, h, title, sub, tag=None, fill="#FFFFFF"):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{LINE}"/>')
    text(x + 12, y + 24, title, size=13, weight=650, fill=INK)
    for i, s in enumerate(sub if isinstance(sub, (list, tuple)) else [sub]):
        text(x + 12, y + 43 + i * 16, s, size=11.5, fill=MUTED)
    if tag:
        status(x + w - 8, y + 8, tag)

def shield(cx, cy, label):
    add(f'<g transform="translate({cx},{cy})"><path d="M0,-13 L11,-8.5 V1.5 C11,9 0,14 0,14 C0,14 -11,9 -11,1.5 V-8.5 Z" fill="{PINE}"/>')
    add(f'<text x="0" y="3.5" font-family="Inter, system-ui, sans-serif" font-size="9" font-weight="800" fill="#fff" text-anchor="middle">{label}</text></g>')

def num(cx, cy, label, color=LAKE):
    add(f'<circle cx="{cx}" cy="{cy}" r="11" fill="{color}"/>')
    text(cx, cy + 4.2, label, size=11.5, weight=800, fill="#fff", anchor="middle")

def arrow(d, color=LAKE, dashed=False, both=False):
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    start = f' marker-start="url(#a-{color[1:]}-s)"' if both else ""
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{dash} marker-end="url(#a-{color[1:]})"{start}/>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">')
add('<title id="t">Lina: system architecture</title><desc id="d">Caller talks to the Suisse Line agent, which calls the deterministic booking core in Azure Switzerland North through live tools; the core reads and writes the salon calendar. After each call a signed webhook triggers the call-reviewer agent, which uses Azure AI services as tools. Cancellations trigger the waitlist offer through the Suisse Line outbound agent.</desc>')
add('<defs>')
for c in (LAKE, RED, PINE, SLATE):
    add(f'<marker id="a-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
    add(f'<marker id="a-{c[1:]}-s" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="{c}"/></marker>')
add('</defs>')
add(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# header
text(48, 60, "Lina · AI phone assistant for Salon Edelweiss", size=27, weight=750, fill=INK)
text(48, 90, "System architecture · all processing in Switzerland: Suisse Line (voice) + Azure Switzerland North (logic, agents, AI services)", size=14.5, fill=MUTED)
add(f'<rect x="1520" y="36" width="36" height="36" rx="5" fill="{RED}"/><rect x="1535" y="43" width="6" height="22" fill="#fff"/><rect x="1527" y="51" width="22" height="6" fill="#fff"/>')

# actors
text(40, 150, "PEOPLE", size=11, weight=750, fill=MUTED, ls="1.2")
box(40, 170, 175, 92, "Caller", ["any hour, own language", "DE-CH · DE · FR · IT · EN"], bar=LAKE, title_size=14.5)
box(40, 430, 175, 92, "Waitlisted customer", ["wanted a full day", "gets the freed slot"], bar=PINE, title_size=14.5)
box(40, 818, 175, 92, "Owner & stylists", ["web app (Entra ID)", "own calendar, as before"], bar=SLATE, title_size=14.5)

# Suisse Line zone
add(f'<rect x="248" y="128" width="340" height="540" rx="18" fill="#FFF7F6" stroke="{RED}" stroke-width="1.5" stroke-dasharray="7 5"/>')
text(268, 154, "SUISSE LINE · SWISS DATA CENTRES", size=11, weight=800, fill=RED, ls="1.1")
box(270, 172, 296, 152, "Inbound agent “Lina”", ["Swiss-German STT · dialect voice", "system prompt · knowledge base", "9 live tools over HTTPS (API key)", "forwarding to a human · e-mail summary"], tag="BUILT")
shield(552, 316, "L2")
box(270, 350, 296, 96, "Outbound offer agent", ["calls the waitlisted customer", "accept_offer / decline_offer"], tag="BUILT")
box(270, 472, 296, 96, "Signed webhooks", ["call.completed (+ transcript)", "HMAC-SHA256"], tag="BUILT")
text(270, 600, "Swiss phone number · call forwarding ·", size=11.5, fill=MUTED)
text(270, 617, "dashboard, transcripts, summaries", size=11.5, fill=MUTED)
text(270, 646, "configured from suisse-line/ in this repo", size=11.5, weight=600, fill=RED)

# Azure zone
add(f'<rect x="622" y="128" width="740" height="880" rx="18" fill="#F4F7FD" stroke="{LAKE}" stroke-width="1.5" stroke-dasharray="7 5"/>')
text(642, 154, "AZURE · SWITZERLAND NORTH · REGIONAL ONLY", size=11, weight=800, fill=LAKE, ls="1.1")

box(645, 172, 405, 196, "Booking core · deterministic", [
    "availability: durations, skills, days, buffers",
    "book · move · cancel · idempotent, locked",
    "Empty-Chair Recovery: waitlist → offer → cascade",
    "callers act only on their own bookings",
], tag="BUILT")
pill(663, 312, 170, 40, "HTTPS live tools", [], tag=None)
pill(845, 312, 140, 40, "MCP server", [], tag=None)
status(1036, 324, "BUILT")
text(1000, 338, "", size=1)
shield(1036, 296, "L1")

box(1072, 172, 268, 196, "Data & platform", [
    "store: waitlist · offers · call log",
    "Key Vault: third-party secrets",
    "managed identity: keyless access",
    "App Insights: traces · metrics",
    "Container Apps · Registry · Bicep",
], tag="BUILT")

box(645, 392, 695, 186, "Agents · OpenAI Agents SDK · tools via MCP", [], tag="BUILT")
pill(663, 434, 156, 84, "Call reviewer", ["after every call", "verify · redact · flag"])
pill(831, 434, 156, 84, "Prompt lab", ["text twin of Lina", "sandbox calendar"])
pill(999, 434, 156, 84, "Evaluation lab", ["AI callers + judge", "incl. attackers"], tag="NEXT")
pill(1167, 434, 156, 84, "Owner copilot", ["questions, insights", "read-only tools"])
text(663, 548, "input guardrail: Prompt Shields · output guardrail: every time and price must come from a tool result", size=11.5, fill=MUTED)
shield(1318, 545, "L3")
shield(1143, 506, "L5")

box(645, 602, 695, 150, "Azure AI services · called as tools", [], tag="BUILT")
pill(663, 642, 156, 92, "Azure OpenAI", ["in Foundry", "regional deployment"])
pill(831, 642, 156, 92, "AI Speech", ["TTS de-CH voices", "films · B prototype"], tag="NEXT")
pill(999, 642, 156, 92, "AI Language", ["PII redaction", "language · sentiment"])
pill(1167, 642, 156, 92, "Content Safety", ["Prompt Shields", "harm categories"])
shield(1309, 650, "L4")

box(645, 776, 695, 208, "Web app · owner cockpit and demo", [], tag="BUILT")
chips = ["Overview", "Calendar", "Recovery", "Call reviews", "Talk to Lina", "Copilot", "Quality", "How it works"]
cx = 663
for c in chips:
    w = 18 + len(c) * 7.4
    add(f'<rect x="{cx}" y="818" width="{w}" height="28" rx="14" fill="#FFFFFF" stroke="{LINE}"/>')
    text(cx + w / 2, 836.5, c, size=12, weight=600, fill=SLATE, anchor="middle")
    cx += w + 8
text(663, 878, "live bookings and waitlist · call reviews with agent traces · prompt lab next to the Suisse Line voice widget", size=12, fill=SLATE)
text(663, 898, "evaluation scores and red-team results · guardrail counters · data map · films and interactive architecture", size=12, fill=SLATE)
pill(663, 916, 200, 50, "Entra ID sign-in", ["owner actions only"])
text(880, 946, "reads the booking core and agents through internal APIs", size=11.5, fill=MUTED)

# external column
text(1395, 150, "SALON'S OWN", size=11, weight=750, fill=MUTED, ls="1.2")
box(1395, 172, 175, 132, "Calendar", ["Google Calendar", "Microsoft 365 (Graph)", "1 calendar / stylist", "staff edit as usual"], bar=PINE, tag=None, title_size=14.5)
status(1556, 178, "BUILT")
text(1395, 800, "DELIVERY", size=11, weight=750, fill=MUTED, ls="1.2")
box(1395, 818, 175, 132, "GitHub", ["CI: 85 tests + contract", "Actions → Azure (OIDC)", "no stored secrets", "azd + Bicep"], bar=SLATE, title_size=14.5)

# flows
arrow("M215,212 H268", LAKE); num(241, 196, "1")
arrow("M566,250 H643", LAKE); num(604, 234, "2")
arrow("M1020,170 V112 H1378 V238 H1393", LAKE, both=True); num(1200, 112, "3")
text(1218, 106, "free/busy · events", size=11.5, weight=600, fill=LAKE)
arrow("M566,520 H600 V476 H661", RED); num(604, 498, "4", RED)
arrow("M990,578 V600", SLATE, both=True)
text(998, 594, "tools via MCP", size=11, weight=600, fill=SLATE)
arrow("M915,392 V370", SLATE, both=True)
text(923, 386, "MCP", size=11, weight=600, fill=SLATE)
arrow("M645,345 H610 V398 H568", PINE, both=True); num(610, 371, "A", PINE)
arrow("M270,410 H236 V476 H217", PINE); num(236, 443, "B", PINE)
arrow("M215,864 H643", SLATE); num(430, 864, "C", SLATE)
arrow("M1395,884 H1342", SLATE)
text(1346, 876, "deploy", size=11, weight=600, fill=SLATE, anchor="end")

# legend
add(f'<line x1="40" y1="1024" x2="1560" y2="1024" stroke="{LINE}"/>')
lx = 40
num(lx + 11, 1044, "1", LAKE); text(lx + 28, 1048, "call → booking: live tool → calendar", size=12, fill=SLATE)
num(lx + 311, 1044, "4", RED); text(lx + 328, 1048, "after the call: signed webhook → call reviewer", size=12, fill=SLATE)
num(lx + 651, 1044, "A", PINE); text(lx + 668, 1048, "cancellation → waitlist offer → outbound call", size=12, fill=SLATE)
shield(lx + 1003, 1044, "L1"); text(lx + 1020, 1048, "guardrail layers L1–L5", size=12, fill=SLATE)
status(lx + 1240, 1035, "BUILT"); status(lx + 1290, 1035, "NEXT"); text(lx + 1298, 1048, "status as of this commit", size=12, fill=SLATE)
add('</svg>')
open(OUT, 'w', encoding='utf-8').write("\n".join(out))
print("ok")
