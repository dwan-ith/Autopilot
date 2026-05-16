"""ReflectionAgent — Mission Subagent (post-verification synthesis).

After the Verifier scores confidence, the ReflectionAgent performs a
cross-branch synthesis pass. It reads all hypothesis findings and evidence,
then consolidates them into a single coherent root-cause conclusion with an
adjusted confidence score.

Uses the same LLM slot as the Planner (high quality).
"""

from __future__ import annotations

import logging

from autopilot.agents.base import AgentResult, SubAgent, Tool
from autopilot.models import Mission

log = logging.getLogger("autopilot.agents.reflection")

_SYSTEM = """\
You are the Reflection sub-agent in AUTOPILOT. After parallel investigation
branches have gathered evidence, your job is to synthesise cross-branch findings
into a single coherent root-cause conclusion.

Steps:
1. Review all hypothesis confidence scores and evidence summaries.
2. Identify the leading root cause and any secondary contributing factors.
3. Optionally call knowledge_search once to validate or enrich the conclusion.
4. Return a consolidated finding.

Return:
{"action": "answer", "result": {
  "root_cause": "concise root-cause statement",
  "contributing_factors": ["factor1", "factor2"],
  "adjusted_confidence": 0.75,
  "recommended_next_step": "brief recommended action for the Executor"
}}

Be concise. Do not repeat the same hypothesis twice. If evidence is ambiguous,
say so explicitly and lower the confidence accordingly.
"""


class ReflectionAgent:
    """Mission subagent that performs cross-branch synthesis after verification."""

    role = "reflection"

    def __init__(self, tools: list[Tool] | None = None):
        self._tools = tools or []

    async def reflect(self, mission: Mission) -> Mission:
        """Synthesise all branch findings into a consolidated root-cause conclusion."""
        if not mission.hypotheses:
            log.info("ReflectionAgent: no hypotheses — skipping reflection.")
            return mission

        hyp_lines = "\n".join(
            f"  [{h.confidence:.2f}] {h.title}: {h.rationale}"
            for h in sorted(mission.hypotheses, key=lambda h: h.confidence, reverse=True)
        )
        evidence_lines = "\n".join(
            f"  - [{e.source}] {e.title}: {e.summary[:120]}"
            for e in mission.evidence[:8]
        )

        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Current confidence: {mission.confidence:.2f}\n\n"
            f"Hypothesis confidence scores:\n{hyp_lines}\n\n"
            f"Top evidence items:\n{evidence_lines or '  (none gathered yet)'}\n\n"
            f"Synthesise the cross-branch findings into a single root-cause conclusion "
            f"and an adjusted overall confidence. You may call knowledge_search once if helpful."
        )

        agent = SubAgent(role=self.role, tools=self._tools, max_steps=4, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        r = result.answer

        if isinstance(r, dict):
            # Merge root-cause finding back into the mission summary
            root_cause = str(r.get("root_cause", "")).strip()
            factors = r.get("contributing_factors", [])
            adj_conf = r.get("adjusted_confidence")
            next_step = str(r.get("recommended_next_step", "")).strip()

            if root_cause:
                factors_str = ""
                if isinstance(factors, list) and factors:
                    factors_str = " | Contributing: " + ", ".join(str(f) for f in factors[:3])
                mission.summary = (
                    f"Root cause: {root_cause}{factors_str}"
                    + (f" | Next: {next_step}" if next_step else "")
                )

            if isinstance(adj_conf, (int, float)) and 0.0 <= adj_conf <= 1.0:
                # Weighted blend: 60% prior, 40% reflection.
                # Using a blend (not max) so reflection can LOWER confidence
                # when it finds contradictions — avoiding a one-way ratchet.
                blended = 0.6 * mission.confidence + 0.4 * float(adj_conf)
                mission.confidence = round(min(0.96, max(0.1, blended)), 2)

            log.info(
                "ReflectionAgent: root_cause=%.80s confidence=%.2f",
                root_cause or "(none)",
                mission.confidence,
            )
        else:
            log.info("ReflectionAgent: LLM returned no structured result — keeping current state.")

        return mission
