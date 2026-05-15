from __future__ import annotations

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest


class WebhookConnector(Connector):
    manifest = ConnectorManifest(
        name="webhook",
        description="Generic inbound event connector for arbitrary external systems.",
        capabilities=[Capability.READ],
        event_types=["support_escalation", "monitoring_alert", "status_update", "operational_signal"],
        safe_actions=[],
        reliability_score=0.98,
    )
