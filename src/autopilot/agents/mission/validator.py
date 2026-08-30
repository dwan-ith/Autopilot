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

_VALID_RESOLUTIONS = {"resolved", "partially_resolved", "unresolved", "monitoring_required"}


def _sanitize_resolution(raw: Any) -> dict[str, Any] | None:
    """Accept an LLM validation dict only when its resolution status is one of
    the documented values; anything else falls back to the heuristic path."""
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("resolution_status", "")).strip().lower().replace("-", "_").replace(" ", "_")
    if status not in _VALID_RESOLUTIONS:
        return None
    follow_ups = raw.get("follow_up_actions")
    return {
        "all_actions_succeeded": bool(raw.get("all_actions_succeeded")),
        "resolution_status": status,
        "follow_up_needed": bool(raw.get("follow_up_needed", status in {"partially_resolved", "unresolved"})),
        "follow_up_actions": [
            str(item)[:200] for item in (follow_ups if isinstance(follow_ups, list) else [])
        ][:10],
        "validation_summary": str(raw.get("validation_summary", ""))[:500],
    }

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

        sanitized = _sanitize_resolution(result.answer)
        if sanitized:
            log.info(
                "Validator: %s — follow_up=%s",
                sanitized.get("resolution_status"), sanitized.get("follow_up_needed"),
            )
            return sanitized

        # Heuristic fallback. Actions skipped for missing credentials are NOT
        # successes, and policy-blocked actions must never be "retried" by an
        # autonomous actor — they need a human decision.
        succeeded = sum(1 for a in actions if a.status == "complete")
        all_ok = succeeded == len(actions)
        follow_ups = [
            f"Manually review {a.connector}.{a.action}: {a.summary[:120]}"
            for a in actions if a.status in {"failed", "blocked", "skipped"}
        ]
        return {
            "all_actions_succeeded": all_ok,
            "resolution_status": (
                "resolved" if all_ok and mission.confidence >= 0.7
                else "partially_resolved" if succeeded and mission.confidence >= 0.5
                else "unresolved"
            ),
            "follow_up_needed": bool(follow_ups) or not all_ok,
            "follow_up_actions": follow_ups[:10],
            "validation_summary": f"{succeeded}/{len(actions)} actions fully completed.",
        }
