"""Executor — Mission Subagent #3.

Carries out bounded, policy-approved side effects through connectors:
  - Creating GitHub issues
  - Posting Slack notifications
  - Writing artifact reports
  - Posting webhook callbacks

The Executor reasons about which actions to take and in what order,
given the mission brief and available approved connectors.

Uses the Groq-2 slot (fast) — actions should execute without delay.
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, SubAgent
from autopilot.connectors.base import Connector, ConnectorRegistry
from autopilot.models import ActionApproval, ActionResult, Capability, Mission

log = logging.getLogger("autopilot.agents.executor")

_SYSTEM = """\
You are the Executor sub-agent in AUTOPILOT. You decide which bounded actions
to take after a mission is verified.

Given the mission brief, available connectors, and policy approvals, determine:
1. Which actions should execute (from the approved action list)
2. In what order
3. What content each action should carry

You cannot invent new actions. You can only use actions in the connector's safe_actions list
that the Governor has already approved (status=allowed).

Return:
{"action": "answer", "result": {
  "execution_plan": [
    {"connector": "github", "action": "create_issue", "priority": 1, "notes": "why"},
    {"connector": "artifact", "action": "write_report", "priority": 2, "notes": "why"}
  ],
  "skip_reason": "why any actions were skipped (or null)"
}}
"""


class ExecutorAgent:
    """Mission subagent that carries out approved actions."""

    role = "executor"

    async def synthesize_brief(self, mission: Mission) -> str:
        """Generate the mission brief using an LLM."""
        hyp_lines = "\n".join(
            f"  [{h.confidence:.2f}] {h.title}: {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        )
        ev_lines = "\n".join(
            f"  [{e.source}] {e.title}: {e.summary[:200]}"
            for e in mission.evidence[:10]
        ) or "  - No evidence gathered."

        system = """\
You are the Synthesis Operator in AUTOPILOT. Write a concise operational brief in Markdown.

Structure:
# AUTOPILOT Mission Brief
## Incident Summary
## Root Cause Analysis
## Evidence
## Recommendation
## Risk

Return: {"action": "answer", "result": {"brief": "# AUTOPILOT Mission Brief\\n..."}}
"""
        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n"
            f"Replans: {mission.replans}\n\n"
            f"Hypotheses:\n{hyp_lines}\n\n"
            f"Evidence:\n{ev_lines}"
        )

        agent = SubAgent(role="synthesizer", tools=[], max_steps=2, system_prompt=system)
        result: AgentResult = await agent.run(task)
        brief = result.answer.get("brief") if isinstance(result.answer, dict) else None
        return brief or self._heuristic_brief(mission)

    def _heuristic_brief(self, mission: Mission) -> str:
        from datetime import datetime, timezone
        top = sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        lead = top[0] if top else None
        rec = "Continue monitoring and record the investigation."
        if lead and ("regression" in lead.title.lower() or "rollout" in lead.title.lower()) and mission.confidence >= 0.65:
            rec = "Inspect the recent rollout/deployment and prepare rollback or flag-disable steps."
        elif mission.severity in {"critical", "high"}:
            rec = "Escalate to the owning team with a customer-safe status update and evidence-backed next actions."
        return (
            f"# AUTOPILOT Mission Brief\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
            f"## Incident Summary\n{mission.summary}\n\n"
            f"## Root Cause Analysis\n"
            f"{lead.title if lead else 'Undetermined'}: {lead.rationale if lead else ''}\n\n"
            f"## Evidence\n"
            + "\n".join(f"- [{e.source}] **{e.title}**: {e.summary[:150]}" for e in mission.evidence[:8])
            + f"\n\n## Recommendation\n{rec}\n\n"
            f"## Risk\nSeverity={mission.severity}, Confidence={mission.confidence:.2f}, Replans={mission.replans}\n"
        )
