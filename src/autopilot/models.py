from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Capability(str, Enum):
    READ = "read"
    SEARCH = "search"
    WRITE = "write"
    ACTION = "action"
    NOTIFY = "notify"


class AuthMode(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH = "oauth"
    WEBHOOK = "webhook"
    DEMO = "demo"
    MCP = "mcp"


class ConnectorStatus(str, Enum):
    AVAILABLE = "available"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class ActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class MissionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


class StepStatus(str, Enum):
    STARTED = "started"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


class GraphNodeKind(str, Enum):
    SIGNAL = "signal"
    HYPOTHESIS = "hypothesis"
    BRANCH = "branch"
    SUBAGENT = "subagent"
    OPERATOR = "operator"
    REPLAN = "replan"
    ACTION = "action"
    POLICY = "policy"
    VALIDATION = "validation"


class ConnectorToolSpec(BaseModel):
    name: str
    description: str
    capability: Capability
    input_schema: dict[str, str] = Field(default_factory=dict)
    output: str = "structured_result"
    risk: ActionRisk = ActionRisk.LOW
    requires_confirmation: bool = False
    mcp_tool: bool = False
    read_only_hint: bool = False
    destructive_hint: bool = False


class ConnectorManifest(BaseModel):
    name: str
    description: str
    category: str = "System"
    auth_mode: AuthMode = AuthMode.NONE
    capabilities: list[Capability]
    event_types: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    safe_actions: list[str] = Field(default_factory=list)
    tools: list[ConnectorToolSpec] = Field(default_factory=list)
    reliability_score: float = Field(default=0.9, ge=0.0, le=1.0)
    auth_required: bool = False


class ConnectorCatalogItem(BaseModel):
    id: str
    name: str
    category: str
    description: str
    icon: str
    auth_mode: AuthMode = AuthMode.OAUTH
    capabilities: list[Capability]
    event_types: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    safe_actions: list[str] = Field(default_factory=list)
    implemented_actions: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    tools: list[ConnectorToolSpec] = Field(default_factory=list)
    demo_available: bool = True
    implemented: bool = False


class ConnectorConnection(BaseModel):
    connector_id: str
    user_id: str = "default_user"  # Support multi-tenant connections
    status: ConnectorStatus = ConnectorStatus.DISCONNECTED
    auth_mode: AuthMode = AuthMode.DEMO
    granted_scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    credentials_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Signal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sig"))
    source: str
    type: str
    summary: str
    entities: list[str] = Field(default_factory=list)
    urgency: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    received_at: datetime = Field(default_factory=utc_now)


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hyp"))
    title: str
    rationale: str
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "open"
    search_focus: str = ""  # What the Investigator should query — set by PlannerAgent


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


class PolicyDecision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("policy"))
    connector: str
    action: str
    allowed: bool
    reason: str
    risk: ActionRisk = ActionRisk.LOW
    requires_validation: bool = False
    confidence_required: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_observed: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class ActionApproval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    mission_id: str
    connector: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    risk: ActionRisk = ActionRisk.MEDIUM
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str | None = None
    result: ActionResult | None = None
    error: str | None = None


class MissionGraphNode(BaseModel):
    id: str = Field(default_factory=lambda: new_id("node"))
    kind: GraphNodeKind
    title: str
    status: StepStatus = StepStatus.STARTED
    parent_ids: list[str] = Field(default_factory=list)
    branch_id: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    retryable: bool = False
    error: str | None = None
    summary: str = ""
    ref_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


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


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("agent"))
    role: str
    status: StepStatus = StepStatus.STARTED
    hypothesis_id: str | None = None
    objective: str = ""
    tools: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    output_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


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
    approvals: list[ActionApproval] = Field(default_factory=list)
    graph: list[MissionGraphNode] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    replans: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    memory_notes: list[str] = Field(default_factory=list)
    """Async pipeline / hackathon orchestration flags (persisted with mission JSON)."""
    pipeline: dict[str, Any] = Field(default_factory=dict)


class WebhookSignalRequest(BaseModel):
    source: str = "webhook"
    type: str = "operational_signal"
    summary: str
    entities: list[str] = Field(default_factory=list)
    urgency: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)


class ConnectorActionRequest(BaseModel):
    mission_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
