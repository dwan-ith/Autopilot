from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import ActionResult, Capability, Evidence, Hypothesis, Mission, Signal


HIGH_URGENCY = {"urgent", "high", "critical", "p0", "p1"}


class OperatorSuite:
    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry

    async def evaluate_signal(self, mission: Mission) -> Mission:
        summaries = " ".join(signal.summary.lower() for signal in mission.signals)
        urgency_score = max((2 if signal.urgency.lower() in HIGH_URGENCY else 1 for signal in mission.signals), default=1)
        impact_words = sum(word in summaries for word in ["customer", "enterprise", "failure", "error", "spike", "urgent", "sla"])
        mission.severity = "high" if urgency_score == 2 or impact_words >= 3 else "medium"
        mission.summary = f"{len(mission.signals)} signal(s) normalized; severity={mission.severity}; entities={', '.join(self.entities(mission)) or 'none'}."
        return mission

    async def plan_mission(self, mission: Mission) -> Mission:
        text = self.mission_text(mission)
        plans = [
            (
                "Rollout or configuration regression",
                "The event mentions rollout/export/job failures or a sudden shift, so a recent change may be the root cause.",
                ["rollout", "export", "job", "failure", "config", "schema"],
            ),
            (
                "Customer-impacting operational incident",
                "The signal may affect a customer or SLA and requires an action packet even if technical root cause is still uncertain.",
                ["customer", "enterprise", "urgent", "sla"],
            ),
            (
                "Service health or dependency degradation",
                "The symptoms include errors, latency, or status changes that may originate from service health or dependency degradation.",
                ["error", "latency", "spike", "status", "dependency"],
            ),
        ]
        hypotheses: list[Hypothesis] = []
        for title, rationale, keywords in plans:
            if any(keyword in text for keyword in keywords):
                hypotheses.append(Hypothesis(title=title, rationale=rationale))

        if not hypotheses:
            hypotheses.append(
                Hypothesis(
                    title="Unclassified operational signal",
                    rationale="The runtime needs a broad investigation because the signal did not match a specialized pattern.",
                    confidence=0.25,
                )
            )
        mission.hypotheses = hypotheses
        return mission

    async def investigate(self, mission: Mission) -> Mission:
        searchers = self.registry.by_capability(Capability.SEARCH)
        if not searchers:
            return mission

        async def investigate_hypothesis(hypothesis: Hypothesis) -> list[Evidence]:
            query = f"{hypothesis.title} {' '.join(self.entities(mission))} {mission.signals[-1].summary}"
            await asyncio.sleep(0.35)
            batches = await asyncio.gather(*(connector.search(query) for connector in searchers))
            evidence = [item for batch in batches for item in batch]
            hypothesis.evidence_ids.extend(item.id for item in evidence)
            if evidence:
                hypothesis.confidence = min(0.92, max(item.confidence for item in evidence) * 0.82 + 0.14)
            return evidence

        batches = await asyncio.gather(*(investigate_hypothesis(hypothesis) for hypothesis in mission.hypotheses))
        mission.evidence.extend(item for batch in batches for item in batch)
        return mission

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        if not mission.hypotheses:
            mission.confidence = 0.0
            return mission, True

        signal_bonus = min(0.16, len(mission.signals) * 0.06)
        evidence_bonus = min(0.18, len([ev for ev in mission.evidence if ev.confidence >= 0.65]) * 0.04)
        best = max((hypothesis.confidence for hypothesis in mission.hypotheses), default=0)
        mission.confidence = round(min(0.96, best + signal_bonus + evidence_bonus), 2)

        needs_replan = mission.confidence < 0.72 and mission.replans < 1
        return mission, needs_replan

    async def replan(self, mission: Mission, reason: str | None = None) -> Mission:
        mission.replans += 1
        entities = ", ".join(self.entities(mission)) or "affected system"
        mission.hypotheses.append(
            Hypothesis(
                title="Follow-up evidence gap investigation",
                rationale=reason or f"Confidence remained below threshold; spawn scoped search for independent evidence around {entities}.",
                confidence=0.3,
            )
        )
        return mission

    async def synthesize(self, mission: Mission) -> str:
        top_hypotheses = sorted(mission.hypotheses, key=lambda item: item.confidence, reverse=True)
        top = top_hypotheses[0] if top_hypotheses else None
        evidence_lines = "\n".join(
            f"- {ev.title} ({ev.source}, confidence {ev.confidence:.2f}): {ev.summary}"
            for ev in mission.evidence[:8]
        ) or "- No evidence gathered."
        signals = "\n".join(f"- [{sig.source}/{sig.type}] {sig.summary}" for sig in mission.signals)
        recommendation = "Create an owner-visible action packet and continue monitoring."
        if top and "regression" in top.title.lower() and mission.confidence >= 0.72:
            recommendation = "Open a remediation task to inspect the recent rollout/configuration change and prepare rollback or flag-disable steps."
        elif mission.severity == "high":
            recommendation = "Escalate to the owning team with customer-safe status copy and evidence-backed next actions."

        return f"""# AUTOPILOT Mission Brief

Generated: {datetime.now(timezone.utc).isoformat()}
Mission: {mission.title}
Severity: {mission.severity}
Confidence: {mission.confidence:.2f}
Replans: {mission.replans}

## Signals
{signals}

## Leading Hypothesis
{top.title if top else "None"}

{top.rationale if top else "No hypothesis selected."}

## Evidence
{evidence_lines}

## Operational Recommendation
{recommendation}

## Bounded Actions
- Write durable mission report.
- Notify the operations channel or local fallback connector.
- Persist memory note for future correlation.
"""

    async def publish_actions(self, mission: Mission, brief: str) -> list[ActionResult]:
        actions: list[ActionResult] = []
        writers = self.registry.by_capability(Capability.WRITE)
        notifiers = self.registry.by_capability(Capability.NOTIFY)

        if writers:
            actions.append(
                await writers[0].write(
                    f"mission-{mission.id}",
                    brief,
                    {"mission_id": mission.id, "confidence": mission.confidence},
                )
            )

        if notifiers:
            text = f"AUTOPILOT completed {mission.title} | severity={mission.severity} | confidence={mission.confidence:.2f}"
            actions.append(await notifiers[0].action("notify_ops", {"mission_id": mission.id, "text": text}))

        return actions

    def entities(self, mission: Mission) -> list[str]:
        seen: list[str] = []
        for signal in mission.signals:
            for entity in signal.entities:
                if entity not in seen:
                    seen.append(entity)
        return seen

    def mission_text(self, mission: Mission) -> str:
        return " ".join([mission.title, mission.summary, *[signal.summary for signal in mission.signals], *self.entities(mission)]).lower()
