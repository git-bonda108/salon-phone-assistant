# 0003 · One tool registry, exposed as HTTP live tools and as an MCP server

**Context.** Suisse Line calls tools over HTTPS. Our own agents (call reviewer, prompt lab, evaluation lab, owner copilot) and any MCP client (Claude, Copilot Studio, Foundry agents) speak the Model Context Protocol. Two hand-maintained tool lists would drift apart.

**Decision.** Each tool is defined once (name, description, parameter schema, handler) and published twice: as `POST /tools/{name}` for Suisse Line, and as an MCP server for agents. A contract test checks that `suisse-line/tools.json`, the HTTP layer and the MCP server expose the same tools with the same parameters.

**Consequences.** What the evaluation lab tests is exactly what the phone agent uses. The MCP surface also gives future products (for example a Suisse Meets follow-up assistant) a standard way to use salon tools.
