from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot.models import ApprovalStatus, AuthMode, ConnectorConnection, ConnectorStatus, Mission, MissionStatus, OperatorStep, Signal, utc_now


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
ARTIFACT_DIR = ROOT / "artifacts"
DB_PATH = DATA_DIR / "autopilot.db"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_connector_granted_scopes(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _parse_connector_metadata(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return val
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _parse_connector_connected_at(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._write_count = 0
        self._wal_checkpoint_every = int(os.getenv("AUTOPILOT_WAL_CHECKPOINT_EVERY", "50"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout=30000")
        conn.execute("pragma foreign_keys=on")
        return conn

    def _maybe_checkpoint(self, conn: sqlite3.Connection) -> None:
        """Run a passive WAL checkpoint every N writes to prevent WAL bloat."""
        self._write_count += 1
        if self._write_count % self._wal_checkpoint_every == 0:
            try:
                conn.execute("pragma wal_checkpoint(PASSIVE)")
            except Exception:
                pass  # Non-fatal; SQLite will checkpoint eventually on its own

    def init(self) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
            conn.execute("pragma journal_mode=wal")
            conn.execute("pragma synchronous=normal")
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
                    idempotency_key text,
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
                create table if not exists connector_connections (
                    connector_id text not null,
                    user_id text not null default 'default_user',
                    status text not null,
                    auth_mode text not null,
                    granted_scopes text not null,
                    connected_at text,
                    credentials_ref text,
                    metadata text not null,
                    primary key (connector_id, user_id)
                );
                """
            )
            self._ensure_column(conn, "signals", "idempotency_key", "text")
            self._ensure_column(conn, "connector_connections", "user_id", "text default 'default_user'")
            self._ensure_column(conn, "connector_connections", "credentials_ref", "text")
            conn.execute(
                """
                create unique index if not exists idx_signals_idempotency
                    on signals(idempotency_key)
                    where idempotency_key is not null
                """
            )

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {ddl}")

    def save_signal(self, signal: Signal, mission_id: str | None = None) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into signals
                (id, mission_id, source, type, summary, entities, urgency, idempotency_key, payload, received_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    mission_id,
                    signal.source,
                    signal.type,
                    signal.summary,
                    json.dumps(signal.entities),
                    signal.urgency,
                    signal.idempotency_key,
                    json.dumps(signal.payload, default=_json_default),
                    signal.received_at.isoformat(),
                ),
            )

    def create_mission(self, mission: Mission) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
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
        with self._lock:
            existing = self.get_mission(mission.id)
            if existing:
                seen_signals = {signal.id for signal in mission.signals}
                mission.signals.extend(signal for signal in existing.signals if signal.id not in seen_signals)

                seen_graph = {node.id for node in mission.graph}
                mission.graph.extend(node for node in existing.graph if node.id not in seen_graph)

                seen_policies = {decision.id for decision in mission.policy_decisions}
                mission.policy_decisions.extend(
                    decision for decision in existing.policy_decisions if decision.id not in seen_policies
                )

                seen_approvals = {approval.id for approval in mission.approvals}
                mission.approvals.extend(
                    approval for approval in existing.approvals if approval.id not in seen_approvals
                )
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
                self._maybe_checkpoint(conn)
            for signal in mission.signals:
                self.save_signal(signal, mission.id)
            return

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
        with self._lock, closing(self.connect()) as conn, conn:
            row = conn.execute("select payload from missions where id=?", (mission_id,)).fetchone()
        if not row:
            return None
        return Mission.model_validate_json(row["payload"])

    
    def count_missions(self) -> int:
        with self._lock, closing(self.connect()) as conn, conn:
            row = conn.execute('SELECT COUNT(*) as count FROM missions').fetchone()
        return row['count'] if row else 0

    def count_traces(self) -> int:
        with self._lock, closing(self.connect()) as conn, conn:
            row = conn.execute('SELECT COUNT(*) as count FROM traces').fetchone()
        return row['count'] if row else 0

    def list_missions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                """
                select payload
                from missions order by datetime(updated_at) desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [Mission.model_validate_json(row["payload"]).model_dump() for row in rows]

    def active_missions(self) -> list[Mission]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select payload from missions where status in (?, ?, ?) order by datetime(updated_at) desc",
                (MissionStatus.QUEUED.value, MissionStatus.RUNNING.value, MissionStatus.WAITING.value),
            ).fetchall()
        return [Mission.model_validate_json(row["payload"]) for row in rows]

    def mission_for_signal_key(self, idempotency_key: str | None) -> Mission | None:
        if not idempotency_key:
            return None
        with self._lock, closing(self.connect()) as conn, conn:
            row = conn.execute(
                """
                select m.payload
                from signals s
                join missions m on m.id = s.mission_id
                where s.idempotency_key=?
                limit 1
                """,
                (idempotency_key,),
            ).fetchone()
        return Mission.model_validate_json(row["payload"]) if row else None

    def list_action_approvals(self, status: ApprovalStatus | None = None) -> list[dict[str, Any]]:
        approvals: list[dict[str, Any]] = []
        for mission in self._all_missions():
            for approval in mission.approvals:
                if status and approval.status != status:
                    continue
                item = approval.model_dump()
                item["mission_title"] = mission.title
                item["mission_status"] = mission.status.value
                item["confidence"] = mission.confidence
                approvals.append(item)
        approvals.sort(key=lambda item: item["requested_at"], reverse=True)
        return approvals

    def get_action_approval(self, approval_id: str) -> tuple[Mission, int] | None:
        for mission in self._all_missions():
            for index, approval in enumerate(mission.approvals):
                if approval.id == approval_id:
                    return mission, index
        return None

    def _all_missions(self) -> list[Mission]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute("select payload from missions order by datetime(updated_at) desc").fetchall()
        return [Mission.model_validate_json(row["payload"]) for row in rows]

    def add_step(self, step: OperatorStep) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
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

    def list_steps(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select * from steps where mission_id=? order by created_at, id",
                (mission_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def trace(self, mission_id: str | None, name: str, status: str, payload: dict[str, Any], parent_step_id: str | None = None) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
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
            self._maybe_checkpoint(conn)

    def list_traces(self, mission_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, closing(self.connect()) as conn, conn:
            if mission_id:
                rows = conn.execute(
                    "select * from trace_events where mission_id=? order by id desc limit ?",
                    (mission_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("select * from trace_events order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def remember(self, key: str, value: str) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
            conn.execute(
                "insert into memory_items (key, value, created_at) values (?, ?, ?)",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )
            self._prune_memory_locked(conn, key)

    def _prune_memory_locked(self, conn: sqlite3.Connection, key: str) -> None:
        keep = max(1, int(os.getenv("AUTOPILOT_MEMORY_KEEP_PER_KEY", "500")))
        conn.execute(
            """
            delete from memory_items
            where key=?
              and id not in (
                select id from memory_items
                where key=?
                order by id desc
                limit ?
              )
            """,
            (key, key, keep),
        )

    def recall(self, key_like: str, limit: int = 5) -> list[str]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                "select value from memory_items where key like ? order by id desc limit ?",
                (f"%{key_like}%", limit),
            ).fetchall()
        return [row["value"] for row in rows]

    def list_connector_connections(self, user_id: str = "default_user") -> list[ConnectorConnection]:
        with self._lock, closing(self.connect()) as conn, conn:
            rows = conn.execute("select * from connector_connections where user_id=?", (user_id,)).fetchall()
        return [
            ConnectorConnection(
                connector_id=row["connector_id"],
                user_id=row["user_id"],
                status=ConnectorStatus(row["status"]),
                auth_mode=AuthMode(row["auth_mode"]),
                granted_scopes=_parse_connector_granted_scopes(row["granted_scopes"]),
                connected_at=_parse_connector_connected_at(row["connected_at"]),
                credentials_ref=row["credentials_ref"],
                metadata=_parse_connector_metadata(row["metadata"]),
            )
            for row in rows
        ]

    def get_connector_connection(self, connector_id: str, user_id: str = "default_user") -> ConnectorConnection | None:
        with self._lock, closing(self.connect()) as conn, conn:
            row = conn.execute(
                "select * from connector_connections where connector_id=? and user_id=?",
                (connector_id, user_id),
            ).fetchone()
        if not row:
            return None
        return ConnectorConnection(
            connector_id=row["connector_id"],
            user_id=row["user_id"],
            status=ConnectorStatus(row["status"]),
            auth_mode=AuthMode(row["auth_mode"]),
            granted_scopes=_parse_connector_granted_scopes(row["granted_scopes"]),
            connected_at=_parse_connector_connected_at(row["connected_at"]),
            credentials_ref=row["credentials_ref"],
            metadata=_parse_connector_metadata(row["metadata"]),
        )

    def save_connector_connection(self, connection: ConnectorConnection) -> None:
        with self._lock, closing(self.connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into connector_connections
                (connector_id, user_id, status, auth_mode, granted_scopes, connected_at, credentials_ref, metadata)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection.connector_id,
                    connection.user_id,
                    connection.status.value,
                    connection.auth_mode.value,
                    json.dumps(connection.granted_scopes),
                    connection.connected_at.isoformat() if connection.connected_at else None,
                    connection.credentials_ref,
                    json.dumps(connection.metadata, default=_json_default),
                ),
            )


class StateStore(Store):
    """Shared durable mission state for orchestrator, agents, traces, and analytics.

    Extends Store with analytics read methods for dashboards and reporting.
    """

    def mission_stats(self) -> dict:
        """Aggregate mission statistics: counts by status, avg confidence, replan rate."""
        missions = self._all_missions()
        if not missions:
            return {"total": 0, "by_status": {}, "avg_confidence": 0.0, "avg_replans": 0.0, "avg_evidence": 0.0}

        by_status: dict[str, int] = {}
        total_conf = 0.0
        total_replans = 0
        total_evidence = 0
        for m in missions:
            by_status[m.status.value] = by_status.get(m.status.value, 0) + 1
            total_conf += m.confidence
            total_replans += m.replans
            total_evidence += len(m.evidence)

        n = len(missions)
        return {
            "total": n,
            "by_status": by_status,
            "avg_confidence": round(total_conf / n, 3),
            "avg_replans": round(total_replans / n, 2),
            "avg_evidence": round(total_evidence / n, 2),
        }

    def agent_performance(self) -> list[dict]:
        """Per-role agent metrics: call count, avg tool calls, avg confidence, failure rate."""
        missions = self._all_missions()
        roles: dict[str, dict] = {}
        for m in missions:
            for run in m.agent_runs:
                role = run.role
                if role not in roles:
                    roles[role] = {"role": role, "runs": 0, "total_tool_calls": 0, "total_conf": 0.0, "failures": 0, "total_duration_ms": 0.0}
                bucket = roles[role]
                bucket["runs"] += 1
                bucket["total_tool_calls"] += run.tool_calls
                bucket["total_conf"] += run.confidence
                bucket["total_duration_ms"] += run.duration_ms
                if run.status.value == "failed":
                    bucket["failures"] += 1

        result = []
        for bucket in roles.values():
            n = bucket["runs"]
            result.append({
                "role": bucket["role"],
                "total_runs": n,
                "avg_tool_calls": round(bucket["total_tool_calls"] / n, 1) if n else 0,
                "avg_confidence": round(bucket["total_conf"] / n, 3) if n else 0,
                "failure_rate": round(bucket["failures"] / n, 3) if n else 0,
                "avg_duration_ms": round(bucket["total_duration_ms"] / n, 1) if n else 0,
            })
        return sorted(result, key=lambda r: r["total_runs"], reverse=True)

    def connector_health(self) -> list[dict]:
        """Per-connector action health: total actions, success/fail/skip counts."""
        missions = self._all_missions()
        connectors: dict[str, dict] = {}
        for m in missions:
            for action in m.actions:
                name = action.connector
                if name not in connectors:
                    connectors[name] = {"connector": name, "total": 0, "complete": 0, "failed": 0, "skipped": 0, "blocked": 0}
                bucket = connectors[name]
                bucket["total"] += 1
                status = action.status
                if status in bucket:
                    bucket[status] += 1

        return sorted(connectors.values(), key=lambda c: c["total"], reverse=True)

