"""AUTOPILOT Operator Suite — LLM-powered multi-agent operators.

Each operator is a distinct cognitive agent with its own system prompt,
reasoning pattern, and output schema.  When no LLM provider is configured
the operators degrade to deterministic heuristics so the system remains
demo-safe without credentials.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import ActionResult, Capability, Evidence, Hypothesis, Mission, Signal
from autopilot.operators.llm import parse_json, reason

log = logging.getLogger("autopilot.operators")

HIGH_URGENCY = {"urgent", "high", "critical", "p0", "p1"}

# ═══════════════════════════════════════════════════════════════════════════
#  System prompts — each defines a distinct agent persona
# ═══════════════════════════════════════════════════════════════════════════

SIGNAL_EVALUATOR_PROMPT = """\
You are the **Signal Evaluator** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: triage incoming operational signals and produce a
structured severity assessment.

REASONING PROCESS:
1. Read every signal carefully.
2. Identify affected systems, customers, and teams.
3. Assess blast radius — who is hurt and how badly?
4. Score urgency on a 1-10 scale.
5. Recommend where investigation should focus.

Respond with a single JSON object:
{
  "severity": "critical" | "high" | "medium" | "low",
  "impact_summary": "one-paragraph impact assessment",
  "key_entities": ["entity1", "entity2"],
  "urgency_score": 8,
  "investigation_focus": "short sentence on where to look first"
}
"""

MISSION_PLANNER_PROMPT = """\
You are the **Mission Planner** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: given operational signals, decompose the incident into
competing hypotheses that must be investigated in parallel.

REASONING PROCESS:
1. Consider multiple distinct root causes — do NOT fixate on one.
2. For each hypothesis, explain WHY it could be the cause.
3. Rank by initial plausibility (0.0-1.0).
4. State what evidence would CONFIRM or REFUTE each.

Respond with a single JSON object:
{
  "hypotheses": [
    {
      "title": "short descriptive title",
      "rationale": "2-3 sentence reasoning about why this could be the cause",
      "initial_confidence": 0.45,
      "confirming_evidence": ["what would prove this right"],
      "refuting_evidence": ["what would prove this wrong"]
    }
  ]
}

Generate 2-4 hypotheses.  They MUST be meaningfully different from each other.
"""

INVESTIGATOR_PROMPT = """\
You are the **Investigator** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: given a hypothesis about an operational incident,
generate targeted search queries to find confirming or refuting evidence.

REASONING PROCESS:
1. Understand the hypothesis and its required evidence.
2. Generate 2-3 specific, targeted search queries.
3. Queries should seek BOTH confirming and refuting evidence.
4. Include technical terms that would appear in runbooks, docs, or alerts.

Respond with a single JSON object:
{
  "queries": [
    "specific search query 1",
    "specific search query 2"
  ],
  "reasoning": "brief explanation of search strategy"
}
"""

VERIFIER_PROMPT = """\
You are the **Verification Agent** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: critically evaluate all collected evidence against the
hypotheses and determine whether the investigation has sufficient basis for
action.

REASONING PROCESS:
1. For each hypothesis, assess the quality of supporting evidence.
2. Check for confirmation bias — are we ignoring contradictory evidence?
3. Identify gaps — what SHOULD we have found but didn't?
4. Score overall confidence (0.0-1.0).
5. Decide whether more investigation is needed.

Respond with a single JSON object:
{
  "analysis": "3-5 sentence critical assessment of the evidence",
  "confidence": 0.78,
  "leading_hypothesis": "title of the most supported hypothesis",
  "gaps": ["gap 1", "gap 2"],
  "needs_replan": true | false,
  "replan_reason": "why more investigation is needed (only if needs_replan is true)"
}

Be HONEST.  If evidence is weak, say so.  A confident wrong answer is worse
than an honest low-confidence assessment.
"""

REPLANNER_PROMPT = """\
You are the **Adaptive Replanner** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: when the verification gate identifies evidence gaps,
generate new hypotheses or investigation angles to fill those gaps.

