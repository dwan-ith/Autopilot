"""AUTOPILOT operator suite — 9-agent architecture.

Persistent agents (instantiated once in RuntimeKernel):
  Orchestrator  — RuntimeKernel itself (coordinates the flow)
  Correlator    — classifies and correlates signals
  Verifier      — scores evidence quality and confidence
  Governor      — enforces action policy
  Memory        — cross-mission learning

Mission subagents (spawned per mission):
  Planner       — generates investigation hypotheses
  Investigator  — parallel evidence-gathering (real tool loops)
  Executor      — synthesizes briefs and executes approved actions
  Validator     — verifies post-action resolution
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from autopilot.agents.base import Tool
from autopilot.agents.mission.executor import ExecutorAgent
from autopilot.agents.mission.investigator import InvestigatorAgent
from autopilot.agents.mission.planner import PlannerAgent
from autopilot.agents.mission.validator import ValidatorAgent
from autopilot.agents.persistent.correlator import CorrelatorAgent
from autopilot.agents.persistent.governor import GovernorAgent
from autopilot.agents.persistent.memory import MemoryAgent
from autopilot.agents.persistent.verifier import VerifierAgent
from autopilot.connectors.base import Connector, ConnectorRegistry
from autopilot.models import (
    ActionApproval,
    ActionResult,
    Capability,
    Evidence,
    Hypothesis,
    Mission,
    PolicyDecision,
    utc_now,
)
from autopilot.policy import PolicyEngine

log = logging.getLogger("autopilot.operators")


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def _tools_from_registry(registry: ConnectorRegistry) -> list[Tool]:
    """Convert connector capabilities into SubAgent tools."""
    tools: list[Tool] = []
    seen = set()

    for connector in registry.by_capability(Capability.SEARCH):
        name = connector.manifest.name
        generic_name = f"search_{name}"
        if generic_name not in seen:
            seen.add(generic_name)
            tools.append(Tool(
                name=generic_name,
                description=f"Search {name} for relevant information. Returns list of evidence items.",
                parameters={"query": "Search query string"},
                fn=connector.search,
            ))
        # Rich tool interface from as_tools() (e.g. github_search_issues, github_read_issue)
        if hasattr(connector, "as_tools"):
            for t in connector.as_tools():
                if t.name not in seen:
                    seen.add(t.name)
                    tools.append(t)

    return tools


def _knowledge_tools(registry: ConnectorRegistry) -> list[Tool]:
    """Return only the knowledge/search tools (for Planner)."""
    tools = []
    seen = set()
    for connector in registry.by_capability(Capability.SEARCH):
        if connector.manifest.name == "knowledge":
            if hasattr(connector, "as_tools"):
                for t in connector.as_tools():
                    if t.name not in seen:
                        seen.add(t.name)
                        tools.append(t)
            else:
                tools.append(Tool(
                    name=f"search_{connector.manifest.name}",
                    description="Search runbooks and knowledge base.",
                    parameters={"query": "Query string"},
                    fn=connector.search,
                ))
            break
    return tools


# ---------------------------------------------------------------------------
# OperatorSuite
# ---------------------------------------------------------------------------

class OperatorSuite:
    """Wires persistent agents and spawns mission subagents."""

    def __init__(
        self,
        registry: ConnectorRegistry,
        memory_agent: MemoryAgent | None = None,
    ):
        self.registry = registry

        # Persistent agents
        self.correlator = CorrelatorAgent()
        self.verifier = VerifierAgent()
        self.governor = GovernorAgent()
        self.memory = memory_agent  # provided by RuntimeKernel (has Store access)

    # ── Signal evaluation (Correlator) ──────────────────────────────────────

    async def evaluate_signal(self, mission: Mission, active_missions: list[Mission] | None = None) -> Mission:
        result = await self.correlator.correlate(mission.signals[-1], active_missions or [])
        if isinstance(result, dict) and "severity" in result:
            mission.severity = str(result.get("severity", mission.severity))
            impact = str(result.get("impact_summary", "")).strip()
            focus = str(result.get("investigation_focus", "")).strip()
            mission.summary = " | ".join(p for p in [impact, f"Focus: {focus}" if focus else ""] if p)
            for signal in mission.signals:
                for entity in result.get("key_entities", []) or []:
                    if entity not in signal.entities:
                        signal.entities.append(str(entity))
        log.info("Correlator: severity=%s summary=%.80s", mission.severity, mission.summary)
        return mission

    # ── Hypothesis planning (Planner subagent) ───────────────────────────────

    async def plan_mission(self, mission: Mission) -> Mission:
        planner = PlannerAgent(tools=_knowledge_tools(self.registry))
        mission = await planner.plan(mission)
        return mission

    # ── Parallel investigation (Investigator subagents) ─────────────────────

    async def investigate(self, mission: Mission, hypothesis_ids: set[str] | None = None) -> Mission:
        investigator = InvestigatorAgent(tools=_tools_from_registry(self.registry))
        return await investigator.investigate(mission, hypothesis_ids)

    # ── Evidence verification (Verifier) ────────────────────────────────────

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        return await self.verifier.verify(mission)

    # ── Adaptive replanning (Planner subagent) ───────────────────────────────

    async def replan(self, mission: Mission, reason_text: str | None = None) -> Mission:
        mission.replans += 1
        from autopilot.agents.base import SubAgent
        system = """\
