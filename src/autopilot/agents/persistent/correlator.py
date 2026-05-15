"""Correlator — Persistent Agent #1.

Merges related events, extracts entities, determines incident boundaries,
and decides whether a new signal belongs to an existing mission or requires
a new one.

Uses the Groq-1 slot (fast) since correlation must happen before the mission
is even created — latency matters here more than reasoning depth.
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, PersistentAgent, SubAgent, Tool
from autopilot.models import Mission, Signal

log = logging.getLogger("autopilot.agents.correlator")

_SYSTEM = """\
You are the Correlator in AUTOPILOT. You analyze incoming signals and determine:
1. Whether the signal belongs to an existing active mission (based on shared entities)
2. What the true severity is
3. What the key entities and impact scope are
4. A concise impact summary

Active missions (if any) are provided in the task context. Match by shared entities
to decide whether the new signal correlates to an existing mission.

Return your final answer as:
{"action": "answer", "result": {
  "severity": "critical|high|medium|low",
  "impact_summary": "concise one-sentence impact statement",
  "key_entities": ["entity1", "entity2"],
  "correlated_mission_id": "mission_id_or_null",
  "investigation_focus": "where to focus first"
}}

Be direct. Do not hedge. If confidence is low, say severity=medium.
"""


class CorrelatorAgent(PersistentAgent):
    """Persistent agent that classifies and correlates incoming signals."""

    role = "correlator"
    default_max_steps = 3

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def correlate(
        self,
        signal: Signal,
        active_missions: list[Mission],
    ) -> dict[str, Any]:
        """Classify a signal and check if it belongs to an existing mission."""

        missions_ctx = ""
        if active_missions:
            lines = []
            for m in active_missions[:5]:
                entities = [e for s in m.signals for e in s.entities][:6]
                lines.append(f"  - {m.id}: '{m.title}' entities={entities}")
            missions_ctx = "Active missions:\n" + "\n".join(lines)
        else:
            missions_ctx = "No active missions currently."

        task = (
            f"Signal received:\n"
            f"  source={signal.source}\n"
            f"  type={signal.type}\n"
            f"  urgency={signal.urgency}\n"
            f"  summary={signal.summary}\n"
            f"  entities={signal.entities}\n\n"
            f"{missions_ctx}\n\n"
            f"Classify this signal and determine if it correlates to an active mission "
            f"(shared entities = same incident). If so, set correlated_mission_id to that mission's id."
        )

        agent = SubAgent(
            role=self.role,
            tools=[],
            max_steps=self.default_max_steps,
            system_prompt=self._system_prompt(),
        )
        result: AgentResult = await agent.run(task)

        r = result.answer
        if isinstance(r, dict) and "severity" in r:
            return r

        # Heuristic fallback
        signal_entities = {e.lower() for e in signal.entities}
        correlated_id = None
        for m in active_missions:
            mission_entities = {e.lower() for s in m.signals for e in s.entities}
            if signal_entities & mission_entities:
                correlated_id = m.id
                break

        high_urgency = signal.urgency.lower() in {"urgent", "high", "critical", "p0", "p1"}
        return {
            "severity": "high" if high_urgency else "medium",
            "impact_summary": signal.summary,
            "key_entities": signal.entities,
            "correlated_mission_id": correlated_id,
            "investigation_focus": f"Investigate {', '.join(signal.entities[:2]) or signal.type}",
        }
