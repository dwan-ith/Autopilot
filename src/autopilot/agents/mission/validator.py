"""Validator — Mission Subagent #4.

After actions execute, the Validator checks:
1. Did the actions succeed at the connector level?
2. Is there evidence the underlying problem was actually addressed?
3. Should the mission be marked resolved or does it need follow-up?

Uses the Groq-2 slot (fast, shared with Executor).
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, SubAgent
from autopilot.models import ActionResult, Mission

log = logging.getLogger("autopilot.agents.validator")

_SYSTEM = """\
You are the Validator sub-agent in AUTOPILOT. You verify that executed actions
actually addressed the underlying incident.

Given the executed actions and their results, determine:
1. Did all actions succeed?
2. Is the root cause actually addressed or only partially?
3. Are there follow-up actions needed?
4. What is the final resolution status?

Return:
{"action": "answer", "result": {
  "all_actions_succeeded": true,
  "resolution_status": "resolved|partially_resolved|unresolved|monitoring_required",
  "follow_up_needed": false,
  "follow_up_actions": [],
  "validation_summary": "brief summary of what was validated"
}}
"""


class ValidatorAgent:
    """Mission subagent that validates post-action resolution."""

    role = "validator"

    async def validate(self, mission: Mission, actions: list[ActionResult]) -> dict[str, Any]:
        """Validate whether executed actions resolved the incident."""
        if not actions:
            return {
                "all_actions_succeeded": False,
                "resolution_status": "unresolved",
                "follow_up_needed": True,
                "follow_up_actions": ["Investigate why no actions were executed"],
                "validation_summary": "No actions were taken — mission remains unresolved.",
            }

        action_lines = "\n".join(
            f"  - {a.connector}.{a.action}: status={a.status}, summary={a.summary[:150]}"
            for a in actions
        )
        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n\n"
            f"Executed actions:\n{action_lines}\n\n"
            f"Evidence gathered: {len(mission.evidence)} items\n"
            f"Leading hypothesis: {max(mission.hypotheses, key=lambda h: h.confidence).title if mission.hypotheses else 'None'}\n\n"
            f"Did these actions adequately address the incident? What is the resolution status?"
        )

        agent = SubAgent(role=self.role, tools=[], max_steps=2, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        r = result.answer

        if isinstance(r, dict) and "resolution_status" in r:
            log.info("Validator: %s — follow_up=%s", r.get("resolution_status"), r.get("follow_up_needed"))
            return r

        # Heuristic fallback
        succeeded = sum(1 for a in actions if a.status in {"complete", "skipped"})
        all_ok = succeeded == len(actions)
        return {
            "all_actions_succeeded": all_ok,
            "resolution_status": "resolved" if all_ok and mission.confidence >= 0.7 else "monitoring_required",
            "follow_up_needed": not all_ok,
            "follow_up_actions": [f"Retry {a.connector}.{a.action}" for a in actions if a.status == "failed"],
            "validation_summary": f"{succeeded}/{len(actions)} actions succeeded.",
        }
