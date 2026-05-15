from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, ActionRisk, Capability, ConnectorManifest, ConnectorToolSpec
from autopilot.storage import ARTIFACT_DIR


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
        description="Sends bounded outbound notifications through Slack webhook when configured, otherwise logs locally.",
        category="Communication",
        auth_mode="webhook",
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
                input_schema={"mission_id": "Mission id", "text": "Notification text"},
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
        reliability_score=0.9,
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
            }
        mode = "slack" if os.getenv("SLACK_WEBHOOK_URL") else "local_fallback"
        return {
            "configured": True,
            "action_ready": True,
            "missing": [],
            "mode": mode,
            "detail": "Slack webhook is configured." if mode == "slack" else "Will write local notification artifacts.",
            "action": action,
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
        category="Engineering",
        auth_mode="api_key",
        capabilities=[Capability.WRITE, Capability.ACTION],
        scopes=["issues.write"],
        objects=["issues", "teams"],
        event_types=[],
        safe_actions=["create_issue"],
        tools=[
            ConnectorToolSpec(
                name="linear_create_issue",
                description="Create a Linear issue with a verified incident brief.",
                capability=Capability.ACTION,
                input_schema={"title": "Issue title", "description": "Issue body", "team_id": "Configured team id"},
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
                mcp_tool=True,
            ),
        ],
        reliability_score=0.82,
        auth_required=True,
    )

    def readiness(self, action: str | None = None) -> dict:
        missing = []
        if not os.getenv("LINEAR_API_KEY"):
            missing.append("LINEAR_API_KEY")
        if not os.getenv("LINEAR_TEAM_ID"):
            missing.append("LINEAR_TEAM_ID")
        return {
            "configured": not missing,
            "action_ready": not missing,
            "missing": missing,
            "mode": "api_key" if not missing else "missing_credentials",
            "detail": "Linear issue creation is configured." if not missing else "Linear credentials are incomplete.",
            "action": action,
        }

    async def action(self, name: str, payload: dict) -> ActionResult:
        if os.getenv("PROJECT_MGMT_PROVIDER", "linear").lower() == "jira":
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="blocked",
                summary="Jira is intentionally stubbed for this build. Set PROJECT_MGMT_PROVIDER=linear to create Linear issues.",
                metadata={"provider": "jira", "stub": True},
            )

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


class CloudInfraConnector(Connector):
    manifest = ConnectorManifest(
        name="cloud_infra",
        description="Triggers one demoable deployment action through a webhook or GitHub Actions dispatch.",
        capabilities=[Capability.ACTION],
        event_types=[],
        safe_actions=["trigger_deployment"],
        reliability_score=0.78,
        auth_required=False,
    )

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
