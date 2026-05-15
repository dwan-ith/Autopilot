"""Real sub-agent operator suite for AUTOPILOT.

Each operator now spawns genuine SubAgent instances that run
reason → tool_call → observe loops, rather than making a single
sequential LLM call and returning. The Orchestrator runs parallel
sub-agents for investigation branches.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from autopilot.agents.base import Orchestrator, SubAgent, Tool
from autopilot.connectors.base import Connector, ConnectorRegistry
from autopilot.models import ActionResult, Capability, Evidence, Hypothesis, Mission
from autopilot.operators.llm import parse_json, reason
from autopilot.policy import PolicyEngine

log = logging.getLogger("autopilot.operators")

HIGH_URGENCY = {"urgent", "high", "critical", "p0", "p1"}


# ── Utility: build tool list from the connector registry ────────────────

def _tools_from_registry(registry: ConnectorRegistry) -> list[Tool]:
    """Convert connector capabilities into SubAgent tools."""
    tools: list[Tool] = []

    for connector in registry.by_capability(Capability.SEARCH):
        # Generic search tool per connector
        name = connector.manifest.name
        tools.append(Tool(
            name=f"search_{name}",
            description=f"Search {name} for relevant information. Returns list of evidence items.",
            parameters={"query": "Search query string"},
            fn=connector.search,
        ))

        # If the connector exposes as_tools(), use those (richer interface)
        if hasattr(connector, "as_tools"):
            tools.extend(connector.as_tools())  # type: ignore[attr-defined]

    return tools


# ── Signal Evaluator (single agent, not parallel) ────────────────────────

EVALUATOR_SYSTEM = """\
You are the Signal Evaluator in AUTOPILOT. Your job is to triage incoming signals.

You have no tools. Analyze the signals and return your assessment as:
{"action": "answer", "result": {
  "severity": "critical|high|medium|low",
  "impact_summary": "concise impact statement",
  "key_entities": ["entity1"],
  "investigation_focus": "where to focus first"
}}
"""


# ── Mission Planner (single agent, no tools) ─────────────────────────────

PLANNER_SYSTEM = """\
You are the Mission Planner in AUTOPILOT. Create competing hypotheses for the incident.

Return exactly: {"action": "answer", "result": {"hypotheses": [
  {"title": "short title", "rationale": "why this might be true", "initial_confidence": 0.45}
]}}

Generate 2-4 meaningfully different hypotheses. Never fixate on a single cause.
"""


# ── Investigator (real sub-agent with tool-use loop) ─────────────────────

INVESTIGATOR_SYSTEM = """\
You are an Investigator sub-agent in AUTOPILOT. You have tools to search for real data.

Your job:
1. Use your search tools to gather actual evidence for or against your hypothesis.
2. Make 2-4 targeted tool calls with specific queries.
3. After gathering evidence, return a summary.

Return final answer as:
{"action": "answer", "result": {
  "evidence_gathered": [{"title": "...", "summary": "...", "confidence": 0.7}],
  "assessment": "what the evidence shows",
  "confidence": 0.75
}}
"""


# ── Verifier (single agent, no tools) ────────────────────────────────────

VERIFIER_SYSTEM = """\
You are the Verification Gate in AUTOPILOT. Critically evaluate evidence quality.

{"action": "answer", "result": {
  "analysis": "critical assessment of evidence",
  "confidence": 0.78,
  "leading_hypothesis": "title",
  "gaps": ["gap1"],
  "needs_replan": false,
  "replan_reason": "why or why not"
}}

Be honest. If evidence is weak, say so.
"""


# ── Synthesizer (single agent, no tools) ────────────────────────────────

SYNTHESIZER_SYSTEM = """\
You are the Synthesis Operator. Write a concise operational brief in Markdown.

Return: {"action": "answer", "result": {"brief": "# AUTOPILOT Mission Brief\\n..."}}

