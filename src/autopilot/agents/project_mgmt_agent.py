from __future__ import annotations

import logging
from autopilot.config import settings
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType

logger = logging.getLogger(__name__)


class ProjectMgmtAgent(BaseAgent):
    agent_type = AgentType.PROJECT_MGMT

    async def handle(self, task: AgentTask) -> ActionRecord:
        jira = settings.JIRA_URL
        logger.info("ProjectMgmtAgent handling task %s (jira=%s)", task.id, bool(jira))
        result = {
            "status": "stubbed",
            "note": "would create/update ticket in Jira/Linear",
        }
        return ActionRecord(
            agent_type=self.agent_type,
            platform="project_mgmt",
            action_type="handle_task",
            payload=task.payload,
            result=result,
            created_at=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        return bool(settings.JIRA_TOKEN or settings.LINEAR_TOKEN)
