"""Verifier — Persistent Agent #2.

Critically evaluates the quality of gathered evidence, scores confidence,
detects contradictions, and decides whether the mission has enough basis
to act or needs another investigation round.

Uses the OpenRouter-3 slot (high quality) because this is the primary gate
between investigation and action.
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, PersistentAgent, SubAgent
from autopilot.models import Evidence, Hypothesis, Mission

log = logging.getLogger("autopilot.agents.verifier")

_SYSTEM = """\
You are the Verifier in AUTOPILOT. You critically evaluate the quality of
gathered evidence and decide whether the mission is ready to act.

Your job:
1. Check evidence quality and source diversity
2. Score overall confidence (0.0–1.0) based on what was actually found
3. Identify the leading hypothesis
4. Decide whether to replan (if evidence is weak or contradictory)
5. Flag any evidence gaps

Be honest. A confidence of 0.95 means you are very sure. Weak or thin evidence
should produce 0.4–0.6, not 0.85+.

Return:
{"action": "answer", "result": {
  "analysis": "critical assessment of evidence quality",
  "confidence": 0.78,
  "leading_hypothesis": "hypothesis title",
  "gaps": ["gap1", "gap2"],
  "needs_replan": false,
  "replan_reason": "why or why not"
}}
"""


class VerifierAgent(PersistentAgent):
    """Persistent agent that scores evidence quality and confidence."""

    role = "verifier"
    default_max_steps = 3

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def verify(self, mission: Mission) -> tuple[Mission, bool]:
        """Score evidence quality and decide if replanning is needed."""

        hyp_lines = "\n".join(
            f"  - [{h.confidence:.2f}] {h.title}: {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        )
        ev_lines = "\n".join(
            f"  - [{e.source}][{e.confidence:.2f}] {e.title}: {e.summary[:150]}"
            for e in mission.evidence[:12]
        ) or "  - No evidence gathered."

        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Replans so far: {mission.replans}\n\n"
            f"Hypotheses:\n{hyp_lines}\n\n"
            f"Evidence ({len(mission.evidence)} items):\n{ev_lines}\n\n"
            f"Critically evaluate the evidence and score confidence. "
            f"Be conservative — only score high if evidence is strong and diverse."
        )

        agent = SubAgent(role=self.role, tools=[], max_steps=3, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        r = result.answer

        if isinstance(r, dict) and "confidence" in r:
            mission.confidence = round(min(0.96, float(r.get("confidence", 0.5))), 2)
            needs_replan = bool(r.get("needs_replan", False)) and mission.replans < 2
            log.info("Verifier: confidence=%.2f needs_replan=%s", mission.confidence, needs_replan)
            return mission, needs_replan

        # Heuristic fallback
        return mission, self._heuristic_verify(mission)

    def _heuristic_verify(self, mission: Mission) -> bool:
        strong = sum(1 for e in mission.evidence if e.confidence >= 0.65)
        source_diversity = len({e.source for e in mission.evidence})
        best_conf = max((h.confidence for h in mission.hypotheses), default=0)
        mission.confidence = round(
            min(0.96, best_conf * 0.6 + strong * 0.05 + source_diversity * 0.04), 2
        )
        return mission.confidence < 0.65 and mission.replans < 2
