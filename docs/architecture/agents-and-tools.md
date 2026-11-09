# Agents, tools and MCP

## One tool registry, three surfaces

Every tool is defined once in [`app/tools.py`](../../app/tools.py): name, description, input model and handler. Everything else is derived from it, so the phone agent, our agents and external MCP clients can never drift apart ([0003](../decisions/0003-one-tool-registry-http-and-mcp.md)).

| Surface | Who uses it | Auth | Tools |
| --- | --- | --- | --- |
| `POST /tools/{name}` | Suisse Line live tools (the phone agent) | `X-Api-Key` (tool key) | 9 caller tools |
| `/mcp/caller` (MCP, streamable HTTP) | prompt lab, evaluation lab, any MCP client acting for a caller | tool key | the same 9 caller tools |
| `/mcp/owner` (MCP, streamable HTTP) | owner copilot, call reviewer, owner's own MCP clients (e.g. Claude, Copilot Studio) | admin token | read-only: `list_appointments`, `list_waitlist`, `salon_stats`, `check_availability`, `find_appointments` + AI tools |
| `suisse-line/tools.json` | pasted into the Suisse Line portal | — | generated: `python scripts/export_tools.py`; CI fails if out of date |

Owner tools are **never** reachable with the caller key (tested). Write tools are **never** on the owner server: changes happen through the caller tools or in the calendar.

Our own agents use the MCP servers **in-process** ([`InProcessMCP`](../../app/mcp_server.py)): the real MCP protocol and the same tool list as external clients, without a network hop.

## Azure AI services as tools

[`app/ai_tools.py`](../../app/ai_tools.py). One Azure AI Services (Foundry) account in Switzerland North, keyless via managed identity.

| Tool | Service | Used by |
| --- | --- | --- |
| `prompt_shield` | Content Safety Prompt Shields, plus override-phrase rules (either signal counts; see [findings](../safety/guardrails.md#findings-from-the-live-system)) | input guardrail (prompt lab, copilot), call reviewer |
| `moderate_text` | Content Safety, harm categories | input guardrail, call reviewer |
| `redact_pii` | AI Language, PII detection (chunked to the 5,000-character limit) | call reviewer, **before anything is stored** |
| `analyze_sentiment`, `detect_language` | AI Language | call reviewer |

**Offline rules** answer when no endpoint is configured or Azure fails: phone, e-mail and IBAN redaction, injection phrases in DE/FR/IT/EN, abuse words. Redaction never fails open, and every result names its `engine`.

## The agents

[`app/ai_agents/team.py`](../../app/ai_agents/team.py). OpenAI Agents SDK, Azure OpenAI **regional** deployment in Switzerland North (`gpt-4.1-mini`; optional `gpt-5.1` judge).

| Agent | Runs | Tools | Guardrails | Why an agent |
| --- | --- | --- | --- | --- |
| **Prompt lab**: Lina in text | on demand (web app, evaluation lab) | caller MCP on a **sandbox calendar** | Prompt Shields + harm (input, *before* the model), grounding (output) | regression-test prompt changes cheaply; a test conversation can never book a real chair |
| **Call reviewer** | after every `call.completed` webhook, in the background | owner MCP (read-only) | deterministic checks run first; the agent can add attention, never remove it | judgement on messy transcripts: what was claimed, was it done, does the salon need to act |
| **Owner copilot** | in the web app | owner MCP (read-only) | Prompt Shields + harm, grounding | questions such as "who is waiting for Saturday?", "what did recovery earn this month?" |

The deterministic part of the call review always runs, with or without a model:
1. PII redaction (Azure AI Language + rules) before storage
2. Prompt Shields and harm check on the transcript
3. Claim check: booking language in the transcript but no booking recorded → `claim_without_record`
4. Sentiment

## Guardrails in the agent loop

| Guardrail | Type | Mechanism | Test |
| --- | --- | --- | --- |
| Prompt Shields + harm | input, `run_in_parallel=False` | blocks **before** the model is called: an attack costs no tokens | `test_prompt_injection_is_blocked_before_the_model_runs` (fails if switched to parallel) |
| Grounding | output, deterministic | every `HH:MM` and CHF amount in a reply must appear in a tool result, the knowledge base or the user's own words | `test_invented_time_is_blocked_by_output_guardrail` |
| Sandbox | architecture | prompt lab books into an in-memory calendar | `test_prompt_lab_books_only_in_the_sandbox` |
| Read-only owner scope | architecture | owner MCP server has no write tools | `test_owner_server_has_owner_and_ai_tools_but_no_write_tools` |
| Deterministic flags win | code | the reviewer model cannot clear a flag set by code | `test_reviewer_agent_…cannot_remove_deterministic_flags` |

Blocked turns are recorded as `guardrail_blocked` events and counted on the dashboard.

## Tracing stays in Switzerland

The Agents SDK sends traces to OpenAI by default, which would export transcripts and tool arguments. [`install_local_tracing`](../../app/ai_agents/runtime.py) replaces the exporter: every span (agent, model call, tool, guardrail) is stored in our database and logged to App Insights in Switzerland North. The web app shows them per conversation (`GET /api/traces/{id}`).

## How it is tested without paying for tokens

[`tests/test_agents.py`](../../tests/test_agents.py) replaces only the language model with a scripted `FakeModel`. The real Agents SDK loop, the real MCP servers, the real guardrails and the real booking core run. Each guardrail test was checked by breaking the guardrail and watching the test fail. Real-model behaviour is measured separately in the evaluation lab.
