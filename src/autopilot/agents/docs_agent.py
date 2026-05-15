from __future__ import annotations

import logging
from autopilot.config import settings
from datetime import datetime, timezone

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType

logger = logging.getLogger(__name__)


class DocsAgent(BaseAgent):
    agent_type = AgentType.DOCS

    async def handle(self, task: AgentTask) -> ActionRecord:
        notion = settings.NOTION_TOKEN
        confluence = settings.CONFLUENCE_TOKEN
        logger.info(
            "DocsAgent handling task %s (notion=%s, confluence=%s)",
            task.id,
            bool(notion),
            bool(confluence),
        )
        result = {"status": "stubbed", "note": "would update Notion/Confluence pages"}
        return ActionRecord(
            agent_type=self.agent_type,
            platform="docs",
            action_type="handle_task",
            payload=task.payload,
            result=result,
            created_at=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        return bool(settings.NOTION_TOKEN or settings.CONFLUENCE_TOKEN)
