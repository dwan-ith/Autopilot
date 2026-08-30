"""Governor — Persistent Agent #3.

Enforces action boundaries. Every connector action must pass through the
Governor before execution. Unlike the raw PolicyEngine (which is pure rule-
based), the Governor applies LLM reasoning to ambiguous cases — but the LLM
can only *escalate* a decision (attach an endorsement and/or route it to the
human approval queue). It can never convert a policy block into autonomous
execution; `PolicyDecision.allowed=True` originates exclusively from the
deterministic engine.

Uses the Groq-3 slot (fast) since policy checks are on the hot path.
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, PersistentAgent, SubAgent
from autopilot.connectors.base import Connector
from autopilot.models import ActionRisk, Mission, PolicyDecision
from autopilot.policy import PolicyEngine

log = logging.getLogger("autopilot.agents.governor")

_SYSTEM = """\
You are the Governor in AUTOPILOT. You enforce action boundaries.

Given a proposed action, evaluate:
1. Is the action within acceptable risk bounds given current confidence?
2. Are there any anomalies (unusual scope, unexpected targets, policy gaps)?
3. Should this be allowed, held for human approval, or blocked outright?

Return:
{"action": "answer", "result": {
  "decision": "allow|hold|block",
  "reason": "brief explanation",
  "risk_assessment": "what could go wrong if we proceed",
  "escalate": false
}}

'hold' means queue for human approval. 'block' means deny entirely.
Be conservative for write/action operations. Be liberal for read-only evidence gathering.
"""


class GovernorAgent(PersistentAgent):
    """Persistent agent that enforces action policy with LLM reasoning for edge cases."""

    role = "governor"
    default_max_steps = 2

    def __init__(self):
        self._policy = PolicyEngine()

    def _system_prompt(self) -> str:
        return _SYSTEM

    def decide(self, mission: Mission, connector: Connector, action: str) -> PolicyDecision:
        """Deterministic policy only. The sync path never consults an LLM."""
        return self._policy.decide(mission, connector, action)

    async def decide_async(self, mission: Mission, connector: Connector, action: str) -> PolicyDecision:
        """Async variant — LLM review for borderline cases, escalation-only.

        Invariant: if the deterministic engine did not allow autonomous
        execution, the returned decision also has allowed=False. An LLM
        "allow" verdict on a blocked-but-borderline action becomes a
        human-approval queue entry carrying the endorsement, so only an
        explicit operator decision can release the side effect.
        """
        decision = self._policy.decide(mission, connector, action)
        if decision.allowed:
            return decision

        _, required, requires_validation = PolicyEngine.ACTION_RULES.get(
            action, (ActionRisk.HIGH, 0.85, True)
        )
        borderline = (
            abs(mission.confidence - required) <= 0.12
            and mission.confidence >= required * 0.85
        )

        if borderline and action in connector.manifest.safe_actions:
            r = await self._llm_review(mission, connector, action)
            llm_decision = str(r.get("decision", "block")).lower() if isinstance(r, dict) else "block"
            if llm_decision in {"allow", "hold"}:
                endorsed = "endorsed" if llm_decision == "allow" else "not endorsed"
                log.info(
                    "Governor: LLM review %s for %s.%s (confidence=%.2f) — routing to human approval queue",
                    endorsed, connector.manifest.name, action, mission.confidence,
                )
                return PolicyDecision(
                    connector=connector.manifest.name,
                    action=action,
                    allowed=False,
                    reason=(
                        f"Borderline confidence ({mission.confidence:.2f} vs required {required:.2f}); "
                        f"Governor LLM review {endorsed} the action. Queued for human approval. "
                        f"{r.get('reason', '')}"
                    ).strip(),
                    risk=decision.risk,
                    requires_validation=True,
                    confidence_required=required,
                    confidence_observed=mission.confidence,
                )
            # "block" or unparsable review — deterministic decision stands.

        return decision

    async def _llm_review(self, mission: Mission, connector: Connector, action: str) -> dict[str, Any]:
        task = (
            f"Proposed action: {connector.manifest.name}.{action}\n"
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n"
            f"Evidence count: {len(mission.evidence)}\n"
            f"Safe actions declared: {connector.manifest.safe_actions}\n\n"
            f"Is this action appropriate given the evidence level? "
            f"The deterministic policy said NO because confidence is borderline."
        )
        agent = SubAgent(role=self.role, tools=[], max_steps=2, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        return result.answer if isinstance(result.answer, dict) else {}
