from __future__ import annotations

import os
from typing import Any
import importlib

from autopilot.storage import Store


class TraceSink:
    """Local-first trace sink with optional Omium SDK forwarding."""

    def __init__(self, store: Store):
        self.store = store
        self.api_key = os.getenv("OMIUM_API_KEY")
        self.workflow = os.getenv("OMIUM_WORKFLOW", "autopilot-runtime")
        self.client: Any | None = None
        self.mode = "local"
        self.error: str | None = None
        if self.api_key:
            self._init_omium()

    def emit(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None = None,
    ) -> None:
        enriched = {
            **payload,
            "omium_enabled": bool(self.client),
            "trace_provider": self.mode,
            "omium_error": self.error,
        }
        self.store.trace(mission_id, name, status, enriched, parent_step_id)
        self._emit_omium(mission_id, name, status, enriched, parent_step_id)

    def _init_omium(self) -> None:
        try:
            module = importlib.import_module("omium")
        except Exception as exc:
            self.mode = "local+omium-sdk-missing"
            self.error = str(exc)
            return

        try:
            if hasattr(module, "Client"):
                self.client = module.Client(api_key=self.api_key)
            elif hasattr(module, "OmiumClient"):
                self.client = module.OmiumClient(api_key=self.api_key)
            else:
                self.client = module
            self.mode = "local+omium"
        except Exception as exc:
            self.client = None
            self.mode = "local+omium-init-failed"
            self.error = str(exc)

    def _emit_omium(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None,
    ) -> None:
        if not self.client:
            return
        event = {
            "workflow": self.workflow,
            "mission_id": mission_id,
            "name": name,
            "status": status,
            "payload": payload,
            "parent_id": parent_step_id,
        }
        try:
            for method_name in ("trace", "track", "event", "log"):
                method = getattr(self.client, method_name, None)
                if method:
                    method(**event)
                    return
            self.error = "Omium SDK loaded but no supported trace method was found."
        except TypeError:
            try:
                method(event)
            except Exception as exc:
                self.error = str(exc)
        except Exception as exc:
            self.error = str(exc)
