"""Planner — Mission Subagent #1.

Breaks the mission into competing investigation branches (hypotheses).
Given a correlated incident context, the Planner generates 2–4 meaningfully
different hypotheses, each with a search_focus that the Investigator can act on.

Uses the OpenRouter-2 slot (high quality, separate from Investigator).
Has access to the knowledge search tool so it can consult runbooks before planning.
"""

from __future__ import annotations

import logging

from autopilot.agents.base import AgentResult, SubAgent, Tool
from autopilot.agents.validation import safe_confidence
from autopilot.models import Hypothesis, Mission

log = logging.getLogger("autopilot.agents.planner")

_SYSTEM = """\
You are the Planner sub-agent in AUTOPILOT. Your job is to generate 2–4 competing
investigation hypotheses for the current mission.

You MAY use the knowledge_search tool to check runbooks before deciding on hypotheses.
Make 1–2 targeted searches, then return your hypotheses.

Each hypothesis should be meaningfully different — don't generate variants of the same idea.
Include a search_focus field so the Investigator knows what to look for.

Return:
{"action": "answer", "result": {"hypotheses": [
  {
    "title": "short descriptive title",
    "rationale": "why this might be the root cause",
    "initial_confidence": 0.35,
    "search_focus": "what the Investigator should search for"
  }
]}}

Generate 2–4 hypotheses. Never fixate on a single cause. Cover different dimensions:
infrastructure, code change, dependency, configuration, user impact.
"""


class PlannerAgent:
    """Mission subagent that generates investigation branches."""

    role = "planner"

    def __init__(self, tools: list[Tool] | None = None):
        self._tools = tools or []

    async def plan(self, mission: Mission) -> Mission:
        """Generate hypotheses for the mission, optionally consulting runbooks."""
        signal_lines = "\n".join(
            f"  [{s.source}/{s.type}] urgency={s.urgency}: {s.summary}"
            for s in mission.signals
        )
        entities = list({e for s in mission.signals for e in s.entities})

        task = (
            f"Mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Assessment: {mission.summary}\n"
            f"Key entities: {', '.join(entities)}\n\n"
            f"Signals received:\n{signal_lines}\n\n"
            f"Use knowledge_search if you want to check relevant runbooks first. "
            f"Then generate 2–4 competing hypotheses."
        )

        agent = SubAgent(role=self.role, tools=self._tools, max_steps=5, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)

        r = result.answer
        hypotheses_data = r.get("hypotheses") if isinstance(r, dict) else None

        if isinstance(hypotheses_data, list) and hypotheses_data:
            mission.hypotheses = [
                Hypothesis(
                    title=str(item.get("title", "Unnamed")),
                    rationale=str(item.get("rationale", "")),
                    confidence=safe_confidence(item.get("initial_confidence"), default=0.35),
                    search_focus=str(item.get("search_focus", item.get("title", ""))),
                )
                for item in hypotheses_data
                if isinstance(item, dict)
            ]
        else:
            mission.hypotheses = self._heuristic_hypotheses(mission)

        log.info("Planner: generated %d hypotheses for '%s'", len(mission.hypotheses), mission.title)
        return mission

    def _heuristic_hypotheses(self, mission: Mission) -> list[Hypothesis]:
        text = " ".join([mission.title, mission.summary, *[s.summary for s in mission.signals]]).lower()
        candidates = [
            ("Rollout or configuration regression",
             "A recent deployment or flag change may have introduced a regression.",
             "recent rollout deployment configuration regression job failure",
             ["rollout", "deploy", "config", "flag", "job", "export", "failure"]),
            ("Customer-impacting operational incident",
             "Active user impact requiring SLA-level response.",
             "customer enterprise SLA escalation user impact support",
             ["customer", "enterprise", "urgent", "sla", "impact"]),
            ("Service dependency degradation",
             "An upstream dependency (database, queue, third-party) is degraded.",
             "error spike latency dependency timeout service degradation",
             ["error", "latency", "spike", "dependency", "timeout", "503", "500"]),
            ("Data pipeline or async job failure",
             "A background job or data pipeline has failed silently.",
             "data pipeline async job batch queue failure export",
             ["pipeline", "export", "job", "batch", "async", "queue"]),
        ]
        hyps = [
            Hypothesis(title=t, rationale=r, search_focus=sf)
            for t, r, sf, kw in candidates
            if any(k in text for k in kw)
        ]
        return hyps or [Hypothesis(
            title="Unclassified operational signal",
            rationale="No pattern matched. Broad investigation required.",
            confidence=0.25,
            search_focus=mission.title,
        )]
