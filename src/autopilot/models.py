from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Capability(str, Enum):
    READ = "read"
    SEARCH = "search"
    WRITE = "write"
    ACTION = "action"
    NOTIFY = "notify"


class AgentType(str, Enum):
    ORCHESTRATOR = "orchestrator"
    GITHUB = "github"
    PROJECT_MGMT = "project_mgmt"
    COMMUNICATION = "communication"
    CLOUD_INFRA = "cloud_infra"
    DOCS = "docs"
    ANALYTICS = "analytics"
    SECURITY_AUDIT = "security_audit"


class MissionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETE = "complete"
    FAILED = "failed"


class StepStatus(str, Enum):
    STARTED = "started"
    COMPLETE = "complete"
    FAILED = "failed"


class GraphNodeKind(str, Enum):
    SIGNAL = "signal"
    OPERATOR = "operator"
    REPLAN = "replan"
    ACTION = "action"
    POLICY = "policy"


class ConnectorManifest(BaseModel):
    name: str
    description: str
    capabilities: list[Capability]
    event_types: list[str] = Field(default_factory=list)
    safe_actions: list[str] = Field(default_factory=list)
    reliability_score: float = Field(default=0.9, ge=0.0, le=1.0)
    auth_required: bool = False


class Signal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sig"))
    source: str
    type: str
    summary: str
    entities: list[str] = Field(default_factory=list)
    urgency: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=utc_now)
    source_platform: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hyp"))
    title: str
    rationale: str
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "open"


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    source: str
    title: str
    summary: str
    url: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    connector: str
    action: str
    status: str
    summary: str
    artifact_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


<<<<<<< HEAD
class AgentTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    agent_type: AgentType
    mission_id: str
    signal_id: str | None = None
    priority: int = 50
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ActionRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("arec"))
    agent_type: AgentType
    platform: str | None = None
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


=======
class PolicyDecision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("policy"))
    connector: str
    action: str
    allowed: bool
    reason: str
    confidence_required: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_observed: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class MissionGraphNode(BaseModel):
    id: str = Field(default_factory=lambda: new_id("node"))
    kind: GraphNodeKind
    title: str
    status: StepStatus = StepStatus.STARTED
    parent_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    ref_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


>>>>>>> 7e86d18fb019511de1ac9377bbddc4d936f91751
class OperatorStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    mission_id: str
    name: str
    role: str
    status: StepStatus = StepStatus.STARTED
    parent_id: str | None = None
    input_summary: str = ""
    output_summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Mission(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mission"))
    title: str
    status: MissionStatus = MissionStatus.QUEUED
    severity: str = "medium"
    summary: str = ""
    signals: list[Signal] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    actions: list[ActionResult] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    graph: list[MissionGraphNode] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    replans: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    memory_notes: list[str] = Field(default_factory=list)
    assigned_agents: list[AgentType] = Field(default_factory=list)
    policy_flags: dict[str, Any] = Field(default_factory=dict)


class WebhookSignalRequest(BaseModel):
    source: str = "webhook"
    type: str = "operational_signal"
    summary: str
    entities: list[str] = Field(default_factory=list)
    urgency: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)
    source_platform: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
