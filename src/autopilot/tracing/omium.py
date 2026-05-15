"""Real Omium tracing integration for AUTOPILOT.

When OMIUM_API_KEY is set, every trace event is shipped to the Omium
observability platform via its REST ingest API.

When not configured, traces are emitted to the local SQLite store only
(which is always active).
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

OMIUM_INGEST_URL = "https://ingest.omium.dev/v1/traces"


class TraceSink:
    """Emits trace events to SQLite (always) and Omium (when configured).

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
        self._enabled = bool(self._api_key)
        self._session_id = f"autopilot-{int(time.time())}"
        if self._enabled:
            log.info("Omium tracing enabled (session=%s)", self._session_id)
        else:
            log.debug("Omium tracing disabled (no OMIUM_API_KEY)")

    def emit(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Emit a trace event synchronously to SQLite, async to Omium."""
        self._store.trace(mission_id, name, status, payload, parent_step_id)

        if self._enabled:
            self._ship_to_omium(mission_id, name, status, payload, parent_step_id, duration_ms)

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

        if self._enabled:
            self._ship_to_omium(mission_id, event_name, status, payload, None, duration_ms)

    def _ship_to_omium(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None,
        duration_ms: float | None,
    ) -> None:
        """Fire-and-forget HTTP POST to Omium ingest API."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_ship(mission_id, name, status, payload, parent_step_id, duration_ms))
        except RuntimeError:
            # No event loop running — skip async ship (startup/teardown)
            pass

    async def _async_ship(
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
                await client.post(
                    OMIUM_INGEST_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(event, default=str),
                )
        except Exception as exc:
            log.debug("Omium ingest failed (non-critical): %s", exc)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(timezone.utc).isoformat()
