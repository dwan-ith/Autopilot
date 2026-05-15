from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot.models import Mission, MissionStatus, OperatorStep, Signal, utc_now
from autopilot.models import AgentTask, ActionRecord


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
ARTIFACT_DIR = ROOT / "artifacts"
DB_PATH = DATA_DIR / "autopilot.db"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.executescript(
                """
                create table if not exists missions (
                    id text primary key,
                    status text not null,
                    title text not null,
                    severity text not null,
                    summary text not null,
                    confidence real not null,
                    replans integer not null,
                    payload text not null,
                    created_at text not null,
                    updated_at text not null,
                    completed_at text
                );
                create table if not exists signals (
                    id text primary key,
                    mission_id text,
                    source text not null,
                    type text not null,
                    summary text not null,
                    entities text not null,
                    urgency text not null,
                    payload text not null,
                    received_at text not null
                );
                create table if not exists steps (
                    id text primary key,
                    mission_id text not null,
                    parent_id text,
                    name text not null,
                    role text not null,
                    status text not null,
                    input_summary text not null,
                    output_summary text not null,
                    metadata text not null,
                    created_at text not null,
                    completed_at text
                );
                create table if not exists trace_events (
                    id integer primary key autoincrement,
                    mission_id text,
                    parent_step_id text,
                    name text not null,
                    status text not null,
                    payload text not null,
                    created_at text not null
                );
                create table if not exists memory_items (
                    id integer primary key autoincrement,
                    key text not null,
                    value text not null,
                    created_at text not null
                );
                create table if not exists agent_tasks (
                    id text primary key,
                    agent_type text not null,
                    mission_id text not null,
                    signal_id text,
                    priority integer,
                    payload text not null,
                    status text not null,
                    created_at text not null,
                    updated_at text not null
                );
                create table if not exists action_records (
                    id text primary key,
                    agent_type text not null,
                    platform text,
                    action_type text not null,
                    payload text not null,
                    result text not null,
                    trace_id text,
                    created_at text not null
                );
                """
            )

    def save_signal(self, signal: Signal, mission_id: str | None = None) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into signals
                (id, mission_id, source, type, summary, entities, urgency, payload, received_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    mission_id,
                    signal.source,
                    signal.type,
                    signal.summary,
                    json.dumps(signal.entities),
                    signal.urgency,
                    json.dumps(signal.payload, default=_json_default),
                    signal.received_at.isoformat(),
                ),
            )

    def create_mission(self, mission: Mission) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert into missions
                (id, status, title, severity, summary, confidence, replans, payload, created_at, updated_at, completed_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._mission_row(mission),
            )
        for signal in mission.signals:
            self.save_signal(signal, mission.id)

    def update_mission(self, mission: Mission) -> None:
        existing = self.get_mission(mission.id)
        if existing:
            seen = {signal.id for signal in mission.signals}
            mission.signals.extend(signal for signal in existing.signals if signal.id not in seen)
        mission.updated_at = utc_now()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                update missions
                set status=?, title=?, severity=?, summary=?, confidence=?, replans=?, payload=?,
                    created_at=?, updated_at=?, completed_at=?
                where id=?
                """,
                self._mission_row(mission)[1:] + (mission.id,),
            )
        for signal in mission.signals:
            self.save_signal(signal, mission.id)

    def _mission_row(self, mission: Mission) -> tuple[Any, ...]:
        return (
            mission.id,
            mission.status.value,
            mission.title,
            mission.severity,
            mission.summary,
            mission.confidence,
            mission.replans,
            mission.model_dump_json(),
            mission.created_at.isoformat(),
            mission.updated_at.isoformat(),
            mission.completed_at.isoformat() if mission.completed_at else None,
        )

    def get_mission(self, mission_id: str) -> Mission | None:
        with closing(self.connect()) as conn, conn:
            row = conn.execute("select payload from missions where id=?", (mission_id,)).fetchone()
        if not row:
            return None
        return Mission.model_validate_json(row["payload"])

    def list_missions(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                """
                select id, status, title, severity, summary, confidence, replans, created_at, updated_at, completed_at
                from missions order by datetime(updated_at) desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_missions(self) -> list[Mission]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select payload from missions where status in (?, ?, ?) order by datetime(updated_at) desc",
                (MissionStatus.QUEUED.value, MissionStatus.RUNNING.value, MissionStatus.WAITING.value),
            ).fetchall()
        return [Mission.model_validate_json(row["payload"]) for row in rows]

    def add_step(self, step: OperatorStep) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into steps
                (id, mission_id, parent_id, name, role, status, input_summary, output_summary, metadata, created_at, completed_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.id,
                    step.mission_id,
                    step.parent_id,
                    step.name,
                    step.role,
                    step.status.value,
                    step.input_summary,
                    step.output_summary,
                    json.dumps(step.metadata, default=_json_default),
                    step.created_at.isoformat(),
                    step.completed_at.isoformat() if step.completed_at else None,
                ),
            )

    def save_agent_task(self, task: AgentTask) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into agent_tasks
                (id, agent_type, mission_id, signal_id, priority, payload, status, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.agent_type.value,
                    task.mission_id,
                    task.signal_id,
                    task.priority,
                    task.model_dump_json(),
                    task.status,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def get_agent_tasks(self, mission_id: str) -> list[AgentTask]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select payload from agent_tasks where mission_id=? order by datetime(created_at) desc",
                (mission_id,),
            ).fetchall()
        return [AgentTask.model_validate_json(row["payload"]) for row in rows]

    def save_action_record(self, record: ActionRecord) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into action_records
                (id, agent_type, platform, action_type, payload, result, trace_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.agent_type.value,
                    record.platform,
                    record.action_type,
                    record.model_dump_json(),
                    record.result and json.dumps(record.result, default=_json_default) or json.dumps({}),
                    record.trace_id,
                    record.created_at.isoformat(),
                ),
            )

    def get_action_records(self, mission_id: str) -> list[ActionRecord]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select payload from action_records where payload like ? order by datetime(created_at) desc",
                (f"%{mission_id}%",),
            ).fetchall()
        return [ActionRecord.model_validate_json(row["payload"]) for row in rows]

    def list_steps(self, mission_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select * from steps where mission_id=? order by datetime(created_at), id",
                (mission_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def trace(self, mission_id: str | None, name: str, status: str, payload: dict[str, Any], parent_step_id: str | None = None) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert into trace_events (mission_id, parent_step_id, name, status, payload, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    parent_step_id,
                    name,
                    status,
                    json.dumps(payload, default=_json_default),
                    utc_now().isoformat(),
                ),
            )

    def list_traces(self, mission_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn, conn:
            if mission_id:
                rows = conn.execute(
                    "select * from trace_events where mission_id=? order by id desc limit ?",
                    (mission_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("select * from trace_events order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def remember(self, key: str, value: str) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "insert into memory_items (key, value, created_at) values (?, ?, ?)",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    def recall(self, key_like: str, limit: int = 5) -> list[str]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select value from memory_items where key like ? order by id desc limit ?",
                (f"%{key_like}%", limit),
            ).fetchall()
        return [row["value"] for row in rows]
