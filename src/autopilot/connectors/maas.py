from __future__ import annotations

from typing import Dict

from autopilot.models import AgentType


class MAASConnectorRegistry:
    def __init__(self) -> None:
        self._agents: Dict[AgentType, object] = {}

    def register(self, agent) -> None:
        self._agents[agent.agent_type] = agent

    def get(self, agent_type: AgentType):
        return self._agents.get(agent_type)

    def agents(self):
        return list(self._agents.values())
