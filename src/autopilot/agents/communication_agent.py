from __future__ import annotations

import logging
from autopilot.config import settings
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType

logger = logging.getLogger(__name__)


class CommunicationAgent(BaseAgent):
    agent_type = AgentType.COMMUNICATION

    async def handle(self, task: AgentTask) -> ActionRecord:
        slack = settings.SLACK_BOT_TOKEN
        teams = settings.TEAMS_WEBHOOK_URL
        logger.info(
            "CommunicationAgent handling task %s (slack=%s, teams=%s)",
            task.id,
            bool(slack),
            bool(teams),
        )
        result = {"status": "stubbed", "note": "would send Slack/Teams messages"}
        return ActionRecord(
            agent_type=self.agent_type,
            platform="communication",
            action_type="handle_task",
            payload=task.payload,
            result=result,
            created_at=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        return bool(settings.SLACK_BOT_TOKEN or settings.TEAMS_WEBHOOK_URL)