REASONING PROCESS:
1. Review what was already investigated and what gaps remain.
2. Consider angles that were NOT covered in the initial investigation.
3. Generate 1-2 new hypotheses targeting the specific gaps.

Respond with a single JSON object:
{
  "new_hypotheses": [
    {
      "title": "descriptive title",
      "rationale": "why this new angle could resolve the gaps",
      "search_focus": "what specifically to look for"
    }
  ],
  "strategy": "brief explanation of replanning strategy"
}
"""

SYNTHESIZER_PROMPT = """\
You are the **Synthesis Operator** in the AUTOPILOT Autonomous Operator Runtime.

Your cognitive role: produce a clear, actionable operational brief from the
full investigation.  This brief will be read by on-call engineers and
operations managers.

STRUCTURE YOUR RESPONSE AS MARKDOWN with these sections:
# AUTOPILOT Mission Brief

## Incident Summary
(2-3 sentences: what happened, who is affected, how severe)

## Root Cause Assessment
(Leading hypothesis with confidence level and reasoning)

## Evidence Summary
(Bullet list of key evidence items with source and confidence)

## Operational Recommendation
(Concrete next steps — be specific about what to do, not vague platitudes)

## Risk Assessment
(What could go wrong if we act on this recommendation)

Write for an engineer who needs to act NOW.  Be direct.  No fluff.
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Operator Suite
# ═══════════════════════════════════════════════════════════════════════════

