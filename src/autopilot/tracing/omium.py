"""Observability sink: SQLite (always) plus optional Omium.

Omium does **not** expose a public ``POST /api/v1/traces`` ingest URL for arbitrary
JSON blobs — that legacy constant caused HTTP 405 in production logs.

Supported paths:

1. **Official SDK** (recommended for dashboard traces): install ``omium``, set
   ``OMIUM_SDK_INIT=1``, ``OMIUM_API_KEY``, and call ``omium.init()`` from app
   startup (see ``autopilot.api.main`` lifespan). Decorate hot paths with
   ``@omium.trace`` where needed.

2. **Custom HTTP relay** (advanced): set ``OMIUM_HTTP_INGEST_URL`` to an endpoint
   *you* operate that accepts our event JSON; ``OMIUM_API_KEY`` is sent as
   ``X-API-Key`` (Omium-compatible auth header).

Without (1) or (2), events remain in SQLite only — still the authoritative local audit trail.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import timezone
from typing import Any

import httpx

from autopilot.storage import Store

log = logging.getLogger("autopilot.tracing")

DEFAULT_OMIUM_RELAY_URL = "https://api.omium.ai/api/v1/traces" # Legacy/Placeholder

class TraceSink:
    """Emits trace events to SQLite (always) and optionally to a remote HTTP relay.

    Each event is structured with:
      - mission_id: links the event to a mission
      - parent_step_id: causal chain link for sub-agent tracing
      - name: dot-separated event name (e.g. "agent.github_search.complete")
      - status: "started" | "complete" | "failed" | "skipped"
      - payload: arbitrary structured metadata
      - duration_ms: elapsed time since last event (when available)
    """

    def __init__(self, store: Store):
        self._store = store
        self._api_key = os.getenv("OMIUM_API_KEY", "").strip()
        self._http_url = os.getenv("OMIUM_HTTP_INGEST_URL", "").strip()
        self._http_enabled = bool(self._api_key and self._http_url)
        self._session_id = f"autopilot-{int(time.time())}"
        if self._http_enabled:
            log.info(
                "Omium-compatible HTTP ingest enabled (session=%s url=%s)",
                self._session_id,
                self._http_url[:48] + ("…" if len(self._http_url) > 48 else ""),
            )
        elif self._api_key and not self._http_url:
            log.debug(
                "OMIUM_API_KEY set but OMIUM_HTTP_INGEST_URL unset — SQLite traces only "
                "(enable Omium SDK with OMIUM_SDK_INIT=1 or supply a relay URL)."
            )
        else:
            log.debug("Omium HTTP relay disabled (no OMIUM_API_KEY)")

    def emit(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Emit a trace event synchronously to SQLite, async HTTP relay when configured."""
        self._store.trace(mission_id, name, status, payload, parent_step_id)

        if self._http_enabled:
            self._ship_http(mission_id, name, status, payload, parent_step_id, duration_ms)

    def emit_agent_step(
        self,
        mission_id: str | None,
        agent_id: str,
        agent_role: str,
        step_number: int,
        tool_name: str | None,
        status: str,
        duration_ms: float | None = None,
        result_summary: str | None = None,
    ) -> None:
        """Structured trace for sub-agent tool invocations."""
        payload = {
            "agent_id": agent_id,
            "agent_role": agent_role,
            "step": step_number,
            "tool": tool_name,
            "result": result_summary,
        }
        if duration_ms is not None:
            payload["duration_ms"] = round(duration_ms, 1)

        event_name = f"agent.{agent_role.lower().replace(' ', '_')}"
        if tool_name:
            event_name += f".{tool_name}"
        event_name += f".{status}"

        self._store.trace(mission_id, event_name, status, payload)

        if self._http_enabled:
            self._ship_http(mission_id, event_name, status, payload, None, duration_ms)

    def _ship_http(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None,
        duration_ms: float | None,
    ) -> None:
        """Fire-and-forget HTTP POST to user-configured ingest URL."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_ship_http(mission_id, name, status, payload, parent_step_id, duration_ms))
        except RuntimeError:
            # No event loop running — skip async ship (startup/teardown)
            pass

    async def _async_ship_http(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None,
        duration_ms: float | None,
    ) -> None:
        event = {
            "session_id": self._session_id,
            "mission_id": mission_id,
            "parent_step_id": parent_step_id,
            "name": name,
            "status": status,
            "payload": payload,
            "timestamp": _now_iso(),
        }
        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms, 1)

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    self._http_url,
                    headers={
                        "X-API-Key": self._api_key,
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(event, default=str),
                )
                if resp.status_code >= 400:
                    log.warning("Omium ingest returned HTTP %s; disabling remote tracing for this process", resp.status_code)
                    self._http_enabled = False
        except Exception as exc:
            log.debug("Omium HTTP ingest failed (non-critical): %s", exc)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(timezone.utc).isoformat()
