from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import (
    ActionResult,
    ActionRisk,
    Capability,
    ConnectorManifest,
    ConnectorToolSpec,
)
from autopilot.storage import ARTIFACT_DIR

log = logging.getLogger(__name__)


class ArtifactConnector(Connector):
    manifest = ConnectorManifest(
        name="artifact",
        description="Writes durable local reports, action packets, and audit artifacts.",
        category="System",
        capabilities=[Capability.WRITE, Capability.ACTION],
        objects=["reports", "action packets", "audit artifacts"],
        event_types=[],
        safe_actions=["write_report", "write_action_packet"],
        tools=[
            ConnectorToolSpec(
                name="artifact_write_report",
                description="Write a durable Markdown mission report to the local artifact store.",
                capability=Capability.WRITE,
                input_schema={"name": "Artifact name", "content": "Markdown report", "metadata": "Optional metadata"},
                output="ActionResult",
            ),
            ConnectorToolSpec(
                name="artifact_write_action_packet",
                description="Write a structured JSON action packet for downstream review or automation.",
                capability=Capability.ACTION,
                input_schema={"name": "Packet name", "payload": "Structured action payload"},
                output="ActionResult",
                risk=ActionRisk.LOW,
            ),
        ],
        reliability_score=0.99,
    )

    def readiness(self, action: str | None = None) -> dict:
        return {
            "configured": True,
            "action_ready": True,
            "missing": [],
            "mode": "local",
            "detail": "Local artifact store is available.",
            "action": action,
            "integration_live": True,
        }

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
        if name != "write_action_packet":
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="failed",
                summary=f"Unknown artifact action: {name}",
                metadata={"safe_actions": self.manifest.safe_actions},
            )
        packet_name = payload.get("packet_name") or f"action-packet-{payload.get('mission_id', 'unknown')}"
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(packet_name)).strip("-")
        path = ARTIFACT_DIR / f"{safe_name}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action="write_action_packet",
            status="complete",
            summary=f"Recorded action payload {path.name}",
            artifact_path=str(path),
            metadata={"payload_keys": sorted(payload.keys())},
        )


class NotificationConnector(Connector):
    manifest = ConnectorManifest(
        name="notification",
        description="Sends bounded outbound notifications through Slack (Web API or Webhook) when configured, otherwise logs locally.",
        category="Communication",
        auth_mode="api_key",
        capabilities=[Capability.NOTIFY, Capability.ACTION],
        scopes=["chat.write"],
        objects=["messages", "incident updates"],
        event_types=[],
        safe_actions=["notify_ops", "webhook_callback"],
        tools=[
            ConnectorToolSpec(
                name="slack_notify_ops",
                description="Send an approved operational notification to Slack or local fallback.",
                capability=Capability.NOTIFY,
                input_schema={"mission_id": "Mission id", "text": "Notification text", "channel": "Optional channel ID or name"},
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="webhook_callback",
                description="Post an approved callback payload to an external webhook.",
                capability=Capability.ACTION,
                input_schema={"callback_url": "Optional callback URL", "payload": "Callback body"},
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
            ),
        ],
        reliability_score=0.92,
        auth_required=False,
    )

    def readiness(self, action: str | None = None) -> dict:
        if action == "webhook_callback" and not os.getenv("AUTOPILOT_CALLBACK_URL"):
            return {
                "configured": False,
                "action_ready": False,
                "missing": ["AUTOPILOT_CALLBACK_URL"],
                "mode": "missing_callback",
                "detail": "Outbound callback URL is not configured.",
                "action": action,
                "integration_live": False,
            }
        
        has_token = bool(os.getenv("SLACK_ACCESS_TOKEN"))
        has_webhook = bool(os.getenv("SLACK_WEBHOOK_URL"))
        channel_env = os.getenv("SLACK_DEFAULT_CHANNEL", "").strip()
        channel_warning = None

        if has_token and not channel_env:
            channel_warning = (
                "SLACK_DEFAULT_CHANNEL is not set. Defaulting to '#ops'. "
                "Set SLACK_DEFAULT_CHANNEL to your workspace channel name."
            )

        mode = "slack_api" if has_token else ("slack_webhook" if has_webhook else "local_fallback")
        return {
            "configured": True,
            "action_ready": True,
            "missing": [],
            "mode": mode,
            "detail": f"Slack {mode.split('_')[1]} active." if mode != "local_fallback" else "Will write local notification artifacts.",
            "action": action,
            "integration_live": mode != "local_fallback",
            "channel_warning": channel_warning,
        }

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
            except (OSError, KeyError, ValueError) as exc:
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="failed",
                    summary=f"Webhook callback failed: {exc}",
                    metadata={"mode": "webhook_callback"},
                )

        # Slack Logic
        token = os.getenv("SLACK_ACCESS_TOKEN")
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        text = payload.get("text", json.dumps(payload))
        
        if token:
            channel = payload.get("channel") or os.getenv("SLACK_DEFAULT_CHANNEL", "#ops")
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"channel": channel, "text": text}
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        slack_err = data.get("error", "unknown_error")
                        if slack_err == "channel_not_found":
                            log.error(
                                "Slack channel_not_found: channel does not exist. "
                                "Set SLACK_DEFAULT_CHANNEL to a valid channel name."
                            )
                        else:
                            log.error("Slack API error: %s", slack_err)
                        raise Exception(f"Slack API error: {slack_err}")
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary=f"Posted notification to Slack channel {channel} via API.",
                    metadata={"mode": "slack_api", "channel": channel, "ts": data.get("ts")},
                )
            except Exception as exc:
                log.error("Slack API notification failed: %s", exc)
                # Fall through to webhook if available

        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(webhook_url, json={"text": text})
                    response.raise_for_status()
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary="Posted notification to Slack webhook.",
                    metadata={"mode": "slack_webhook"},
                )
            except Exception as exc:
                log.error("Slack webhook notification failed: %s", exc)

        path = ARTIFACT_DIR / f"notification-{payload.get('mission_id', 'unknown')}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary="Slack not configured or failed; wrote local notification artifact.",
            artifact_path=str(Path(path)),
            metadata={"mode": "local_fallback"},
        )


