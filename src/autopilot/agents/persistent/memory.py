"""Memory — Persistent Agent #4.

Cross-mission memory: stores resolution patterns, source reliability scores,
and reusable context. Called by the Orchestrator to enrich new missions
with prior learnings.

Storage schema uses compound keys so recall is incident-type-aware:
  key = "resolution:{signal_type}"    → for type-matched recall
  key = "resolution:all"              → for cross-type fallback

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

    def _incident_type_key(self, mission: Mission) -> str:
        """Derive a storage key from the dominant signal type."""
        types = [s.type for s in mission.signals if s.type]
        if not types:
            return "resolution:unknown"
        # Normalise: use the first signal type, strip sub-categories after "."
        dominant = types[0].split(".")[0].lower().strip()
        return f"resolution:{dominant}"

    async def recall_for(self, mission: Mission) -> dict[str, Any]:
        """Surface relevant prior resolutions for the current mission.

        Recall strategy:
        1. Type-keyed memories for the same incident type (most relevant)
        2. Cross-type fallback from 'resolution:all'
        3. LLM synthesis of the combined set
        """
        type_key = self._incident_type_key(mission)

        # Try type-specific memories first, fall back to cross-type
        prior: list[str] = self._store.recall(type_key, limit=6)
        if len(prior) < 3:
            cross_type = self._store.recall("resolution:all", limit=5)
            # Deduplicate while preserving order
            seen = set(prior)
            for item in cross_type:
                if item not in seen:
                    seen.add(item)
                    prior.append(item)
            prior = prior[:8]

        if not prior:
            return {"relevant_patterns": [], "known_fixes": [], "confidence_boost": 0.0, "notes": "No prior missions."}

        task = (
            f"Current mission: {mission.title}\n"
            f"Severity: {mission.severity}\n"
            f"Type: {type_key}\n"
            f"Entities: {', '.join(e for s in mission.signals for e in s.entities)}\n\n"
            f"Prior mission resolutions (type-matched first, then cross-type):\n"
            + "\n".join(f"  - {p}" for p in prior)
            + "\n\nExtract relevant patterns for the current mission. "
            + "Be specific — only include patterns that are actually relevant to this incident type."
        )

        agent = SubAgent(role=self.role, tools=[], max_steps=2, system_prompt=_SYSTEM)
        result: AgentResult = await agent.run(task)
        r = result.answer

        if isinstance(r, dict) and "relevant_patterns" in r:
            log.info("Memory: found %d relevant patterns for mission '%s' (type=%s)",
                     len(r.get("relevant_patterns", [])), mission.title, type_key)
            return r

        return {"relevant_patterns": [], "known_fixes": [], "confidence_boost": 0.0, "notes": "Memory unavailable."}

    def remember(self, mission: Mission, outcome: str) -> None:
        """Store a mission resolution under both a type-specific and a global key."""
        pattern = (
            f"{mission.title} | "
            f"severity={mission.severity} | "
            f"confidence={mission.confidence:.2f} | "
            f"evidence={len(mission.evidence)} | "
            f"replans={mission.replans} | "
            f"outcome={outcome}"
        )
        type_key = self._incident_type_key(mission)
        # Store under type-specific key for future type-matched recall
        self._store.remember(type_key, pattern)
        # Also store under global key for cross-type fallback
        self._store.remember("resolution:all", pattern)
        log.info("Memory: stored resolution for '%s' (key=%s)", mission.title, type_key)
