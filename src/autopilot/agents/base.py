from __future__ import annotations

from abc import ABC, abstractmethod

from autopilot.models import ActionRecord, AgentTask, AgentType


class BaseAgent(ABC):
    agent_type: AgentType

    @abstractmethod
    async def handle(self, task: AgentTask) -> ActionRecord: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
