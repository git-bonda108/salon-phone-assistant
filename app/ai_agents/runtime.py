"""Model access and tracing for the agents.

* Models: Azure OpenAI in Switzerland North, **regional (Standard) deployments**,
  keyless via managed identity. No endpoint configured = offline mode.
* Tracing: the Agents SDK exports traces to OpenAI by default. That would send
  transcripts and tool arguments out of Switzerland, so the default exporter is
  replaced by ``LocalTraceStore``: spans are kept in our own store (shown in the web
  app) and logged to App Insights through the standard logger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from agents import Model, OpenAIChatCompletionsModel, set_trace_processors
from agents.tracing import TracingProcessor

from ..config import Settings
from ..store import Store

log = logging.getLogger("agents.trace")


def make_model(settings: Settings, deployment: str | None = None) -> Model | None:
    """Azure OpenAI chat model with Entra ID auth, or None when no endpoint is configured."""
    if not settings.azure_openai_endpoint:
        return None
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AsyncAzureOpenAI

    token_provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    client = AsyncAzureOpenAI(azure_endpoint=settings.azure_openai_endpoint, azure_ad_token_provider=token_provider,
                              api_version=settings.azure_openai_api_version)
    return OpenAIChatCompletionsModel(model=deployment or settings.azure_openai_deployment, openai_client=client)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


class LocalTraceStore(TracingProcessor):
    """Keeps every agent span in our own database (Switzerland), nothing is exported."""

    def __init__(self, store: Store):
        self.store = store

    def on_trace_start(self, trace) -> None:
        try:
            self.store.add_trace(trace.trace_id, trace.name)
        except Exception:  # tracing must never break a run
            log.exception("trace start")

    def on_trace_end(self, trace) -> None:
        pass

    def on_span_start(self, span) -> None:
        pass

    def on_span_end(self, span) -> None:
        try:
            data = span.span_data.export() if span.span_data else {}
            kind = data.get("type", "span")
            name = data.get("name") or data.get("server") or kind
            err = span.error
            self.store.add_span({
                "span_id": span.span_id, "trace_id": span.trace_id, "parent_id": span.parent_id,
                "kind": kind, "name": name, "started_at": _iso(span.started_at), "ended_at": _iso(span.ended_at),
                "error": (err.get("message") if isinstance(err, dict) else str(err)) if err else None,
                "data": data,
            })
            log.info("span %s %s %s", kind, name, "error" if err else "ok")
        except Exception:
            log.exception("span end")

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


def install_local_tracing(store: Store) -> LocalTraceStore:
    """Replace the SDK's default (OpenAI) exporter with the local store."""
    proc = LocalTraceStore(store)
    set_trace_processors([proc])
    return proc
