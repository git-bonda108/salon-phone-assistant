# 0003 · One tool registry, exposed as HTTP live tools and as an MCP server

**Context.** Suisse Line calls tools over HTTPS. Our own agents (call reviewer, prompt lab, evaluation lab, owner copilot) and any MCP client (Claude, Copilot Studio, Foundry agents) speak the Model Context Protocol. Two hand-maintained tool lists would drift apart.

**Decision.** Each tool is defined once in `app/tools.py` (name, description, input model, handler) and published as `POST /tools/{name}` for Suisse Line, as MCP servers (`/mcp/caller`, `/mcp/owner`) for agents, and as `suisse-line/tools.json` (generated; CI fails if it is stale). Our own agents speak MCP to these servers **in-process**: same protocol and tool list as external clients, no network hop. Owner tools are read-only and never reachable with the caller key.

**Consequences.** What the evaluation lab tests is exactly what the phone agent uses. The MCP surface also gives future products (for example a Suisse Meets follow-up assistant) a standard way to use salon tools.
