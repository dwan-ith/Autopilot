from __future__ import annotations

from autopilot.connectors.base import Connector
from autopilot.models import Mission, PolicyDecision


class PolicyEngine:
    """Policy-bounds actions before connectors produce side effects."""

    def __init__(self, min_external_notify_confidence: float = 0.55):
        self.min_external_notify_confidence = min_external_notify_confidence

    def decide(self, mission: Mission, connector: Connector, action: str) -> PolicyDecision:
        if action not in connector.manifest.safe_actions:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason=f"Action '{action}' is not declared safe by connector '{connector.manifest.name}'.",
                confidence_observed=mission.confidence,
            )

        required = 0.0
        if connector.manifest.name == "notification":
            required = self.min_external_notify_confidence

        if mission.confidence < required:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason=f"Mission confidence {mission.confidence:.2f} is below required {required:.2f}.",
                confidence_required=required,
                confidence_observed=mission.confidence,
            )

        return PolicyDecision(
            connector=connector.manifest.name,
            action=action,
            allowed=True,
            reason="Action is connector-declared safe and confidence policy passed.",
            confidence_required=required,
            confidence_observed=mission.confidence,
        )
