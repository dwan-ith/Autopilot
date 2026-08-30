"""Executor — Mission Subagent #3.

Carries out bounded, policy-approved side effects through connectors:
  - Creating GitHub issues
  - Posting Slack notifications
  - Writing artifact reports
  - Posting webhook callbacks

The Executor first generates a prioritized execution plan based on the
mission brief and available connectors, then publish_actions() in core.py
executes that plan in stated priority order.

Uses the Groq-2 slot (fast) — actions should execute without delay.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from autopilot.agents.base import AgentResult, SubAgent
from autopilot.agents.validation import safe_int, severity_at_least
from autopilot.connectors.base import Connector
from autopilot.models import Mission

log = logging.getLogger("autopilot.agents.executor")

_SYSTEM = """\
You are the Executor sub-agent in AUTOPILOT. You decide which bounded actions
to take after a mission is verified.

Given the mission brief, available connectors, and policy approvals, determine:
1. Which actions should execute (from the approved action list)
2. In what order (priority 1 = most urgent)
3. What content each action should carry

You cannot invent new actions. You can only use actions in the connector's safe_actions list
that the Governor has already approved (status=allowed).

Return:
{"action": "answer", "result": {
  "execution_plan": [
    {"connector": "github", "action": "create_issue", "priority": 1, "notes": "why"},
    {"connector": "notification", "action": "notify_ops", "priority": 2, "notes": "why"},
    {"connector": "artifact", "action": "write_report", "priority": 3, "notes": "why"}
  ],
  "skip_reason": "why any actions were skipped (or null)"
}}

Order by: (1) time sensitivity, (2) audience impact, (3) risk level (lower risk first).
Always include write_report last — it's the safety net.
"""

_BRIEF_SYSTEM = """\
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


class ExecutorAgent:
    """Mission subagent that plans and carries out approved actions."""

    role = "executor"

    async def plan_actions(
        self,
        mission: Mission,
        brief: str,
        available_connectors: list[Connector],
    ) -> list[dict[str, Any]]:
        """Ask the LLM to produce a prioritized execution plan.

        Returns a list of dicts: [{"connector": str, "action": str, "priority": int, "notes": str}]
        sorted by priority ascending. Falls back to a sensible heuristic ordering if LLM fails.
        """
        connector_surface = "\n".join(
            f"  - {c.manifest.name}: safe_actions={c.manifest.safe_actions}"
            for c in available_connectors
        )
        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n"
            f"Evidence count: {len(mission.evidence)}\n"
            f"Replans: {mission.replans}\n\n"
            f"Available connectors and their safe actions:\n{connector_surface}\n\n"
            f"Brief excerpt:\n{brief[:800]}\n\n"
            f"Generate a prioritized execution plan. "
            f"For high-severity missions, prioritize notification and issue creation. "
            f"For low-severity, prioritize artifact writing. "
            f"Always include write_report."
        )
        agent = SubAgent(role=self.role, tools=[], max_steps=3, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)

        if isinstance(result.answer, dict):
            plan = result.answer.get("execution_plan", [])
            if isinstance(plan, list) and plan:
                valid = [
                    step for step in plan
                    if isinstance(step, dict)
                    and step.get("connector")
                    and step.get("action")
                ]
                if valid:
                    log.info("Executor: LLM generated %d-step execution plan", len(valid))
                    return sorted(valid, key=lambda s: safe_int(s.get("priority"), default=99, lo=1, hi=999))

        log.info("Executor: falling back to heuristic execution plan")
        return self._heuristic_plan(mission, available_connectors)

    def _heuristic_plan(
        self,
        mission: Mission,
        available_connectors: list[Connector],
    ) -> list[dict[str, Any]]:
        """Produce a sensible default ordering based on severity and risk."""
        plan: list[dict[str, Any]] = []
        connector_map = {c.manifest.name: c for c in available_connectors}

        # High/critical -> notify first, then issue, then artifact.
        # Keep known production connectors in a stable order, then include any
        # other registered issue connector so custom integrations are not
        # silently ignored when the LLM planner is disabled or unavailable.
        if severity_at_least(mission.severity, "high"):
            order = [
                ("notification", "notify_ops", 1),
                ("github", "create_issue", 2),
                ("linear", "create_issue", 3),
                ("artifact", "write_action_packet", 4),
                ("artifact", "write_report", 5),
            ]
            issue_priority = 3
            known_issue_connectors = {name for name, action, _ in order if action == "create_issue"}
            for connector_name in sorted(connector_map):
                connector = connector_map[connector_name]
                if (
                    connector_name not in known_issue_connectors
                    and "create_issue" in connector.manifest.safe_actions
                ):
                    issue_priority += 1
                    order.insert(
                        max(0, len(order) - 2),
                        (connector_name, "create_issue", issue_priority),
                    )
        else:
            # Medium/low -> artifact first (safest), then issue, then notify.
            order = [
                ("artifact", "write_report", 1),
                ("artifact", "write_action_packet", 2),
                ("github", "create_issue", 3),
                ("notification", "notify_ops", 4),
            ]
            issue_priority = 3
            known_issue_connectors = {name for name, action, _ in order if action == "create_issue"}
            for connector_name in sorted(connector_map):
                connector = connector_map[connector_name]
                if (
                    connector_name not in known_issue_connectors
                    and "create_issue" in connector.manifest.safe_actions
                ):
                    issue_priority += 1
                    order.insert(max(0, len(order) - 1), (connector_name, "create_issue", issue_priority))

        for connector_name, action, priority in order:
            if connector_name in connector_map:
                c = connector_map[connector_name]
                if action in c.manifest.safe_actions:
                    plan.append({
                        "connector": connector_name,
                        "action": action,
                        "priority": priority,
                        "notes": f"Heuristic plan: severity={mission.severity}",
                    })

        return plan

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

        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n"
            f"Replans: {mission.replans}\n\n"
            f"Hypotheses:\n{hyp_lines}\n\n"
            f"Evidence:\n{ev_lines}"
        )

        agent = SubAgent(role="synthesizer", tools=[], max_steps=2, system_prompt=_BRIEF_SYSTEM)
        result: AgentResult = await agent.run(task)
        brief = result.answer.get("brief") if isinstance(result.answer, dict) else None
        return brief or self._heuristic_brief(mission)

    def _heuristic_brief(self, mission: Mission) -> str:
        from datetime import datetime
        top = sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        lead = top[0] if top else None
        rec = "Continue monitoring and record the investigation."
        if lead and ("regression" in lead.title.lower() or "rollout" in lead.title.lower()) and mission.confidence >= 0.65:
            rec = "Inspect the recent rollout/deployment and prepare rollback or flag-disable steps."
        elif mission.severity in {"critical", "high"}:
            rec = "Escalate to the owning team with a customer-safe status update and evidence-backed next actions."
        return (
            f"# AUTOPILOT Mission Brief\n"
            f"Generated: {datetime.now(UTC).isoformat()}\n\n"
            f"## Incident Summary\n{mission.summary}\n\n"
            f"## Root Cause Analysis\n"
            f"{lead.title if lead else 'Undetermined'}: {lead.rationale if lead else ''}\n\n"
            f"## Evidence\n"
            + "\n".join(f"- [{e.source}] **{e.title}**: {e.summary[:150]}" for e in mission.evidence[:8])
            + f"\n\n## Recommendation\n{rec}\n\n"
            f"## Risk\nSeverity={mission.severity}, Confidence={mission.confidence:.2f}, Replans={mission.replans}\n"
        )
