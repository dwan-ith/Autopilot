from __future__ import annotations

import json
import logging
from typing import Any

from autopilot.models import Mission, Signal, AgentTask, ActionRecord, OperatorStep
from autopilot.storage import Store
from autopilot.storage_interface import StoreInterface

logger = logging.getLogger(__name__)


class RedisPostgresStore(StoreInterface):
    """Start of a Redis (hot) + Postgres (durable) adapter.

    For now this implements a hot Redis layer if available and falls back
    to the existing SQLite `Store` for durable persistence. This is a
    starting point — later we can replace the fallback with Postgres
    writes.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        pg_dsn: str | None = None,
        fallback: Store | None = None,
    ):
        self.fallback = fallback or Store()
        self.redis = None
        if redis_url:
            try:
                import redis

                self.redis = redis.from_url(redis_url)
            except Exception:
                logger.exception(
                    "Failed to initialize redis client; continuing without redis"
                )
                self.redis = None

        # PG DSN support is planned; currently delegate to fallback (SQLite)
        self.pg_dsn = pg_dsn

    # --- mission & signals ---
    def save_signal(self, signal: Signal, mission_id: str | None = None) -> None:
        if self.redis:
            key = f"signal:{signal.id}"
            self.redis.set(key, json.dumps(signal.model_dump(), default=str))
        self.fallback.save_signal(signal, mission_id)

    def create_mission(self, mission: Mission) -> None:
        if self.redis:
            key = f"mission:{mission.id}"
            self.redis.set(key, mission.model_dump_json())
        self.fallback.create_mission(mission)

    def update_mission(self, mission: Mission) -> None:
        if self.redis:
            key = f"mission:{mission.id}"
            self.redis.set(key, mission.model_dump_json())
        self.fallback.update_mission(mission)

    def get_mission(self, mission_id: str) -> Mission | None:
        if self.redis:
            key = f"mission:{mission_id}"
            raw = self.redis.get(key)
            if raw:
                try:
                    return Mission.model_validate_json(raw)
                except Exception:
                    logger.exception("Failed to parse mission from redis payload")
        return self.fallback.get_mission(mission_id)

    def list_missions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.fallback.list_missions(limit)

    def active_missions(self) -> list[Mission]:
        return self.fallback.active_missions()

    # --- steps / traces ---
    def add_step(self, step: OperatorStep) -> None:
        self.fallback.add_step(step)

    def list_steps(self, mission_id: str) -> list[dict[str, Any]]:
        return self.fallback.list_steps(mission_id)

    def trace(
        self,
        mission_id: str | None,
        name: str,
        status: str,
        payload: dict[str, Any],
        parent_step_id: str | None = None,
    ) -> None:
        self.fallback.trace(mission_id, name, status, payload, parent_step_id)

    def list_traces(
        self, mission_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.fallback.list_traces(mission_id, limit)

    # --- memory ---
    def remember(self, key: str, value: str) -> None:
        self.fallback.remember(key, value)

    def recall(self, key_like: str, limit: int = 5) -> list[str]:
        return self.fallback.recall(key_like, limit)

    # --- agent tasks & records ---
    def save_agent_task(self, task: AgentTask) -> None:
        if self.redis:
            key = f"agent_task:{task.id}"
            self.redis.set(key, task.model_dump_json())
        self.fallback.save_agent_task(task)

    def get_agent_tasks(self, mission_id: str) -> list[AgentTask]:
        return self.fallback.get_agent_tasks(mission_id)

    def save_action_record(self, record: ActionRecord) -> None:
        if self.redis:
            key = f"action_record:{record.id}"
            self.redis.set(key, record.model_dump_json())
        self.fallback.save_action_record(record)

    def get_action_records(self, mission_id: str) -> list[ActionRecord]:
        return self.fallback.get_action_records(mission_id)