class OperatorSuite:
    """Coordinates LLM-powered operators across a mission lifecycle.

    Each public method represents a distinct agent invocation.  The agents
    share state through the Mission object but reason independently with
    their own system prompts.
    """

    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry

    # ------------------------------------------------------------------
    #  Agent 1: Signal Evaluator
    # ------------------------------------------------------------------

    async def evaluate_signal(self, mission: Mission) -> Mission:
        """Triage agent — assesses severity and impact of incoming signals."""
        signal_text = "\n".join(
            f"[{s.source}/{s.type}] urgency={s.urgency}\n  {s.summary}\n  entities: {', '.join(s.entities)}"
            for s in mission.signals
        )
        prompt = f"Evaluate these {len(mission.signals)} operational signal(s):\n\n{signal_text}"

        raw = await reason(SIGNAL_EVALUATOR_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if result and isinstance(result, dict):
            mission.severity = result.get("severity", mission.severity)
            entities_from_llm = result.get("key_entities", [])
            impact = result.get("impact_summary", "")
            focus = result.get("investigation_focus", "")
            mission.summary = f"{impact} Investigation focus: {focus}"
            # Merge LLM-extracted entities into signals
            for sig in mission.signals:
                for ent in entities_from_llm:
                    if ent not in sig.entities:
                        sig.entities.append(ent)
            log.info("Signal evaluation (LLM): severity=%s", mission.severity)
        else:
            # Heuristic fallback
            summaries = " ".join(s.summary.lower() for s in mission.signals)
            urgency_max = max((2 if s.urgency.lower() in HIGH_URGENCY else 1 for s in mission.signals), default=1)
            impact_words = sum(w in summaries for w in ["customer", "enterprise", "failure", "error", "spike", "urgent", "sla"])
            mission.severity = "high" if urgency_max == 2 or impact_words >= 3 else "medium"
            mission.summary = (
                f"{len(mission.signals)} signal(s) normalized; severity={mission.severity}; "
                f"entities={', '.join(self.entities(mission)) or 'none'}."
            )
            log.info("Signal evaluation (heuristic): severity=%s", mission.severity)

        return mission

    # ------------------------------------------------------------------
    #  Agent 2: Mission Planner
    # ------------------------------------------------------------------

    async def plan_mission(self, mission: Mission) -> Mission:
        """Planner agent — decomposes the incident into competing hypotheses."""
        signal_text = "\n".join(
            f"- [{s.source}/{s.type}] {s.summary} (entities: {', '.join(s.entities)})"
            for s in mission.signals
        )
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Assessment: {mission.summary}\n\n"
            f"Signals:\n{signal_text}\n\n"
            f"Generate hypotheses for what is causing this incident."
        )

        raw = await reason(MISSION_PLANNER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if result and isinstance(result, dict) and "hypotheses" in result:
            mission.hypotheses = []
            for h in result["hypotheses"]:
                mission.hypotheses.append(Hypothesis(
                    title=h.get("title", "Unnamed hypothesis"),
                    rationale=h.get("rationale", ""),
                    confidence=float(h.get("initial_confidence", 0.35)),
                ))
            log.info("Mission planning (LLM): %d hypotheses", len(mission.hypotheses))
        else:
            # Heuristic fallback
            mission.hypotheses = self._heuristic_hypotheses(mission)
            log.info("Mission planning (heuristic): %d hypotheses", len(mission.hypotheses))

        return mission

    # ------------------------------------------------------------------
    #  Agent 3: Investigator (tool-calling loop)
    # ------------------------------------------------------------------

    async def investigate(self, mission: Mission) -> Mission:
        """Investigator agent — generates search queries via LLM, then
        executes them through capability-routed connectors."""
        searchers = self.registry.by_capability(Capability.SEARCH)
        if not searchers:
            return mission

        async def investigate_hypothesis(hyp: Hypothesis) -> list[Evidence]:
            # Step 1: LLM generates targeted search queries
            prompt = (
                f"Hypothesis: {hyp.title}\n"
                f"Rationale: {hyp.rationale}\n"
                f"Mission context: {mission.summary}\n"
                f"Known entities: {', '.join(self.entities(mission))}\n\n"
                f"Generate search queries to investigate this hypothesis."
            )
            raw = await reason(INVESTIGATOR_PROMPT, prompt, json_mode=True)
            result = parse_json(raw)

            if result and isinstance(result, dict) and "queries" in result:
                queries = result["queries"]
                log.info("Investigator (LLM) generated %d queries for '%s'", len(queries), hyp.title)
            else:
                # Heuristic fallback: construct query from hypothesis context
                queries = [f"{hyp.title} {' '.join(self.entities(mission))} {mission.signals[-1].summary}"]
                log.info("Investigator (heuristic) for '%s'", hyp.title)

            # Step 2: Execute queries through all search-capable connectors
            all_evidence: list[Evidence] = []
            for query in queries[:3]:  # Cap at 3 queries per hypothesis
                await asyncio.sleep(0.15)  # Rate-limit courtesy
                batches = await asyncio.gather(*(c.search(query) for c in searchers))
                for batch in batches:
                    all_evidence.extend(batch)

            # Link evidence to hypothesis
            hyp.evidence_ids.extend(ev.id for ev in all_evidence)
            if all_evidence:
                best_conf = max(ev.confidence for ev in all_evidence)
                hyp.confidence = min(0.92, best_conf * 0.82 + 0.14)

            return all_evidence

        # Fan out investigation across all hypotheses concurrently
        batches = await asyncio.gather(*(investigate_hypothesis(h) for h in mission.hypotheses))
        for batch in batches:
            mission.evidence.extend(batch)

        return mission

    # ------------------------------------------------------------------
    #  Agent 4: Verification Gate
    # ------------------------------------------------------------------

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        """Verification agent — critically evaluates evidence and decides
        whether to proceed or replan."""
        hyp_text = "\n".join(
            f"- {h.title} (confidence={h.confidence:.2f}): {h.rationale}"
            for h in mission.hypotheses
        )
        ev_text = "\n".join(
            f"- [{ev.source}] {ev.title} (confidence={ev.confidence:.2f}): {ev.summary[:150]}"
            for ev in mission.evidence[:10]
        )
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Replans so far: {mission.replans}\n\n"
            f"Hypotheses:\n{hyp_text}\n\n"
            f"Evidence collected ({len(mission.evidence)} items):\n{ev_text}\n\n"
            f"Critically assess this evidence and decide if we can proceed to action."
        )

        raw = await reason(VERIFIER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if result and isinstance(result, dict):
            confidence = float(result.get("confidence", 0.5))
            mission.confidence = round(min(0.96, confidence), 2)
            needs_replan = bool(result.get("needs_replan", False)) and mission.replans < 2
            log.info(
                "Verification (LLM): confidence=%.2f needs_replan=%s",
                mission.confidence, needs_replan,
            )
        else:
            # Heuristic fallback
            signal_bonus = min(0.16, len(mission.signals) * 0.06)
            evidence_bonus = min(0.18, len([ev for ev in mission.evidence if ev.confidence >= 0.65]) * 0.04)
            best = max((h.confidence for h in mission.hypotheses), default=0)
            mission.confidence = round(min(0.96, best + signal_bonus + evidence_bonus), 2)
            needs_replan = mission.confidence < 0.72 and mission.replans < 1
            log.info(
                "Verification (heuristic): confidence=%.2f needs_replan=%s",
                mission.confidence, needs_replan,
            )

        return mission, needs_replan

    # ------------------------------------------------------------------
    #  Agent 5: Adaptive Replanner
    # ------------------------------------------------------------------

    async def replan(self, mission: Mission, reason_text: str | None = None) -> Mission:
        """Replanner agent — fills evidence gaps identified by the verifier."""
        mission.replans += 1

        hyp_text = "\n".join(f"- {h.title} (conf={h.confidence:.2f})" for h in mission.hypotheses)
        ev_text = "\n".join(f"- {ev.title}: {ev.summary[:100]}" for ev in mission.evidence[:8])
        prompt = (
            f"Mission: {mission.title}\n"
            f"Current hypotheses:\n{hyp_text}\n\n"
            f"Evidence so far:\n{ev_text}\n\n"
            f"Replan reason: {reason_text or 'Confidence below threshold; evidence gaps detected.'}\n\n"
            f"Generate new investigation angles to fill the gaps."
        )

        raw = await reason(REPLANNER_PROMPT, prompt, json_mode=True)
        result = parse_json(raw)

        if result and isinstance(result, dict) and "new_hypotheses" in result:
            for nh in result["new_hypotheses"]:
                mission.hypotheses.append(Hypothesis(
                    title=nh.get("title", "Follow-up investigation"),
                    rationale=nh.get("rationale", ""),
                    confidence=0.3,
                ))
            log.info("Replanner (LLM): added %d new hypotheses", len(result["new_hypotheses"]))
        else:
            # Heuristic fallback
            entities = ", ".join(self.entities(mission)) or "affected system"
            mission.hypotheses.append(Hypothesis(
                title="Follow-up evidence gap investigation",
                rationale=reason_text or f"Confidence below threshold; scoped search for independent evidence around {entities}.",
                confidence=0.3,
            ))
            log.info("Replanner (heuristic): added 1 fallback hypothesis")

        return mission

    # ------------------------------------------------------------------
    #  Agent 6: Synthesis Operator
    # ------------------------------------------------------------------

    async def synthesize(self, mission: Mission) -> str:
        """Synthesis agent — produces an actionable operational brief."""
        signal_text = "\n".join(f"- [{s.source}/{s.type}] {s.summary}" for s in mission.signals)
        hyp_text = "\n".join(
            f"- {h.title} (confidence={h.confidence:.2f}): {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        )
        ev_text = "\n".join(
            f"- [{ev.source}] {ev.title} (confidence={ev.confidence:.2f}): {ev.summary}"
            for ev in mission.evidence[:10]
        )
        prompt = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Overall confidence: {mission.confidence:.2f}\n"
            f"Replans performed: {mission.replans}\n\n"
            f"Signals:\n{signal_text}\n\n"
            f"Hypotheses:\n{hyp_text}\n\n"
            f"Evidence:\n{ev_text}\n\n"
            f"Write the operational mission brief."
        )

        raw = await reason(SYNTHESIZER_PROMPT, prompt, temperature=0.4)

        if raw:
            log.info("Synthesis (LLM): generated %d-char brief", len(raw))
            return raw

        # Heuristic fallback
        return self._heuristic_brief(mission)

    # ------------------------------------------------------------------
    #  Action Publisher (not LLM-driven — executes bounded actions)
    # ------------------------------------------------------------------

    async def publish_actions(self, mission: Mission, brief: str) -> list[ActionResult]:
        """Execute bounded actions through capability-routed connectors."""
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
            text = (
                f"*AUTOPILOT* completed mission: _{mission.title}_\n"
                f"severity={mission.severity} · confidence={mission.confidence:.2f} · "
                f"replans={mission.replans} · evidence={len(mission.evidence)}"
            )
            actions.append(await notifiers[0].action("notify_ops", {"mission_id": mission.id, "text": text}))

        return actions

    # ------------------------------------------------------------------
    #  Utility methods
    # ------------------------------------------------------------------

    def entities(self, mission: Mission) -> list[str]:
        """Deduplicated entity list across all signals."""
        seen: list[str] = []
        for sig in mission.signals:
            for ent in sig.entities:
                if ent not in seen:
                    seen.append(ent)
        return seen

    def mission_text(self, mission: Mission) -> str:
        return " ".join(
            [mission.title, mission.summary, *[s.summary for s in mission.signals], *self.entities(mission)]
        ).lower()

    # ------------------------------------------------------------------
    #  Heuristic fallbacks (used when no LLM is available)
    # ------------------------------------------------------------------

    def _heuristic_hypotheses(self, mission: Mission) -> list[Hypothesis]:
        text = self.mission_text(mission)
        plans = [
            (
                "Rollout or configuration regression",
                "The event mentions rollout/export/job failures, suggesting a recent change may be the root cause.",
                ["rollout", "export", "job", "failure", "config", "schema"],
            ),
            (
                "Customer-impacting operational incident",
                "The signal may affect a customer or SLA, requiring an action packet even if root cause is uncertain.",
                ["customer", "enterprise", "urgent", "sla"],
            ),
            (
                "Service health or dependency degradation",
                "Symptoms include errors, latency, or status changes from service health or dependency issues.",
                ["error", "latency", "spike", "status", "dependency"],
            ),
        ]
        hypotheses: list[Hypothesis] = []
        for title, rationale, keywords in plans:
            if any(kw in text for kw in keywords):
                hypotheses.append(Hypothesis(title=title, rationale=rationale))
        if not hypotheses:
            hypotheses.append(Hypothesis(
                title="Unclassified operational signal",
                rationale="No specialized pattern matched; broad investigation required.",
                confidence=0.25,
            ))
        return hypotheses

    def _heuristic_brief(self, mission: Mission) -> str:
        top = sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        lead = top[0] if top else None
        evidence_lines = "\n".join(
            f"- {ev.title} ({ev.source}, confidence {ev.confidence:.2f}): {ev.summary}"
            for ev in mission.evidence[:8]
        ) or "- No evidence gathered."
        signals = "\n".join(f"- [{s.source}/{s.type}] {s.summary}" for s in mission.signals)
        recommendation = "Create an owner-visible action packet and continue monitoring."
        if lead and "regression" in lead.title.lower() and mission.confidence >= 0.72:
            recommendation = "Inspect the recent rollout/configuration change and prepare rollback or flag-disable steps."
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
{lead.title if lead else "None"}

{lead.rationale if lead else "No hypothesis selected."}

## Evidence
{evidence_lines}

## Operational Recommendation
{recommendation}

## Bounded Actions
- Write durable mission report.
- Notify the operations channel or local fallback connector.
- Persist memory note for future correlation.
"""
