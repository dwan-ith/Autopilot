from __future__ import annotations

import logging
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType
from autopilot.storage import Store

logger = logging.getLogger(__name__)


class AnalyticsAgent(BaseAgent):
    agent_type = AgentType.ANALYTICS

    def __init__(self, store: Store):
        self.store = store

    async def handle(self, task: AgentTask) -> ActionRecord:
        logger.info("AnalyticsAgent handling task %s", task.id)
        # Aggregate simple KPIs from store (stub)
        traces = self.store.list_traces(task.mission_id, limit=10)
        result = {"status": "stubbed", "traces_sampled": len(traces)}
        return ActionRecord(agent_type=self.agent_type, platform="analytics", action_type="handle_task", payload=task.payload, result=result, created_at=datetime.now(timezone.utc))

    async def health_check(self) -> bool:
        return True
