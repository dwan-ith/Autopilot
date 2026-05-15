from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType
from autopilot.storage import ARTIFACT_DIR

logger = logging.getLogger(__name__)


class SecurityAuditAgent(BaseAgent):
    agent_type = AgentType.SECURITY_AUDIT

    async def handle(self, task: AgentTask) -> ActionRecord:
        logger.info("SecurityAuditAgent handling task %s", task.id)
        # Stubbed scan: write findings to artifacts
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        result = {"status": "stubbed", "findings": []}
        return ActionRecord(agent_type=self.agent_type, platform="security", action_type="handle_task", payload=task.payload, result=result, created_at=datetime.now(timezone.utc))

    async def health_check(self) -> bool:
        return True
