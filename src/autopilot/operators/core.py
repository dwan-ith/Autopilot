"""Scoped operators for the AUTOPILOT runtime.

Operators are capability-oriented workers. They may use an LLM when a provider
is configured, but every operator has a deterministic fallback so the runtime is
demo-safe and testable without credentials.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import ActionResult, Capability, Evidence, Hypothesis, Mission
from autopilot.operators.llm import parse_json, reason
from autopilot.policy import PolicyEngine

log = logging.getLogger("autopilot.operators")

HIGH_URGENCY = {"urgent", "high", "critical", "p0", "p1"}


SIGNAL_EVALUATOR_PROMPT = """\
You are the Signal Evaluator in AUTOPILOT.

Assess incoming operational signals and return one JSON object:
{
  "severity": "critical" | "high" | "medium" | "low",
  "impact_summary": "short impact assessment",
  "key_entities": ["entity"],
  "urgency_score": 8,
  "investigation_focus": "where investigation should focus first"
}
"""


MISSION_PLANNER_PROMPT = """\
You are the Mission Planner in AUTOPILOT.

Create competing hypotheses for the incident. Do not fixate on one cause.
Return one JSON object:
{
  "hypotheses": [
    {
      "title": "short title",
      "rationale": "why this might be true",
      "initial_confidence": 0.45
    }
  ]
}
Generate 2-4 meaningfully different hypotheses.
"""


INVESTIGATOR_PROMPT = """\
You are an Investigator in AUTOPILOT.

Generate targeted search queries for confirming and refuting evidence.
Return one JSON object:
{
  "queries": ["query 1", "query 2"],
  "reasoning": "brief strategy"
}
"""


VERIFIER_PROMPT = """\
You are the Verification Gate in AUTOPILOT.

Critically evaluate evidence quality and gaps. Return one JSON object:
{
  "analysis": "critical assessment",
  "confidence": 0.78,
  "leading_hypothesis": "title",
  "gaps": ["gap"],
  "needs_replan": true,
  "replan_reason": "why more work is needed"
}
Be honest when evidence is weak.
"""


REPLANNER_PROMPT = """\
You are the Adaptive Replanner in AUTOPILOT.

Given evidence gaps, add new investigation angles. Return one JSON object:
{
  "new_hypotheses": [
    {
      "title": "short title",
      "rationale": "why this new angle matters",
      "search_focus": "what to look for"
    }
  ],
  "strategy": "brief strategy"
}
"""


SYNTHESIZER_PROMPT = """\
You are the Synthesis Operator in AUTOPILOT.