Structure: Incident Summary / Root Cause / Evidence Summary / Recommendation / Risk
"""


class OperatorSuite:
    """Orchestrates real sub-agents across the mission lifecycle."""

    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry
        self.policy = PolicyEngine()
        self._tools = _tools_from_registry(registry)

    def _orchestrator(self) -> Orchestrator:
        return Orchestrator(tools=self._tools, max_parallel=5)

    async def evaluate_signal(self, mission: Mission) -> Mission:
        signal_text = "\n".join(
            f"[{s.source}/{s.type}] urgency={s.urgency}\n{s.summary}\nentities: {', '.join(s.entities)}"
            for s in mission.signals
        )
        agent = SubAgent(role="Signal Evaluator", tools=[], max_steps=2)
        result = await agent.run(
            f"Evaluate these {len(mission.signals)} signal(s):\n\n{signal_text}"
        )

        r = result.answer
        if isinstance(r, dict) and "severity" in r:
            mission.severity = str(r.get("severity", mission.severity))
            impact = str(r.get("impact_summary", "")).strip()
            focus = str(r.get("investigation_focus", "")).strip()
            mission.summary = " ".join(p for p in [impact, f"Focus: {focus}" if focus else ""] if p)
            for signal in mission.signals:
                for entity in r.get("key_entities", []) or []:
                    if entity not in signal.entities:
                        signal.entities.append(str(entity))
        else:
            mission = self._heuristic_evaluate(mission)

        log.info("Signal Evaluator: severity=%s summary=%.80s", mission.severity, mission.summary)
        return mission

    async def plan_mission(self, mission: Mission) -> Mission:
        agent = SubAgent(role="Mission Planner", tools=[], max_steps=2)
        result = await agent.run(
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Assessment: {mission.summary}\n\n"
            f"Signals:\n{self._signal_lines(mission)}"
        )

        r = result.answer
        hypotheses_data = r.get("hypotheses") if isinstance(r, dict) else None
        if isinstance(hypotheses_data, list) and hypotheses_data:
            mission.hypotheses = [
                Hypothesis(
                    title=str(item.get("title", "Unnamed")),
                    rationale=str(item.get("rationale", "")),
                    confidence=float(item.get("initial_confidence", 0.35)),
                )
                for item in hypotheses_data
                if isinstance(item, dict)
            ]
        else:
            mission.hypotheses = self._heuristic_hypotheses(mission)

        log.info("Mission Planner: spawned %d hypotheses", len(mission.hypotheses))
        return mission

    async def investigate(self, mission: Mission, hypothesis_ids: set[str] | None = None) -> Mission:
        """Spawn parallel investigator sub-agents — one per hypothesis branch."""
        target_hyps = [
            h for h in mission.hypotheses
            if hypothesis_ids is None or h.id in hypothesis_ids
        ]

        if not target_hyps:
            return mission

        if not self._tools:
            log.info("No search tools available; skipping investigation")
            return mission

        # Build parallel agent tasks — one per hypothesis
        tasks = [
            (
                "Investigator",
                f"Hypothesis: {h.title}\n"
                f"Rationale: {h.rationale}\n"
                f"Mission context: {mission.summary}\n"
                f"Key entities: {', '.join(self.entities(mission))}\n\n"
                f"Search for real evidence to confirm or refute this hypothesis."
            )
            for h in target_hyps
        ]

        log.info("Spawning %d parallel investigator sub-agents", len(tasks))
        results = await self._orchestrator().run_agents(tasks)

        for hyp, agent_result in zip(target_hyps, results):
            if not agent_result.success:
                log.warning("Investigator for '%s' failed: %s", hyp.title, agent_result.error)
                continue

            evidence_data = agent_result.answer.get("evidence_gathered", [])
            if not isinstance(evidence_data, list):
                continue

            gathered = []
            for item in evidence_data:
                if isinstance(item, dict):
                    gathered.append(Evidence(
                        source=item.get("source", "investigator"),
                        title=str(item.get("title", "Evidence")),
                        summary=str(item.get("summary", "")),
                        url=item.get("url"),
                        confidence=float(item.get("confidence", 0.5)),
                        metadata={"agent_id": agent_result.agent_id, "hypothesis": hyp.title},
                    ))

            # Also capture tool results as evidence
            for step in agent_result.steps:
                if step.tool_result and step.tool_result.success:
                    raw = step.tool_result.output
                    if isinstance(raw, list):
                        gathered.extend(e for e in raw if isinstance(e, Evidence))

            new_evidence = self._dedupe_evidence(mission.evidence, gathered)
            mission.evidence.extend(new_evidence)
            hyp.evidence_ids.extend(e.id for e in new_evidence)
            if gathered:
                hyp.confidence = min(0.92, float(agent_result.answer.get("confidence", 0.5)) * 0.85 + 0.14)
                log.info("Investigator: hypothesis '%s' → confidence=%.2f, evidence=%d", hyp.title, hyp.confidence, len(gathered))

        return mission

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        agent = SubAgent(role="Verification Gate", tools=[], max_steps=2)
        result = await agent.run(
            f"Mission: {mission.title}\nSeverity: {mission.severity}\nReplans: {mission.replans}\n\n"
            f"Hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=10)}"
        )

        r = result.answer
        if isinstance(r, dict) and "confidence" in r:
            mission.confidence = round(min(0.96, float(r.get("confidence", 0.5))), 2)
            needs_replan = bool(r.get("needs_replan", False)) and mission.replans < 2
            log.info("Verification Gate: confidence=%.2f needs_replan=%s", mission.confidence, needs_replan)
            return mission, needs_replan

        return mission, self._heuristic_verify(mission)

    async def replan(self, mission: Mission, reason_text: str | None = None) -> Mission:
        mission.replans += 1
        agent = SubAgent(role="Adaptive Replanner", tools=[], max_steps=2)
        result = await agent.run(
            f"Mission: {mission.title}\n"
            f"Hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=8)}\n\n"
            f"Replan reason: {reason_text or 'Confidence below threshold.'}\n\n"
            f"Add 1-2 new investigation angles that were not yet explored. "
            f"Return: {{\"action\": \"answer\", \"result\": {{\"new_hypotheses\": ["
            f"{{\"title\": \"...\", \"rationale\": \"...\", \"search_focus\": \"...\"}}]}}}}"
        )

        added = 0
        new_hyps = result.answer.get("new_hypotheses", []) if isinstance(result.answer, dict) else []
        if isinstance(new_hyps, list):
            for item in new_hyps:
                if isinstance(item, dict):
                    mission.hypotheses.append(Hypothesis(
                        title=str(item.get("title", "Follow-up investigation")),
                        rationale=str(item.get("rationale", "")),
                        confidence=0.3,
                    ))
                    added += 1

        if added == 0:
            entities = ", ".join(self.entities(mission)) or "affected system"
            mission.hypotheses.append(Hypothesis(
                title="Follow-up evidence gap investigation",
                rationale=reason_text or f"Confidence below threshold; run scoped search for independent evidence around {entities}.",
                confidence=0.3,
            ))
        return mission

    async def synthesize(self, mission: Mission) -> str:
        agent = SubAgent(role="Synthesis Operator", tools=[], max_steps=2)
        result = await agent.run(
            f"Mission: {mission.title}\nSeverity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\nReplans: {mission.replans}\n\n"
            f"Signals:\n{self._signal_lines(mission)}\n\n"
            f"Hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=10)}"
        )
        brief = result.answer.get("brief") if isinstance(result.answer, dict) else None
        return brief if brief else self._heuristic_brief(mission)

    async def publish_actions(self, mission: Mission, brief: str) -> list[ActionResult]:
        actions: list[ActionResult] = []

        # 1. Write markdown report (artifact connector)
        writers = [c for c in self.registry.by_capability(Capability.WRITE) if "write_report" in c.manifest.safe_actions]
        for writer in writers:
            decision = self.policy.decide(mission, writer, "write_report")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await writer.write(f"mission-{mission.id}", brief, {"mission_id": mission.id}))

        # 2. Create GitHub issue if configured
        action_connectors = self.registry.by_capability(Capability.ACTION)
        issue_connectors = [c for c in action_connectors if "create_issue" in c.manifest.safe_actions]
        for connector in issue_connectors:
            decision = self.policy.decide(mission, connector, "create_issue")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await connector.action("create_issue", {
                    "title": f"[AUTOPILOT] {mission.title[:120]}",
                    "body": brief,
                    "description": brief,
                    "severity": mission.severity,
                    "confidence": mission.confidence,
                    "labels": ["autopilot", "incident"] if mission.severity in {"high", "critical"} else ["autopilot"],
                }))

        # 3. Write action packet (structured JSON)
        packet_writers = [c for c in action_connectors if "write_action_packet" in c.manifest.safe_actions]
        if packet_writers:
            packet = {
                "mission_id": mission.id,
                "title": mission.title,
                "severity": mission.severity,
                "confidence": mission.confidence,
                "hypotheses": [h.model_dump() for h in mission.hypotheses],
                "evidence_count": len(mission.evidence),
                "recommended_action": self._recommended_action(mission),
            }
            decision = self.policy.decide(mission, packet_writers[0], "write_action_packet")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await packet_writers[0].action(f"action-packet-{mission.id}", packet))

        # 4. Notify ops
        notifiers = self.registry.by_capability(Capability.NOTIFY)
        if notifiers:
            decision = self.policy.decide(mission, notifiers[0], "notify_ops")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                text = (
                    f"AUTOPILOT completed mission: {mission.title}\n"
                    f"severity={mission.severity} | confidence={mission.confidence:.2f} | "
                    f"replans={mission.replans} | evidence={len(mission.evidence)}"
                )
                actions.append(await notifiers[0].action("notify_ops", {"mission_id": mission.id, "text": text}))

        return actions

    # ── Heuristic fallbacks ──────────────────────────────────────────────

    def _heuristic_evaluate(self, mission: Mission) -> Mission:
        summaries = " ".join(s.summary.lower() for s in mission.signals)
        urgency_max = max((2 if s.urgency.lower() in HIGH_URGENCY else 1 for s in mission.signals), default=1)
        impact_words = sum(w in summaries for w in ["customer", "enterprise", "failure", "error", "spike", "outage"])
        mission.severity = "high" if urgency_max == 2 or impact_words >= 3 else "medium"
        mission.summary = (
            f"{len(mission.signals)} signal(s) normalized; severity={mission.severity}; "
            f"entities={', '.join(self.entities(mission)) or 'none'}."
        )
        return mission

    def _heuristic_verify(self, mission: Mission) -> bool:
        strong = {e.title for e in mission.evidence if e.confidence >= 0.65}
        source_bonus = min(0.18, len(strong) * 0.04)
        signal_bonus = min(0.16, len(mission.signals) * 0.06)
        best = max((h.confidence for h in mission.hypotheses), default=0)
        mission.confidence = round(min(0.96, best + signal_bonus + source_bonus), 2)
        return mission.confidence < 0.72 and mission.replans < 2

    def _heuristic_hypotheses(self, mission: Mission) -> list[Hypothesis]:
        text = self.mission_text(mission)
        candidates = [
            ("Rollout or configuration regression", "A recent change may be causal.", ["rollout", "export", "job", "failure", "config"]),
            ("Customer-impacting operational incident", "Affects customer SLA.", ["customer", "enterprise", "urgent", "sla"]),
            ("Service health or dependency degradation", "Errors, latency, or dependency behavior.", ["error", "latency", "spike", "dependency"]),
        ]
        hyps = [Hypothesis(title=t, rationale=r) for t, r, kw in candidates if any(k in text for k in kw)]
        return hyps or [Hypothesis(title="Unclassified signal", rationale="No pattern matched; broad investigation required.", confidence=0.25)]

    def _heuristic_brief(self, mission: Mission) -> str:
        top = sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        lead = top[0] if top else None
        rec = "Create an owner-visible action packet and continue monitoring."
        if lead and "regression" in lead.title.lower() and mission.confidence >= 0.72:
            rec = "Inspect the recent rollout and prepare rollback or flag-disable steps."
        elif mission.severity in {"critical", "high"}:
            rec = "Escalate to the owning team with customer-safe status copy and evidence-backed next actions."
        return (
            f"# AUTOPILOT Mission Brief\nGenerated: {datetime.now(timezone.utc).isoformat()}\n"
            f"Mission: {mission.title}\nSeverity: {mission.severity}\nConfidence: {mission.confidence:.2f}\n\n"
            f"## Incident Summary\n{mission.summary}\n\n"
            f"## Root Cause\n{lead.title if lead else 'No leading hypothesis.'}\n{lead.rationale if lead else ''}\n\n"
            f"## Evidence\n{self._evidence_lines(mission, 8) or '- None gathered.'}\n\n"
            f"## Recommendation\n{rec}\n"
        )

    def _recommended_action(self, mission: Mission) -> str:
        lead = max(mission.hypotheses, key=lambda h: h.confidence, default=None)
        if lead and "regression" in lead.title.lower() and mission.confidence >= 0.72:
            return "Prepare rollback or disable the affected feature flag."
        if mission.severity in {"critical", "high"}:
            return "Notify the owning team and create a follow-up task."
        return "Record the investigation and continue monitoring."

    # ── Helpers ─────────────────────────────────────────────────────────

    def entities(self, mission: Mission) -> list[str]:
        seen: list[str] = []
        for signal in mission.signals:
            for entity in signal.entities:
                normalized = entity.strip()
                if normalized and normalized not in seen:
                    seen.append(normalized)
        return seen

    def mission_text(self, mission: Mission) -> str:
        return " ".join([mission.title, mission.summary, *[s.summary for s in mission.signals], *self.entities(mission)]).lower()

    def _dedupe_evidence(self, existing: list[Evidence], new_items: list[Evidence]) -> list[Evidence]:
        seen = {(e.source.strip().lower(), e.title.strip().lower()) for e in existing}
        unique = []
        for item in new_items:
            key = (item.source.strip().lower(), item.title.strip().lower())
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _signal_lines(self, mission: Mission) -> str:
        return "\n".join(f"- [{s.source}/{s.type}] {s.summary}" for s in mission.signals)

    def _hypothesis_lines(self, mission: Mission) -> str:
        return "\n".join(
            f"- {h.title} (conf={h.confidence:.2f}): {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        )

    def _evidence_lines(self, mission: Mission, limit: int) -> str:
        return "\n".join(
            f"- [{e.source}] {e.title} (conf={e.confidence:.2f}): {e.summary}"
            for e in mission.evidence[:limit]
        )
