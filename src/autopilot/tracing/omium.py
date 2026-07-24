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
from datetime import UTC
from typing import Any

import httpx

from autopilot.storage import Store

log = logging.getLogger("autopilot.tracing")

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
        self._sdk_requested = _env_enabled("OMIUM_SDK_INIT") or _env_enabled("OMIUM_TRACING")
        self._session_id = f"autopilot-{int(time.time())}"
        self._last_remote_status: str = "disabled"
        self._last_remote_error: str | None = None
        self._last_remote_event_at: str | None = None
        self._last_sdk_event_at: str | None = None
        if self._http_enabled:
            self._last_remote_status = "relay_configured"
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

    def status(self) -> dict[str, Any]:
        sdk_importable = False
        sdk_initialized = False
        if self._sdk_requested:
            try:
                import omium
                sdk_importable = True
                sdk_initialized = bool(getattr(omium, "is_initialized", lambda: False)())
            except ImportError:
                sdk_importable = False
        return {
            "local_sqlite": True,
            "session_id": self._session_id,
            "api_key_configured": bool(self._api_key),
            "sdk_requested": self._sdk_requested,
            "sdk_importable": sdk_importable,
            "sdk_initialized": sdk_initialized,
            "http_relay_configured": bool(self._http_url),
            "http_relay_enabled": self._http_enabled,
            "remote_status": self._last_remote_status,
            "last_remote_error": self._last_remote_error,
            "last_remote_event_at": self._last_remote_event_at,
            "last_sdk_event_at": self._last_sdk_event_at,
            "proof_mode": (
                "http_relay"
                if self._http_enabled
                else "sdk_live" if sdk_initialized and self._last_sdk_event_at
                else "sdk_initialized" if sdk_initialized
                else "sdk_requested" if self._sdk_requested and sdk_importable
                else "local_only"
            ),
        }

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

        if self._sdk_requested:
            self._ship_sdk(mission_id, name, status, payload, parent_step_id, duration_ms)

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

        if self._sdk_requested:
            self._ship_sdk(mission_id, event_name, status, payload, None, duration_ms)

        if self._http_enabled:
            self._ship_http(mission_id, event_name, status, payload, None, duration_ms)

    def _ship_sdk(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None,
        duration_ms: float | None,
    ) -> None:
        """Emit an Omium SDK span/checkpoint when the official SDK is initialized."""
        try:
            import omium
            if not getattr(omium, "is_initialized", lambda: False)():
                self._last_remote_status = "sdk_not_initialized"
                self._last_remote_error = "Omium SDK requested but not initialized."
                return
            if mission_id and hasattr(omium, "set_execution_id"):
                try:
                    omium.set_execution_id(mission_id)
                except Exception:
                    pass

            event = {
                "session_id": self._session_id,
                "mission_id": mission_id,
                "parent_step_id": parent_step_id,
                "name": name,
                "status": status,
                "payload": payload,
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
                "timestamp": _now_iso(),
            }

            def _record_omium_event(data: dict[str, Any]) -> dict[str, Any]:
                return {
                    "name": data["name"],
                    "status": data["status"],
                    "mission_id": data["mission_id"],
                    "payload_keys": sorted((data.get("payload") or {}).keys()),
                }

            span_type = "tool" if ".tool" in name or name.startswith("agent.") else "function"
            traced = omium.trace(
                name=name,
                span_type=span_type,
                capture_input=True,
                capture_output=True,
                capture_errors=True,
                mission_id=mission_id,
                parent_step_id=parent_step_id,
                event_status=status,
            )(_record_omium_event)
            if _env_enabled("OMIUM_CHECKPOINTS") and hasattr(omium, "checkpoint"):
                traced = omium.checkpoint(name=f"{name}.checkpoint", on_error="skip")(traced)
            traced(event)
            self._last_remote_status = "sdk_traced"
            self._last_remote_error = None
            self._last_sdk_event_at = _now_iso()
        except Exception as exc:
            self._last_remote_status = "sdk_failed"
            self._last_remote_error = str(exc)[:200]
            log.debug("Omium SDK trace failed (non-critical): %s", exc)

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
                    self._last_remote_status = "failed"
                    self._last_remote_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    log.warning("Omium relay returned HTTP %s; disabling remote tracing for this process", resp.status_code)
                    self._http_enabled = False
                else:
                    self._last_remote_status = "delivered"
                    self._last_remote_error = None
                    self._last_remote_event_at = _now_iso()
        except Exception as exc:
            self._last_remote_status = "failed"
            self._last_remote_error = str(exc)[:200]
            log.debug("Omium HTTP ingest failed (non-critical): %s", exc)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat()


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