Write a concise operational brief in Markdown with:
# AUTOPILOT Mission Brief
## Incident Summary
## Root Cause Assessment
## Evidence Summary
## Operational Recommendation
## Risk Assessment
Be specific and action-oriented.
"""


class OperatorSuite:
    """Coordinates scoped operators across a mission lifecycle."""

    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry
        self.policy = PolicyEngine()

    async def evaluate_signal(self, mission: Mission) -> Mission:
        signal_text = "\n".join(
            f"[{s.source}/{s.type}] urgency={s.urgency}\n{s.summary}\nentities: {', '.join(s.entities)}"
            for s in mission.signals
        )
        raw = await reason(
            SIGNAL_EVALUATOR_PROMPT,
            f"Evaluate these {len(mission.signals)} signal(s):\n\n{signal_text}",
            json_mode=True,
        )
        result = parse_json(raw)

        if isinstance(result, dict):
            mission.severity = str(result.get("severity", mission.severity))
            impact = str(result.get("impact_summary", "")).strip()
            focus = str(result.get("investigation_focus", "")).strip()
            mission.summary = " ".join(part for part in [impact, f"Focus: {focus}" if focus else ""] if part)
            for signal in mission.signals:
                for entity in result.get("key_entities", []) or []:
                    if entity not in signal.entities:
                        signal.entities.append(str(entity))
            return mission

        summaries = " ".join(signal.summary.lower() for signal in mission.signals)
        urgency_max = max((2 if s.urgency.lower() in HIGH_URGENCY else 1 for s in mission.signals), default=1)
        impact_words = sum(
            word in summaries
            for word in ["customer", "enterprise", "failure", "error", "spike", "urgent", "sla", "outage"]
        )
        mission.severity = "high" if urgency_max == 2 or impact_words >= 3 else "medium"
        mission.summary = (
            f"{len(mission.signals)} signal(s) normalized; severity={mission.severity}; "
            f"entities={', '.join(self.entities(mission)) or 'none'}."
        )
        return mission

    async def plan_mission(self, mission: Mission) -> Mission:
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Assessment: {mission.summary}\n\n"
            f"Signals:\n{self._signal_lines(mission)}"
        )
        raw = await reason(MISSION_PLANNER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if isinstance(result, dict) and isinstance(result.get("hypotheses"), list):
            hypotheses = []
            for item in result["hypotheses"]:
                if isinstance(item, dict):
                    hypotheses.append(
                        Hypothesis(
                            title=str(item.get("title", "Unnamed hypothesis")),
                            rationale=str(item.get("rationale", "")),
                            confidence=float(item.get("initial_confidence", 0.35)),
                        )
                    )
            if hypotheses:
                mission.hypotheses = hypotheses
                return mission

        mission.hypotheses = self._heuristic_hypotheses(mission)
        return mission

    async def investigate(self, mission: Mission, hypothesis_ids: set[str] | None = None) -> Mission:
        searchers = self.registry.by_capability(Capability.SEARCH)
        if not searchers:
            return mission

        target_hypotheses = [
            hypothesis for hypothesis in mission.hypotheses
            if hypothesis_ids is None or hypothesis.id in hypothesis_ids
        ]

        async def investigate_hypothesis(hypothesis: Hypothesis) -> list[Evidence]:
            queries = await self._queries_for(mission, hypothesis)
            gathered: list[Evidence] = []
            for query in queries[:3]:
                await asyncio.sleep(0.05)
                batches = await asyncio.gather(*(connector.search(query) for connector in searchers))
                for batch in batches:
                    gathered.extend(batch)

            hypothesis.evidence_ids.extend(evidence.id for evidence in gathered if evidence.id not in hypothesis.evidence_ids)
            if gathered:
                hypothesis.confidence = min(0.92, max(e.confidence for e in gathered) * 0.82 + 0.14)
            return gathered

        batches = await asyncio.gather(*(investigate_hypothesis(hyp) for hyp in target_hypotheses))
        gathered = [item for batch in batches for item in batch]
        mission.evidence.extend(self._dedupe_evidence(mission.evidence, gathered))
        return mission

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Replans: {mission.replans}\n\n"
            f"Hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=10)}"
        )
        raw = await reason(VERIFIER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if isinstance(result, dict):
            mission.confidence = round(min(0.96, float(result.get("confidence", 0.5))), 2)
            needs_replan = bool(result.get("needs_replan", False)) and mission.replans < 2
            return mission, needs_replan

        strong_sources = {e.title for e in mission.evidence if e.confidence >= 0.65}
        source_bonus = min(0.18, len(strong_sources) * 0.04)
        signal_bonus = min(0.16, len(mission.signals) * 0.06)
        best = max((h.confidence for h in mission.hypotheses), default=0)
        mission.confidence = round(min(0.96, best + signal_bonus + source_bonus), 2)
        return mission, mission.confidence < 0.72 and mission.replans < 2

    async def replan(self, mission: Mission, reason_text: str | None = None) -> Mission:
        mission.replans += 1
        prompt = (
            f"Mission: {mission.title}\n"
            f"Current hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=8)}\n\n"
            f"Replan reason: {reason_text or 'Evidence confidence is below threshold.'}"
        )
        raw = await reason(REPLANNER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        added = 0
        if isinstance(result, dict) and isinstance(result.get("new_hypotheses"), list):
            for item in result["new_hypotheses"]:
                if isinstance(item, dict):
                    mission.hypotheses.append(
                        Hypothesis(
                            title=str(item.get("title", "Follow-up investigation")),
                            rationale=str(item.get("rationale", "")),
                            confidence=0.3,
                        )
                    )
                    added += 1

        if added == 0:
            entities = ", ".join(self.entities(mission)) or "affected system"
            mission.hypotheses.append(
                Hypothesis(
                    title="Follow-up evidence gap investigation",
                    rationale=reason_text
                    or f"Confidence below threshold; run scoped search for independent evidence around {entities}.",
                    confidence=0.3,
                )
            )
        return mission

    async def synthesize(self, mission: Mission) -> str:
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Confidence: {mission.confidence:.2f}\n"
            f"Replans: {mission.replans}\n\n"
            f"Signals:\n{self._signal_lines(mission)}\n\n"
            f"Hypotheses:\n{self._hypothesis_lines(mission)}\n\n"
            f"Evidence:\n{self._evidence_lines(mission, limit=10)}"
        )
        raw = await reason(SYNTHESIZER_PROMPT, prompt, temperature=0.4)
        return raw if raw else self._heuristic_brief(mission)

    async def publish_actions(self, mission: Mission, brief: str) -> list[ActionResult]:
        actions: list[ActionResult] = []

        writers = [connector for connector in self.registry.by_capability(Capability.WRITE) if "write_report" in connector.manifest.safe_actions]
        if writers:
            writer = writers[0]
            decision = self.policy.decide(mission, writer, "write_report")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(
                    await writer.write(
                        f"mission-{mission.id}",
                        brief,
                        {"mission_id": mission.id, "confidence": mission.confidence},
                    )
                )

        action_connectors = self.registry.by_capability(Capability.ACTION)
        packet_writers = [connector for connector in action_connectors if "write_action_packet" in connector.manifest.safe_actions]
        if packet_writers:
            packet = {
                "mission_id": mission.id,
                "title": mission.title,
                "severity": mission.severity,
                "confidence": mission.confidence,
                "hypotheses": [hyp.model_dump() for hyp in mission.hypotheses],
                "evidence_count": len(mission.evidence),
                "recommended_action": self._recommended_action(mission),
            }
            decision = self.policy.decide(mission, packet_writers[0], "write_action_packet")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(await packet_writers[0].action(f"action-packet-{mission.id}", packet))

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
            else:
                actions.append(
                    ActionResult(
                        connector=notifiers[0].manifest.name,
                        action="notify_ops",
                        status="blocked",
                        summary=decision.reason,
                        metadata={"policy_decision_id": decision.id},
                    )
                )

        issue_connectors = [connector for connector in action_connectors if "create_issue" in connector.manifest.safe_actions]
        for connector in issue_connectors:
            decision = self.policy.decide(mission, connector, "create_issue")
            mission.policy_decisions.append(decision)
            if not decision.allowed:
                actions.append(
                    ActionResult(
                        connector=connector.manifest.name,
                        action="create_issue",
                        status="blocked",
                        summary=decision.reason,
                        metadata={"policy_decision_id": decision.id},
                    )
                )
                continue
            actions.append(
                await connector.action(
                    "create_issue",
                    {
                        "mission_id": mission.id,
                        "title": f"[AUTOPILOT] {mission.title[:120]}",
                        "description": brief,
                        "severity": mission.severity,
                        "confidence": mission.confidence,
                    },
                )
            )

        callback_connectors = [connector for connector in action_connectors if "webhook_callback" in connector.manifest.safe_actions]
        for connector in callback_connectors:
            decision = self.policy.decide(mission, connector, "webhook_callback")
            mission.policy_decisions.append(decision)
            if decision.allowed:
                actions.append(
                    await connector.action(
                        "webhook_callback",
                        {
                            "mission_id": mission.id,
                            "title": mission.title,
                            "severity": mission.severity,
                            "confidence": mission.confidence,
                            "status": "complete",
                        },
                    )
                )

        return actions

    def entities(self, mission: Mission) -> list[str]:
        seen: list[str] = []
        for signal in mission.signals:
            for entity in signal.entities:
                normalized = entity.strip()
                if normalized and normalized not in seen:
                    seen.append(normalized)
        return seen

    def mission_text(self, mission: Mission) -> str:
        return " ".join(
            [mission.title, mission.summary, *[s.summary for s in mission.signals], *self.entities(mission)]
        ).lower()

    async def _queries_for(self, mission: Mission, hypothesis: Hypothesis) -> list[str]:
        prompt = (
            f"Hypothesis: {hypothesis.title}\n"
            f"Rationale: {hypothesis.rationale}\n"
            f"Mission context: {mission.summary}\n"
            f"Entities: {', '.join(self.entities(mission))}"
        )
        raw = await reason(INVESTIGATOR_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)
        if isinstance(result, dict) and isinstance(result.get("queries"), list):
            queries = [str(query) for query in result["queries"] if str(query).strip()]
            if queries:
                return queries
        return [f"{hypothesis.title} {' '.join(self.entities(mission))} {mission.signals[-1].summary}"]

    def _dedupe_evidence(self, existing: list[Evidence], new_items: list[Evidence]) -> list[Evidence]:
        seen = {self._evidence_key(item) for item in existing}
        unique = []
        for item in new_items:
            key = self._evidence_key(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _evidence_key(self, evidence: Evidence) -> tuple[str, str, str]:
        return (
            evidence.source.strip().lower(),
            evidence.title.strip().lower(),
            evidence.summary[:120].strip().lower(),
        )

    def _heuristic_hypotheses(self, mission: Mission) -> list[Hypothesis]:
        text = self.mission_text(mission)
        candidates = [
            (
                "Rollout or configuration regression",
                "The signal mentions rollout, export, job failures, or sudden changes, so a recent change may be causal.",
                ["rollout", "export", "job", "failure", "config", "schema"],
            ),
            (
                "Customer-impacting operational incident",
                "The signal may affect a customer or SLA and needs an action packet even if root cause is uncertain.",
                ["customer", "enterprise", "urgent", "sla"],
            ),
            (
                "Service health or dependency degradation",
                "The symptoms include errors, latency, status changes, or dependency behavior.",
                ["error", "latency", "spike", "status", "dependency"],
            ),
        ]
        hypotheses = [
            Hypothesis(title=title, rationale=rationale)
            for title, rationale, keywords in candidates
            if any(keyword in text for keyword in keywords)
        ]
        return hypotheses or [
            Hypothesis(
                title="Unclassified operational signal",
                rationale="No specialized pattern matched; broad investigation is required.",
                confidence=0.25,
            )
        ]

    def _heuristic_brief(self, mission: Mission) -> str:
        top = sorted(mission.hypotheses, key=lambda item: item.confidence, reverse=True)
        lead = top[0] if top else None
        recommendation = "Create an owner-visible action packet and continue monitoring."
        if lead and "regression" in lead.title.lower() and mission.confidence >= 0.72:
            recommendation = "Inspect the recent rollout or configuration change and prepare rollback or flag-disable steps."
        elif mission.severity in {"critical", "high"}:
            recommendation = "Escalate to the owning team with customer-safe status copy and evidence-backed next actions."

        return f"""# AUTOPILOT Mission Brief

