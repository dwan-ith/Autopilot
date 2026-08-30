from __future__ import annotations

import os

from autopilot.connectors.base import Connector
from autopilot.models import ActionRisk, Mission, PolicyDecision


class PolicyEngine:
    """Policy-bounds actions before connectors produce side effects.

    The runtime treats connector actions as a constrained capability system:
    each action has a risk class, confidence threshold, and optional validation
    requirement. Connectors still declare their own safe action surface, so both
    runtime policy and connector capability must agree before a side effect runs.
    """

    ACTION_RULES: dict[str, tuple[ActionRisk, float, bool]] = {
        "write_report": (ActionRisk.LOW, 0.0, False),
        "write_action_packet": (ActionRisk.LOW, 0.0, False),
        "notify_ops": (ActionRisk.MEDIUM, 0.55, False),
        "webhook_callback": (ActionRisk.MEDIUM, 0.6, False),
        "create_issue": (ActionRisk.MEDIUM, 0.72, True),
        "trigger_deployment": (ActionRisk.HIGH, 0.85, True),
        "post_message": (ActionRisk.MEDIUM, 0.65, False),
        "mark_investigating": (ActionRisk.LOW, 0.45, False),
    }

    def __init__(self, high_risk_requires_human: bool = True):
        self.high_risk_requires_human = high_risk_requires_human
        self.auto_approve_validation = os.getenv("AUTOPILOT_AUTO_APPROVE_ACTIONS", "").lower() in {"1", "true", "yes"}

    def decide_operator(self, mission: Mission, connector: Connector, action: str) -> PolicyDecision:
        """Policy for authenticated operator-initiated requests.

        The API call itself is a human decision, so it satisfies the
        post-execution validation requirement — but every other gate
        (connector capability surface, confidence threshold, and the
        HIGH-risk human-approval rule) still applies. HIGH-risk actions
        therefore remain blocked here and must go through the approval
        queue; there is no flag that waives that.
        """
        original = self.auto_approve_validation
        self.auto_approve_validation = True
        try:
            return self.decide(mission, connector, action)
        finally:
            self.auto_approve_validation = original

    def decide(self, mission: Mission, connector: Connector, action: str) -> PolicyDecision:
        risk, required, requires_validation = self.ACTION_RULES.get(action, (ActionRisk.HIGH, 0.85, True))

        if action not in connector.manifest.safe_actions:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason=f"Action '{action}' is not declared safe by connector '{connector.manifest.name}'.",
                risk=risk,
                requires_validation=requires_validation,
                confidence_observed=mission.confidence,
            )

        if risk == ActionRisk.HIGH and self.high_risk_requires_human:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason="High-risk actions require explicit human approval.",
                risk=risk,
                requires_validation=True,
                confidence_required=required,
                confidence_observed=mission.confidence,
            )

        if mission.confidence < required:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason=f"Mission confidence {mission.confidence:.2f} is below required {required:.2f}.",
                risk=risk,
                requires_validation=requires_validation,
                confidence_required=required,
                confidence_observed=mission.confidence,
            )

        if requires_validation and not self.auto_approve_validation:
            return PolicyDecision(
                connector=connector.manifest.name,
                action=action,
                allowed=False,
                reason=(
                    "Action passed confidence policy but requires human approval. "
                    "Set AUTOPILOT_AUTO_APPROVE_ACTIONS=1 only in a trusted, controlled environment."
                ),
                risk=risk,
                requires_validation=True,
                confidence_required=required,
                confidence_observed=mission.confidence,
            )

        return PolicyDecision(
            connector=connector.manifest.name,
            action=action,
            allowed=True,
            reason=(
                "Action is connector-declared safe and confidence policy passed"
                + ("; validation is required after execution." if requires_validation else ".")
            ),
            risk=risk,
            requires_validation=requires_validation,
            confidence_required=required,
            confidence_observed=mission.confidence,
        )
