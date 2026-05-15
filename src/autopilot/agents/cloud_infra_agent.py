from __future__ import annotations

import logging
from autopilot.config import settings
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType

logger = logging.getLogger(__name__)


class CloudInfraAgent(BaseAgent):
    agent_type = AgentType.CLOUD_INFRA

    async def handle(self, task: AgentTask) -> ActionRecord:
        aws = settings.AWS_PROFILE
        gcp = settings.GCP_PROJECT
        logger.info(
            "CloudInfraAgent handling task %s (aws=%s, gcp=%s)",
            task.id,
            bool(aws),
            bool(gcp),
        )
        result = {
            "status": "stubbed",
            "note": "would trigger deployment or query cost APIs",
        }
        return ActionRecord(
            agent_type=self.agent_type,
            platform="cloud",
            action_type="handle_task",
            payload=task.payload,
            result=result,
            created_at=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        return bool(
            settings.AWS_PROFILE
            or settings.GCP_PROJECT
            or settings.AZURE_SUBSCRIPTION_ID
        )
