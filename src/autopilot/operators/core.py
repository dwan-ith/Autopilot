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
from typing import Any

from autopilot.agents.base import Tool
from autopilot.agents.mission.executor import ExecutorAgent
from autopilot.agents.mission.investigator import InvestigatorAgent
from autopilot.agents.mission.planner import PlannerAgent
from autopilot.agents.mission.reflection import ReflectionAgent
from autopilot.agents.mission.validator import ValidatorAgent
from autopilot.agents.persistent.correlator import CorrelatorAgent
from autopilot.agents.persistent.governor import GovernorAgent
from autopilot.agents.persistent.memory import MemoryAgent
from autopilot.agents.persistent.verifier import VerifierAgent
from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import (
    ActionApproval,
    ActionResult,
    Capability,
    Hypothesis,
    Mission,
)

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

    async def reflect_mission(self, mission: Mission) -> Mission:
        """Cross-branch synthesis after verification (reads tools optional)."""
        reflector = ReflectionAgent(tools=_knowledge_tools(self.registry))
        return await reflector.reflect(mission)

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
                    search_focus=str(item.get("search_focus", item.get("title", ""))),
                ))
                added += 1

        if added == 0:
            entities = ", ".join(list({e for s in mission.signals for e in s.entities})[:3]) or "affected system"
            mission.hypotheses.append(Hypothesis(
                title="Broad evidence sweep",
                rationale=reason_text or f"Low confidence; run wider search around {entities}.",
                confidence=0.3,
                search_focus=f"{entities} {mission.title}".strip(),
            ))
        log.info("Replanner: added %d new hypotheses (replan #%d)", added or 1, mission.replans)
        return mission

    # ── Brief synthesis + action publishing (Executor + Validator) ──────────

    async def synthesize(self, mission: Mission) -> str:
        executor = ExecutorAgent()
        return await executor.synthesize_brief(mission)

    async def publish_actions(self, mission: Mission, brief: str) -> tuple[list[ActionResult], dict[str, Any]]:
        """Execute approved actions through connectors, in LLM-generated priority order."""
        executor = ExecutorAgent()
        actions: list[ActionResult] = []

        # Collect all connectors that could be actioned
        write_connectors = [c for c in self.registry.by_capability(Capability.WRITE)]
        action_connectors = [c for c in self.registry.by_capability(Capability.ACTION)]
        notify_connectors = [c for c in self.registry.by_capability(Capability.NOTIFY)]
        all_connectors = list({c.manifest.name: c for c in
                               write_connectors + action_connectors + notify_connectors}.values())

        # Ask Executor to generate a priority-ordered plan
        plan = await executor.plan_actions(mission, brief, all_connectors)
        log.info("Executor plan: %s", [(s.get("connector"), s.get("action")) for s in plan])

        # Build a lookup: connector_name → connector instance
        connector_map = {c.manifest.name: c for c in all_connectors}

        for step in plan:
            connector_name = step.get("connector", "")
            action_name = step.get("action", "")

            connector = connector_map.get(connector_name)
            if not connector:
                log.debug("Executor: skipping step — connector '%s' not in registry", connector_name)
                continue

            if action_name not in connector.manifest.safe_actions:
                log.debug("Executor: '%s.%s' not in safe_actions — skipping", connector_name, action_name)
                continue

            # Check readiness
            readiness = connector.readiness(action_name)
            if not readiness.get("action_ready") and action_name not in {"write_report", "write_action_packet", "notify_ops"}:
                actions.append(ActionResult(
                    connector=connector_name,
                    action=action_name,
                    status="skipped",
                    summary=f"Connector not ready: {readiness.get('detail', 'missing credentials')}",
                    metadata={"readiness": readiness},
                ))
                continue

            # Run through Governor
            decision = await self.governor.decide_async(mission, connector, action_name)
            mission.policy_decisions.append(decision)

            if not decision.allowed and decision.requires_validation:
                # Mandatory human gate: queued whenever validation is required,
                # regardless of any LLM endorsement. Only an explicit operator
                # decision via the approvals API can release this action.
                payload = self._action_payload(mission, brief, connector_name, action_name)
                approval = ActionApproval(
                    mission_id=mission.id,
                    connector=connector_name,
                    action=action_name,
                    payload=payload,
                    risk=decision.risk,
                    reason=decision.reason,
                )
                mission.approvals.append(approval)
                actions.append(ActionResult(
                    connector=connector_name,
                    action=action_name,
                    status="pending_approval",
                    summary=f"Queued approval {approval.id} — {decision.reason}",
                    metadata={"approval_id": approval.id, "risk": approval.risk.value},
                ))
                continue

            if not decision.allowed:
                actions.append(ActionResult(
                    connector=connector_name,
                    action=action_name,
                    status="blocked",
                    summary=decision.reason,
                    metadata={"policy": decision.model_dump()},
                ))
                continue

            # Execute
            payload = self._action_payload(mission, brief, connector_name, action_name)
            try:
                if action_name == "write_report":
                    result = await connector.write(f"mission-{mission.id}", brief, {"mission_id": mission.id})
                else:
                    result = await connector.action(action_name, payload)
                actions.append(result)
            except Exception as exc:
                log.error("Executor: %s.%s failed: %s", connector_name, action_name, exc)
                actions.append(ActionResult(
                    connector=connector_name,
                    action=action_name,
                    status="failed",
                    summary=str(exc),
                ))

        # Validate outcomes
        validator = ValidatorAgent()
        validation = await validator.validate(mission, actions)

        return actions, validation

    def _action_payload(self, mission: Mission, brief: str, connector: str, action: str) -> dict[str, Any]:
        """Build the appropriate payload for a given connector/action combination."""
        base = {"mission_id": mission.id, "severity": mission.severity, "confidence": mission.confidence}
        if action == "create_issue":
            return {
                **base,
                "title": f"[AUTOPILOT] {mission.title[:120]}",
                "body": brief,
                "description": brief,
                "labels": ["autopilot", "incident"] if mission.severity in {"high", "critical"} else ["autopilot"],
            }
        if action == "notify_ops":
            return {
                **base,
                "text": (
                    f"AUTOPILOT mission complete: {mission.title} | "
                    f"severity={mission.severity} | confidence={mission.confidence:.2f} | "
                    f"evidence={len(mission.evidence)} | replans={mission.replans}"
                ),
            }
        if action == "write_action_packet":
            return {
                **base,
                "packet_name": f"action-packet-{mission.id}",
                "title": mission.title,
                "hypotheses": [h.model_dump() for h in mission.hypotheses],
                "evidence": [e.model_dump() for e in mission.evidence[:20]],
                "evidence_count": len(mission.evidence),
                "recommended_action": self._recommended_action(mission),
            }
        if action == "trigger_deployment":
            return {
                **base,
                "environment": "staging",
                "ref": "main",
                "reason": f"AUTOPILOT automated deployment trigger — {mission.title}",
            }
        # Generic fallback for any other action
        return {**base, "brief": brief[:500]}

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
