from __future__ import annotations

import os
from typing import Any

from autopilot.storage import Store


class TraceSink:
    """Local-first trace sink with optional Omium-compatible hook.

    The app remains demo-safe without external credentials. If an Omium SDK is
    available, this class can be extended in one place without changing runtime
    code.
    """

    def __init__(self, store: Store):
        self.store = store
        self.enabled = bool(os.getenv("OMIUM_API_KEY"))

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
            "omium_enabled": self.enabled,
            "trace_provider": "local+omium-ready",
        }
        self.store.trace(mission_id, name, status, enriched, parent_step_id)
