from __future__ import annotations

from abc import ABC
from typing import Any

from autopilot.models import (
    ActionResult,
    Capability,
    ConnectorManifest,
    Evidence,
    Signal,
)


class Connector(ABC):
    manifest: ConnectorManifest

    def has(self, capability: Capability) -> bool:
        return capability in self.manifest.capabilities

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        return Signal(
            source=self.manifest.name,
            type=str(payload.get("type", "operational_signal")),
            summary=str(
                payload.get(
                    "summary", payload.get("message", "External signal received")
                )
            ),
            entities=list(payload.get("entities", [])),
            urgency=str(payload.get("urgency", "medium")),
            payload=payload,
        )

    async def search(self, query: str) -> list[Evidence]:
        return []

    async def read(self, ref: str) -> dict[str, Any]:
        return {"ref": ref}

    async def write(
        self, name: str, content: str, metadata: dict[str, Any] | None = None
    ) -> ActionResult:
        raise NotImplementedError(f"{self.manifest.name} does not support write")

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        raise NotImplementedError(f"{self.manifest.name} does not support action")


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.manifest.name] = connector

    def get(self, name: str) -> Connector:
        return self._connectors[name]

    def has_name(self, name: str) -> bool:
        return name in self._connectors

    def manifests(self) -> list[ConnectorManifest]:
        return [connector.manifest for connector in self._connectors.values()]

    def by_capability(self, capability: Capability) -> list[Connector]:
        return [
            connector
            for connector in self._connectors.values()
            if connector.has(capability)
        ]
