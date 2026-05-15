"""PagerDuty — Webhook Connector.

Normalizes PagerDuty incident webhooks (V3 Events API) into AUTOPILOT signals.
Supports incident.triggered, incident.acknowledged, and incident.resolved events.
"""

from __future__ import annotations

from typing import Any

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Signal


class PagerDutyConnector(Connector):
    manifest = ConnectorManifest(
        name="pagerduty",
        description="Ingests PagerDuty incident webhooks and normalizes them into operational signals.",
        category="Observability",
        auth_mode="webhook",
        capabilities=[Capability.READ],
        scopes=["incidents.read"],
        objects=["incidents", "services", "escalation policies"],
        event_types=["incident.triggered", "incident.acknowledged", "incident.resolved"],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="pagerduty_ingest_incident",
                description="Normalize PagerDuty incident webhooks into mission signals.",
                capability=Capability.READ,
                input_schema={"payload": "PagerDuty webhook JSON"},
                output="Signal",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.94,
    )

    def readiness(self, action: str | None = None) -> dict:
        return {
            "configured": True,
            "action_ready": True,
            "missing": [],
            "mode": "webhook",
            "detail": "PagerDuty webhook receiver is active.",
            "action": action,
        }
    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        # PagerDuty V3 webhook format
        event = payload.get("event", payload)
        event_data = event.get("data", event) if isinstance(event, dict) else payload
        incident = event_data.get("incident", event_data) if isinstance(event_data, dict) else {}

        # Extract fields
        title = incident.get("title") or incident.get("summary") or payload.get("summary", "PagerDuty incident")
        service = incident.get("service", {})
        service_name = service.get("name") or service.get("summary") if isinstance(service, dict) else None
        incident_id = incident.get("id") or incident.get("incident_number") or payload.get("id")
        incident_key = incident.get("incident_key") or incident.get("dedup_key")
        urgency = incident.get("urgency", "high")
        status = incident.get("status", "triggered")
        event_type = event.get("event_type", payload.get("event_type", "incident.triggered")) if isinstance(event, dict) else "incident.triggered"

        # Build entities
        entities = [item for item in [
            service_name,
            incident.get("escalation_policy", {}).get("summary") if isinstance(incident.get("escalation_policy"), dict) else None,
            f"incident-{incident_id}" if incident_id else None,
        ] if item]

        # Map PagerDuty urgency to AUTOPILOT urgency
        urgency_map = {"high": "critical", "low": "medium"}
        mapped_urgency = urgency_map.get(urgency, "high") if status == "triggered" else "low"

        return Signal(
            source=self.manifest.name,
            type=str(event_type),
            summary=f"PagerDuty: {title}",
            entities=entities,
            urgency=mapped_urgency,
            payload=payload,
            idempotency_key=f"pagerduty:{incident_key or incident_id}" if (incident_key or incident_id) else None,
        )