You are the Adaptive Replanner in AUTOPILOT.
The current investigation has insufficient confidence. Add 1-2 NEW hypotheses that
weren't explored yet — different dimensions, different entities, different failure modes.

Return: {"action": "answer", "result": {"new_hypotheses": [
  {"title": "...", "rationale": "...", "search_focus": "..."}
]}}
"""
        task = (
            f"Mission: {mission.title}\nConfidence: {mission.confidence:.2f}\n"
            f"Replan reason: {reason_text or 'Confidence below threshold.'}\n\n"
            f"Existing hypotheses:\n"
            + "\n".join(f"  - {h.title}" for h in mission.hypotheses)
            + "\n\nAdd 1-2 new investigation angles not yet explored."
        )
        agent = SubAgent(role="planner", tools=_knowledge_tools(self.registry), max_steps=4, system_prompt=system)
        result = await agent.run(task)

        added = 0
        new_hyps = result.answer.get("new_hypotheses", []) if isinstance(result.answer, dict) else []
        for item in new_hyps if isinstance(new_hyps, list) else []:
            if isinstance(item, dict):
                mission.hypotheses.append(Hypothesis(
                    title=str(item.get("title", "Follow-up investigation")),
                    rationale=str(item.get("rationale", "")),
                    confidence=0.3,
                ))
                added += 1

        if added == 0:
            entities = ", ".join(list({e for s in mission.signals for e in s.entities})[:3]) or "affected system"
            mission.hypotheses.append(Hypothesis(
                title="Broad evidence sweep",
                rationale=reason_text or f"Low confidence; run wider search around {entities}.",
                confidence=0.3,
            ))
        log.info("Replanner: added %d new hypotheses (replan #%d)", added or 1, mission.replans)
        return mission

    # ── Brief synthesis + action publishing (Executor + Validator) ──────────

    async def synthesize(self, mission: Mission) -> str:
        executor = ExecutorAgent()
        return await executor.synthesize_brief(mission)

    async def publish_actions(self, mission: Mission, brief: str) -> tuple[list[ActionResult], dict[str, Any]]:
        """Execute approved actions through connectors and validate results."""
        actions: list[ActionResult] = []

        # 1. Write artifact report (always runs if artifact connector is present)
        writers = [c for c in self.registry.by_capability(Capability.WRITE)
                   if "write_report" in c.manifest.safe_actions]
        for writer in writers:
            decision = self.governor.decide(mission, writer, "write_report")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await writer.write(f"mission-{mission.id}", brief, {"mission_id": mission.id}))

        # 2. Create GitHub issue (if configured and policy allows)
        for connector in self.registry.by_capability(Capability.ACTION):
            if "create_issue" not in connector.manifest.safe_actions:
                continue
            readiness = connector.readiness("create_issue")
            if not readiness.get("action_ready"):
                actions.append(ActionResult(
                    connector=connector.manifest.name,
                    action="create_issue",
                    status="skipped",
                    summary=f"Connector not ready: {readiness.get('detail', 'missing credentials')}",
                    metadata={"readiness": readiness},
                ))
                continue

            payload = {
                "title": f"[AUTOPILOT] {mission.title[:120]}",
                "body": brief,
                "description": brief,
                "severity": mission.severity,
                "confidence": mission.confidence,
                "labels": ["autopilot", "incident"] if mission.severity in {"high", "critical"} else ["autopilot"],
            }
            decision = await self.governor.decide_async(mission, connector, "create_issue")
            mission.policy_decisions.append(decision)

            if decision.allowed:
                result = await connector.action("create_issue", payload)
                actions.append(result)
            elif decision.requires_validation:
                approval = ActionApproval(
                    mission_id=mission.id,
                    connector=connector.manifest.name,
                    action="create_issue",
                    payload=payload,
                    risk=decision.risk,
                    reason=decision.reason,
                )
                mission.approvals.append(approval)
                actions.append(ActionResult(
                    connector=connector.manifest.name,
                    action="create_issue",
                    status="pending_approval",
                    summary=f"Queued approval {approval.id} — confidence={mission.confidence:.2f}",
                    metadata={"approval_id": approval.id, "risk": approval.risk.value},
                ))

        # 3. Write action packet
        packet_writers = [c for c in self.registry.by_capability(Capability.ACTION)
                          if "write_action_packet" in c.manifest.safe_actions]
        if packet_writers:
            packet = {
                "packet_name": f"action-packet-{mission.id}",
                "mission_id": mission.id,
                "title": mission.title,
                "severity": mission.severity,
                "confidence": mission.confidence,
                "hypotheses": [h.model_dump() for h in mission.hypotheses],
                "evidence_count": len(mission.evidence),
                "recommended_action": self._recommended_action(mission),
            }
            decision = self.governor.decide(mission, packet_writers[0], "write_action_packet")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await packet_writers[0].action("write_action_packet", packet))

        # 4. Notify ops
        notifiers = self.registry.by_capability(Capability.NOTIFY)
        if notifiers:
            decision = self.governor.decide(mission, notifiers[0], "notify_ops")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                text = (
                    f"AUTOPILOT mission complete: {mission.title} | "
                    f"severity={mission.severity} | confidence={mission.confidence:.2f} | "
                    f"evidence={len(mission.evidence)} | replans={mission.replans}"
                )
                actions.append(await notifiers[0].action("notify_ops", {"mission_id": mission.id, "text": text}))

        # 5. Validate outcomes
        validator = ValidatorAgent()
        validation = await validator.validate(mission, actions)

        return actions, validation

    def _recommended_action(self, mission: Mission) -> str:
        lead = max(mission.hypotheses, key=lambda h: h.confidence, default=None)
        if lead and ("regression" in lead.title.lower() or "rollout" in lead.title.lower()) and mission.confidence >= 0.65:
            return "Prepare rollback or disable the affected feature flag."
        if mission.severity in {"critical", "high"}:
            return "Notify the owning team and create a follow-up task."
        return "Record the investigation and continue monitoring."

    # ── Helpers ─────────────────────────────────────────────────────────────

    def entities(self, mission: Mission) -> list[str]:
        seen: list[str] = []
        for signal in mission.signals:
            for entity in signal.entities:
                norm = entity.strip()
                if norm and norm not in seen:
                    seen.append(norm)
        return seen
