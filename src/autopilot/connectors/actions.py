from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, Capability, ConnectorManifest
from autopilot.storage import ARTIFACT_DIR


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
        callback_url = payload.get("callback_url") or os.getenv("AUTOPILOT_CALLBACK_URL")
        if name == "webhook_callback" and not callback_url:
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="skipped",
                summary="No AUTOPILOT_CALLBACK_URL configured; outbound callback was not sent.",
                metadata={"mode": "webhook_callback", "required_env": "AUTOPILOT_CALLBACK_URL"},
            )

        if name == "webhook_callback" and callback_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(callback_url, json=payload)
                    response.raise_for_status()
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary="Posted outbound webhook callback.",
                    metadata={"mode": "webhook_callback", "status_code": response.status_code},
                )
            except Exception as exc:
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="failed",
                    summary=f"Webhook callback failed: {exc}",
                    metadata={"mode": "webhook_callback"},
                )

        slack_url = os.getenv("SLACK_WEBHOOK_URL")
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


class LinearConnector(Connector):
    manifest = ConnectorManifest(
        name="linear",
        description="Creates real Linear issues when LINEAR_API_KEY and LINEAR_TEAM_ID are configured.",
        capabilities=[Capability.WRITE, Capability.ACTION],
        event_types=[],
        safe_actions=["create_issue"],
        reliability_score=0.82,
        auth_required=True,
    )

    async def action(self, name: str, payload: dict) -> ActionResult:
        if name != "create_issue":
            raise NotImplementedError(f"Linear action '{name}' is not supported")

        api_key = os.getenv("LINEAR_API_KEY")
        team_id = os.getenv("LINEAR_TEAM_ID")
        if not api_key or not team_id:
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="skipped",
                summary="Linear credentials are not configured; no external issue was created.",
                metadata={"required_env": ["LINEAR_API_KEY", "LINEAR_TEAM_ID"]},
            )

        title = payload.get("title", "AUTOPILOT mission follow-up")
        description = payload.get("description", "")
        query = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url title }
          }
        }
        """
        variables = {"input": {"teamId": team_id, "title": title, "description": description}}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    "https://api.linear.app/graphql",
                    headers={"Authorization": api_key, "Content-Type": "application/json"},
                    json={"query": query, "variables": variables},
                )
                response.raise_for_status()
                data = response.json()
            issue = data.get("data", {}).get("issueCreate", {}).get("issue") or {}
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="complete",
                summary=f"Created Linear issue {issue.get('identifier', issue.get('id', 'unknown'))}.",
                metadata={"url": issue.get("url"), "identifier": issue.get("identifier")},
            )
        except Exception as exc:
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="failed",
                summary=f"Linear issue creation failed: {exc}",
                metadata={"mode": "linear_graphql"},
            )
