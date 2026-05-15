from __future__ import annotations

from typing import Any

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Signal


class WebhookConnector(Connector):
    manifest = ConnectorManifest(
        name="webhook",
        description="Generic inbound event connector for arbitrary external systems.",
        category="Inbound",
        auth_mode="webhook",
        capabilities=[Capability.READ],
        objects=["events", "alerts", "signals"],
        event_types=["support_escalation", "monitoring_alert", "status_update", "operational_signal"],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="webhook_ingest_event",
                description="Normalize an inbound webhook payload into an AUTOPILOT signal.",
                capability=Capability.READ,
                input_schema={"payload": "Webhook JSON payload"},
                output="Signal",
            ),
        ],
        reliability_score=0.98,
    )


class SentryConnector(Connector):
    manifest = ConnectorManifest(
        name="sentry",
        description="Normalizes Sentry issue and error webhooks into operational signals.",
        category="Observability",
        auth_mode="webhook",
        capabilities=[Capability.READ, Capability.SEARCH],
        scopes=["events.read", "issues.read"],
        objects=["errors", "issues", "stack traces", "projects"],
        event_types=["error.created", "issue.regression", "issue.created"],
        safe_actions=["mark_investigating"],
        tools=[
            ConnectorToolSpec(
                name="sentry_ingest_issue",
                description="Normalize Sentry issue and regression webhooks into mission signals.",
                capability=Capability.READ,
                input_schema={"payload": "Sentry webhook JSON"},
                output="Signal",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="sentry_search_issues",
                description="Search normalized Sentry issue context available to the runtime.",
                capability=Capability.SEARCH,
                input_schema={"query": "Error, culprit, project, or issue query"},
                output="Evidence[]",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.92,
    )

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        event = payload.get("event") or payload.get("data", {}).get("event") or {}
        issue = payload.get("issue") or payload.get("data", {}).get("issue") or {}
        project = payload.get("project") or payload.get("data", {}).get("project") or {}
        title = issue.get("title") or event.get("title") or event.get("message") or "Sentry error event"
        culprit = event.get("culprit") or issue.get("culprit")
        project_name = project.get("slug") or project.get("name")
        entities = [item for item in [project_name, culprit, issue.get("shortId")] if item]
        event_id = event.get("event_id") or issue.get("id") or payload.get("id")
        return Signal(
            source=self.manifest.name,
            type=str(payload.get("action", "error.created")),
            summary=f"Sentry reported {title}",
            entities=entities,
            urgency="high" if payload.get("action") in {"created", "regression"} else "medium",
            payload=payload,
            idempotency_key=f"sentry:{event_id}" if event_id else None,
        )