Generated: {datetime.now(timezone.utc).isoformat()}
Mission: {mission.title}
Severity: {mission.severity}
Confidence: {mission.confidence:.2f}
Replans: {mission.replans}

## Incident Summary
{mission.summary}

## Signals
{self._signal_lines(mission)}

## Root Cause Assessment
{lead.title if lead else "No leading hypothesis selected."}

{lead.rationale if lead else ""}

## Evidence Summary
{self._evidence_lines(mission, limit=8) or "- No evidence gathered."}

## Operational Recommendation
{recommendation}

## Policy-Bounded Actions
- Durable report write is allowed by the artifact connector.
- Notification is allowed only when mission confidence passes policy.
"""

    def _recommended_action(self, mission: Mission) -> str:
        lead = max(mission.hypotheses, key=lambda item: item.confidence, default=None)
        if lead and "regression" in lead.title.lower() and mission.confidence >= 0.72:
            return "Prepare rollback or disable the affected feature flag."
        if mission.severity in {"critical", "high"}:
            return "Notify the owning team and create an owner-visible follow-up task."
        return "Record the investigation and continue monitoring for additional signals."

    def _signal_lines(self, mission: Mission) -> str:
        return "\n".join(f"- [{s.source}/{s.type}] {s.summary}" for s in mission.signals)

    def _hypothesis_lines(self, mission: Mission) -> str:
        return "\n".join(
            f"- {h.title} (confidence={h.confidence:.2f}): {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda item: item.confidence, reverse=True)
        )

    def _evidence_lines(self, mission: Mission, limit: int) -> str:
        return "\n".join(
            f"- [{e.source}] {e.title} (confidence={e.confidence:.2f}): {e.summary}"
            for e in mission.evidence[:limit]
        )