class CloudInfraConnector(Connector):
    manifest = ConnectorManifest(
        name="cloud_infra",
        description="Triggers a policy-approved deployment through a webhook or GitHub Actions dispatch.",
        capabilities=[Capability.ACTION],
        event_types=[],
        safe_actions=["trigger_deployment"],
        reliability_score=0.78,
        auth_required=False,
    )

    def readiness(self, action: str | None = None) -> dict:
        webhook = bool(os.getenv("DEPLOYMENT_WEBHOOK_URL"))
        gh = bool(os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPOSITORY") and os.getenv("GITHUB_WORKFLOW_ID"))
        configured = webhook or gh
        missing = []
        if not webhook and not gh:
            missing = ["DEPLOYMENT_WEBHOOK_URL or GITHUB_TOKEN+GITHUB_REPOSITORY+GITHUB_WORKFLOW_ID (optional)"]
        return {
            "configured": True,   # local artifact fallback always works
            "action_ready": True,
            "missing": missing,
            "mode": "webhook" if webhook else "github_actions" if gh else "local_fallback",
            "detail": "Deployment trigger ready." if configured else "No endpoint configured — writes a local artifact trigger record.",
            "action": action,
            "integration_live": configured,
        }

    async def action(self, name: str, payload: dict) -> ActionResult:
        if name != "trigger_deployment":
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="blocked",
                summary=f"Unsupported cloud infra action '{name}'. Only trigger_deployment is in scope.",
            )

        deployment_payload = {
            "environment": payload.get("environment", os.getenv("DEPLOYMENT_ENVIRONMENT", "staging")),
            "ref": payload.get("ref", os.getenv("DEPLOYMENT_REF", "main")),
            "mission_id": payload.get("mission_id"),
            "reason": payload.get("reason", "AUTOPILOT deployment trigger"),
        }
        webhook_url = os.getenv("DEPLOYMENT_WEBHOOK_URL")
        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    response = await client.post(webhook_url, json=deployment_payload)
                    response.raise_for_status()
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary="Triggered deployment webhook.",
                    metadata={"mode": "webhook", **deployment_payload},
                )
            except Exception as exc:
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="failed",
                    summary=f"Deployment webhook failed: {exc}",
                    metadata={"mode": "webhook"},
                )

        github_token = os.getenv("GITHUB_TOKEN")
        repository = os.getenv("GITHUB_REPOSITORY")
        workflow_id = os.getenv("GITHUB_WORKFLOW_ID")
        if github_token and repository and workflow_id:
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    response = await client.post(
                        f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_id}/dispatches",
                        headers={
                            "Authorization": f"Bearer {github_token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        json={"ref": deployment_payload["ref"], "inputs": deployment_payload},
                    )
                    response.raise_for_status()
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="complete",
                    summary="Triggered GitHub Actions workflow dispatch.",
                    metadata={"mode": "github_actions", **deployment_payload},
                )
            except Exception as exc:
                return ActionResult(
                    connector=self.manifest.name,
                    action=name,
                    status="failed",
                    summary=f"GitHub Actions dispatch failed: {exc}",
                    metadata={"mode": "github_actions"},
                )

        path = ARTIFACT_DIR / f"deployment-trigger-{payload.get('mission_id', 'manual')}.json"
        path.write_text(json.dumps(deployment_payload, indent=2), encoding="utf-8")
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary="No deployment endpoint configured; wrote local deployment trigger artifact.",
            artifact_path=str(path),
            metadata={"mode": "local_fallback", **deployment_payload},
        )
