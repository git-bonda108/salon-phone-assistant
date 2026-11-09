# 0004 · All processing in Switzerland; regional model deployments only

**Context.** Suisse IT's products promise Swiss hosting and DSG compliance. Callers' names, numbers and transcripts are personal data. Cloud model deployments of type *Global* or *DataZone* may process requests outside the chosen region.

**Decision.**
* Voice: Suisse Line (Swiss data centres).
* Backend, web app, agents and AI services: **Azure Switzerland North** only, with **regional (Standard)** model deployments, never Global.
* Calendar: the salon's own calendar. A Microsoft 365 tenant can keep data in Microsoft's Swiss datacenter geography; Google Calendar is the salon's own choice and is documented as such in the data map.
* Content Safety, Language and Speech resources are all created in Switzerland North (Prompt Shields and harm detection are available there).
* **Agent traces stay local.** The Agents SDK exports traces to OpenAI by default; that exporter is replaced by a local store plus App Insights in Switzerland North.

**Consequences.** Model choice is limited to what is offered regionally in Switzerland North. At the time of writing that is `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o` and `gpt-5.1` for *Standard* deployments; we use `gpt-4.1-mini` for the agents and optionally `gpt-5.1` as judge. The data map in [safety](../safety/guardrails.md#data-map) lists every store and where it lives. Azure is a US provider, which some Swiss customers weigh under the CLOUD Act. The design keeps models and AI services behind adapters, so they can be moved to fully Swiss-operated hosting.
