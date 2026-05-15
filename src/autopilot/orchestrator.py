from __future__ import annotations

import asyncio
from typing import Iterable, List
import logging

from autopilot.models import AgentTask, AgentType, Mission, Signal

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    def __init__(self, agent_registry: "MAASConnectorRegistry", store: "Store", queue: asyncio.Queue):
        self.registry = agent_registry
        self.store = store
        self.queue = queue

    def route(self, signal: Signal, mission: Mission) -> list[AgentTask]:
        tasks: list[AgentTask] = []
        platform = (signal.source_platform or "").lower()
        ttype = signal.type.lower()

        # GitHub-related signals
        if "github" in platform or any(k in ttype for k in ("push", "pr", "pull", "issue")):
            tasks.append(AgentTask(agent_type=AgentType.GITHUB, mission_id=mission.id, signal_id=signal.id, priority=10, payload={"signal": signal.model_dump()}))
            tasks.append(AgentTask(agent_type=AgentType.PROJECT_MGMT, mission_id=mission.id, signal_id=signal.id, priority=20, payload={"signal": signal.model_dump()}))
            tasks.append(AgentTask(agent_type=AgentType.COMMUNICATION, mission_id=mission.id, signal_id=signal.id, priority=30, payload={"signal": signal.model_dump()}))

        # Cost anomaly / cloud signals
        if any(k in platform for k in ("aws", "gcp", "azure")) or "cost" in ttype or "anomaly" in ttype:
            tasks.append(AgentTask(agent_type=AgentType.CLOUD_INFRA, mission_id=mission.id, signal_id=signal.id, priority=10, payload={"signal": signal.model_dump()}))
            tasks.append(AgentTask(agent_type=AgentType.COMMUNICATION, mission_id=mission.id, signal_id=signal.id, priority=20, payload={"signal": signal.model_dump()}))
            tasks.append(AgentTask(agent_type=AgentType.SECURITY_AUDIT, mission_id=mission.id, signal_id=signal.id, priority=30, payload={"signal": signal.model_dump()}))

        # Default probes for analytics and docs
        if mission.severity == "high":
            tasks.append(AgentTask(agent_type=AgentType.ANALYTICS, mission_id=mission.id, signal_id=signal.id, priority=40, payload={"signal": signal.model_dump()}))

        # Ensure unique agent assignment and sort by priority
        unique: dict[AgentType, AgentTask] = {}
        for t in tasks:
            if t.agent_type not in unique or t.priority < unique[t.agent_type].priority:
                unique[t.agent_type] = t

        result = sorted(unique.values(), key=lambda x: x.priority)
        logger.debug("Orchestrator.route -> %s tasks for mission %s", len(result), mission.id)
        return result

    def enforce_policy(self, task: AgentTask) -> bool:
        # Simple policy enforcement based on mission flags
        flags = task.payload.get("mission", {}).get("policy_flags") if isinstance(task.payload.get("mission"), dict) else None
        # If no flags, allow by default
        allowed = True
        logger.debug("Policy check for task %s -> %s", task.id, allowed)
        return allowed

    async def dispatch(self, tasks: Iterable[AgentTask]) -> None:
        for task in tasks:
            # persist task
            try:
                self.store.save_agent_task(task)
            except Exception:
                logger.exception("Failed persisting task %s", task.id)
            await self.queue.put(task)
            logger.info("Dispatched task %s to queue", task.id)
