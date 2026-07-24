"""Investigator — Mission Subagent #2.

The real multi-step evidence-gathering agent. One Investigator is spawned
per hypothesis branch, all running in parallel.

Each Investigator:
1. Reads its hypothesis and search_focus
2. Makes 2–5 targeted tool calls (GitHub search, knowledge search, etc.)
3. Reasons about each result before deciding the next call
4. Returns structured evidence with honest confidence scores

Uses the OpenRouter-1 slot (highest quality) for deep evidence reasoning.
"""

from __future__ import annotations

import logging

from autopilot.agents.base import AgentResult, Orchestrator, Tool
from autopilot.models import (
    AgentRun,
    Evidence,
    GraphNodeKind,
    Hypothesis,
    Mission,
    MissionGraphNode,
    StepStatus,
    utc_now,
)

log = logging.getLogger("autopilot.agents.investigator")

_SYSTEM = """\
You are an Investigator sub-agent in AUTOPILOT. You gather real evidence.

Your job:
1. Read your hypothesis and search focus carefully.
2. Make 2–5 targeted tool calls to gather actual evidence.
3. After each tool result, reason about what it means for your hypothesis.
4. Return honest evidence — do not fabricate or embellish results.

Evidence confidence scoring:
  0.9+ = directly confirms or refutes the hypothesis with strong data
  0.7-0.9 = strongly relevant, mostly on-point
  0.5-0.7 = relevant but indirect
  0.3-0.5 = weak signal, possibly noise

Return:
{"action": "answer", "result": {
  "evidence_gathered": [
    {"title": "...", "summary": "...", "source": "...", "url": "...", "confidence": 0.75}
  ],
  "assessment": "what the evidence shows about the hypothesis",
  "hypothesis_supported": true,
  "confidence": 0.72
}}
"""


class InvestigatorAgent:
    """Mission subagent that runs parallel evidence-gathering loops."""

    role = "investigator"

    def __init__(self, tools: list[Tool]):
        self._tools = tools

    async def investigate(
        self,
        mission: Mission,
        hypothesis_ids: set[str] | None = None,
    ) -> Mission:
        """Spawn parallel Investigator sub-agents — one per hypothesis branch."""

        target_hyps = [
            h for h in mission.hypotheses
            if hypothesis_ids is None or h.id in hypothesis_ids
        ]
        if not target_hyps:
            return mission

        if not self._tools:
            log.info("No search tools available — skipping investigation")
            return mission

        entities = list({e for s in mission.signals for e in s.entities})
        tasks = [
            (
                self.role,
                f"Hypothesis: {h.title}\n"
                f"Rationale: {h.rationale}\n"
                f"Search focus: {getattr(h, 'search_focus', h.title)}\n"
                f"Mission: {mission.summary}\n"
                f"Key entities: {', '.join(entities)}\n\n"
                f"Search for real evidence to confirm or refute this hypothesis. "
                f"Make targeted searches — be specific, not generic.",
            )
            for h in target_hyps
        ]

        log.info("Spawning %d parallel Investigator sub-agents", len(tasks))
        orchestrator = Orchestrator(tools=self._tools, max_parallel=6, system_prompt=_SYSTEM)
        results = await orchestrator.run_agents(tasks)

        for hyp, agent_result in zip(target_hyps, results):
            self._integrate_result(mission, hyp, agent_result)

        return mission

    def _integrate_result(
        self,
        mission: Mission,
        hyp: Hypothesis,
        agent_result: AgentResult,
    ) -> None:
        tool_names = [s.tool_call for s in agent_result.steps if s.tool_call]
        run = AgentRun(
            id=agent_result.agent_id,
            role=agent_result.agent_role,
            status=StepStatus.COMPLETE if agent_result.success else StepStatus.FAILED,
            hypothesis_id=hyp.id,
            objective=hyp.title,
            tools=tool_names,
            tool_calls=agent_result.tool_calls_made,
            output_summary=str(agent_result.answer.get("assessment", ""))[:500]
                if isinstance(agent_result.answer, dict) else "",
            confidence=float(agent_result.answer.get("confidence", 0.0))
                if isinstance(agent_result.answer, dict) else 0.0,
            duration_ms=agent_result.total_duration_ms,
            error=agent_result.error,
            completed_at=utc_now(),
            metadata={
                "hypothesis": hyp.title,
                "step_count": len(agent_result.steps),
                "hypothesis_supported": agent_result.answer.get("hypothesis_supported", False)
                    if isinstance(agent_result.answer, dict) else False,
                "steps": [
                    {
                        "step_number": s.step_number,
                        "thought": s.thought,
                        "tool_call": s.tool_call,
                        "tool_input": s.tool_input,
                        "tool_result": {
                            "success": s.tool_result.success,
                            "output": str(s.tool_result.output)[:500] if s.tool_result.output else None,
                            "error": s.tool_result.error
                        } if s.tool_result else None
                    } for s in agent_result.steps
                ]
            },
        )
        mission.agent_runs.append(run)
        mission.graph.append(
            MissionGraphNode(
                kind=GraphNodeKind.SUBAGENT,
                title=f"Investigator: {hyp.title}",
                status=run.status,
                parent_ids=[n.id for n in mission.graph if n.ref_id == hyp.id],
                branch_id=hyp.id,
                ref_id=run.id,
                summary=run.output_summary or (agent_result.error or "Investigator completed."),
                completed_at=run.completed_at,
                metadata=run.model_dump(),
            )
        )

        if not agent_result.success:
            log.warning("Investigator for '%s' failed: %s", hyp.title, agent_result.error)
            return

        # Collect LLM-synthesized evidence
        evidence_data = agent_result.answer.get("evidence_gathered", []) if isinstance(agent_result.answer, dict) else []
        gathered: list[Evidence] = []
        if isinstance(evidence_data, list):
            for item in evidence_data:
                if isinstance(item, dict):
                    gathered.append(Evidence(
                        source=item.get("source", "investigator"),
                        title=str(item.get("title", "Evidence")),
                        summary=str(item.get("summary", ""))[:500],
                        url=item.get("url"),
                        confidence=float(item.get("confidence", 0.5)),
                        metadata={"agent_id": agent_result.agent_id, "hypothesis": hyp.title},
                    ))

        # Also promote real tool results (e.g. GitHub search returns Evidence objects)
        for step in agent_result.steps:
            if step.tool_result and step.tool_result.success:
                raw = step.tool_result.output
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, Evidence) and item.confidence > 0.05:
                            gathered.append(item)

        new_evidence = self._dedupe(mission.evidence, gathered)
        mission.evidence.extend(new_evidence)
        hyp.evidence_ids.extend(e.id for e in new_evidence)

        if gathered:
            reported_conf = float(agent_result.answer.get("confidence", 0.5)) if isinstance(agent_result.answer, dict) else 0.5
            hyp.confidence = round(min(0.93, reported_conf * 0.8 + len(new_evidence) * 0.04), 2)
            log.info(
                "Investigator: '%s' → confidence=%.2f, evidence=%d, tool_calls=%d",
                hyp.title, hyp.confidence, len(new_evidence), agent_result.tool_calls_made,
            )

    def _dedupe(self, existing: list[Evidence], new_items: list[Evidence]) -> list[Evidence]:
        seen = {(e.source.strip().lower(), e.title.strip().lower()) for e in existing}
        unique = []
        for item in new_items:
            key = (item.source.strip().lower(), item.title.strip().lower())
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
