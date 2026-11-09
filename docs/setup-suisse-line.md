# Setting up the agent in Suisse Line

Time needed: about 20 minutes. Prerequisite: the backend is deployed and reachable over HTTPS (`https://<your-backend>/health` returns `ok`).

## 1. Trial account
1. Go to <https://suisse-line.ch> → **7 Tage kostenlos testen** (7 days, 60 minutes, no credit card).
2. Choose **Ohne Website starten** (start without a website). The salon is fictional, so we provide the knowledge base ourselves.

## 2. Create the inbound agent
| Field in the portal | Value |
| --- | --- |
| Name | `Salon Edelweiss – Empfang` |
| Language / voice | Schweizerdeutsch (Zürich), female voice; enable FR / IT / EN if offered |
| Who speaks first | Agent |
| Greeting | contents of [`suisse-line/greeting.txt`](../suisse-line/greeting.txt) |
| System prompt | contents of [`suisse-line/system-prompt.md`](../suisse-line/system-prompt.md) |
| Opening hours | Tue–Fri 09:00–18:30, Thu until 20:00, Sat 08:00–16:00, public holidays canton ZH. The assistant answers 24/7; the hours are used for forwarding |
| Forwarding / escalation | salon's mobile number for complaints and "talk to a person" during opening hours |
| E-mail summary | salon's e-mail address (built in, no code needed) |

## 3. Knowledge base
Upload [`suisse-line/knowledge-base/salon-edelweiss.md`](../suisse-line/knowledge-base/salon-edelweiss.md) as a document (or paste it as text). For a real salon you could also let Suisse Line read the salon website.

## 4. Live tools ("Live-Werkzeuge")
Create one tool per entry in [`suisse-line/tools.json`](../suisse-line/tools.json):

| Setting | Value |
| --- | --- |
| Name / description / parameters | copy from `tools.json` (the JSON schema uses only keywords Suisse Line accepts) |
| Method | `POST` |
| URL | `https://<your-backend>` + `path` (e.g. `https://<your-backend>/tools/check_availability`) |
| Header | `X-Api-Key: <TOOL_API_KEY>` |
| Body | the tool arguments as JSON. The backend accepts them flat or wrapped in `arguments` / `parameters` |

If the portal can pass the **caller ID** to tools as a fixed variable, map it to the `phone` parameter of `lookup_caller`, `find_appointments`, `cancel_appointment` and `reschedule_appointment`. This is the most important hardening step (see design notes).

Tool results always come back with HTTP 200 and `{"ok": …, "message": …}`, answered in well under the 30-second tool timeout.

## 5. Webhook (call log)
**Integrationen → Webhooks** → event `call.completed` (optionally also `call.forwarded`) → URL `https://<your-backend>/webhooks/suisse-line`, signing enabled with the same secret as `WEBHOOK_SECRET`. If the portal uses a signature header other than `X-Signature`, set `WEBHOOK_SIGNATURE_HEADER`.

## 6. Waitlist offers (outbound)
1. Create a second agent **outbound** from [`suisse-line/outbound-offer-agent.md`](../suisse-line/outbound-offer-agent.md), with the live tools `accept_offer` and `decline_offer`.
2. In n8n / Make / Zapier: webhook trigger (URL → `NOTIFY_WEBHOOK_URL`) → filter `type == "waitlist.offer"` → start an outbound call through the Suisse Line API with the payload fields as variables. Fallback: send an SMS ("A slot freed up – call us back before HH:MM"). When the customer calls back, the inbound agent sees the offer through `lookup_caller`.

## 7. Phone number and test
Assign a Swiss number in the portal (or forward the salon's existing number), then run the manual test matrix in [`test-plan.md`](test-plan.md). Check the results in the Suisse Line dashboard (transcripts, summaries) and in `https://<your-backend>/admin?token=…`.
