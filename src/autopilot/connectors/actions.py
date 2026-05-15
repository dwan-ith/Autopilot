from __future__ import annotations

import json
from pathlib import Path

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, Capability, ConnectorManifest
from autopilot.storage import ARTIFACT_DIR
from autopilot.config import settings


class ArtifactConnector(Connector):
    manifest = ConnectorManifest(
        name="artifact",
        description="Writes durable local reports, action packets, and audit artifacts.",
        capabilities=[Capability.WRITE, Capability.ACTION],
        event_types=[],
        safe_actions=["write_report", "write_action_packet"],
        reliability_score=0.99,
    )

    async def write(self, name: str, content: str, metadata: dict | None = None) -> ActionResult:
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in name).strip("-")
        path = ARTIFACT_DIR / f"{safe_name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action="write_report",
            status="complete",
            summary=f"Wrote artifact {path.name}",
            artifact_path=str(path),
            metadata=metadata or {},
        )

    async def action(self, name: str, payload: dict) -> ActionResult:
        path = ARTIFACT_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary=f"Recorded action payload {path.name}",
            artifact_path=str(path),
            metadata={"payload_keys": sorted(payload.keys())},
        )


class NotificationConnector(Connector):
    manifest = ConnectorManifest(
        name="notification",
        description="Sends bounded outbound notifications through Slack webhook when configured, otherwise logs locally.",
        capabilities=[Capability.NOTIFY, Capability.ACTION],
        event_types=[],
        safe_actions=["notify_ops", "webhook_callback"],
        reliability_score=0.9,
        auth_required=False,
    )

    async def action(self, name: str, payload: dict) -> ActionResult:
        slack_url = settings.SLACK_WEBHOOK_URL
        if slack_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(slack_url, json={"text": payload.get("text", json.dumps(payload))})
                    response.raise_for_status()
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary="Posted notification to Slack webhook.",
                    metadata={"mode": "slack"},
                )
            except Exception as exc:
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="failed",
                    summary=f"Slack notification failed: {exc}",
                    metadata={"mode": "slack"},
                )

        path = ARTIFACT_DIR / f"notification-{payload.get('mission_id', 'unknown')}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary="No Slack webhook configured; wrote local notification artifact.",
            artifact_path=str(Path(path)),
            metadata={"mode": "local_fallback"},
        )
