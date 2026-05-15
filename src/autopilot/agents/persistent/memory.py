"""Memory — Persistent Agent #4.

Cross-mission memory: stores resolution patterns, source reliability scores,
and reusable context. Called by the Orchestrator to enrich new missions
with prior learnings.

Uses the Groq-3 slot (fast, shared with Governor).
"""

from __future__ import annotations

import logging
from typing import Any

from autopilot.agents.base import AgentResult, PersistentAgent, SubAgent
from autopilot.models import Mission
from autopilot.storage import Store

log = logging.getLogger("autopilot.agents.memory")

_SYSTEM = """\
You are the Memory agent in AUTOPILOT. You surface relevant prior learnings
to help with the current mission.

Given the current mission context and a set of prior mission resolutions,
extract the most relevant patterns, known fixes, and source reliability info.

Return:
{"action": "answer", "result": {
  "relevant_patterns": ["pattern1", "pattern2"],
  "known_fixes": ["fix1"],
  "source_notes": {"github": "reliable for deployment issues"},
  "confidence_boost": 0.05,
  "notes": "brief summary of what memory contributed"
}}

If no relevant prior context exists, return an empty result with confidence_boost=0.
"""


class MemoryAgent(PersistentAgent):
    """Persistent agent that provides cross-mission memory and learning."""

    role = "memory"
    default_max_steps = 2

    def __init__(self, store: Store):
        self._store = store

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def recall_for(self, mission: Mission) -> dict[str, Any]:
        """Surface relevant prior resolutions for the current mission."""
        prior = self._store.recall("mission_resolution", limit=8)
        if not prior:
            return {"relevant_patterns": [], "known_fixes": [], "confidence_boost": 0.0, "notes": "No prior missions."}

        task = (
            f"Current mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Entities: {', '.join(e for s in mission.signals for e in s.entities)}\n\n"
            f"Prior mission resolutions (most recent first):\n"
            + "\n".join(f"  - {p}" for p in prior)
            + "\n\nExtract relevant patterns for the current mission."
        )

        agent = SubAgent(role=self.role, tools=[], max_steps=2, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        r = result.answer

        if isinstance(r, dict) and "relevant_patterns" in r:
            log.info("Memory: found %d relevant patterns for mission '%s'",
                     len(r.get("relevant_patterns", [])), mission.title)
            return r

        return {"relevant_patterns": [], "known_fixes": [], "confidence_boost": 0.0, "notes": "Memory unavailable."}

    def remember(self, mission: Mission, outcome: str) -> None:
        """Store a mission resolution for future recall."""
        pattern = (
            f"{mission.title} | "
            f"severity={mission.severity} | "
            f"confidence={mission.confidence:.2f} | "
            f"evidence={len(mission.evidence)} | "
            f"replans={mission.replans} | "
            f"outcome={outcome}"
        )
        self._store.remember("mission_resolution", pattern)
        log.info("Memory: stored resolution for '%s'", mission.title)
