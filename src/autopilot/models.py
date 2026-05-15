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


class ConnectorManifest(BaseModel):
    name: str
    description: str
    capabilities: list[Capability]
    event_types: list[str] = Field(default_factory=list)
    safe_actions: list[str] = Field(default_factory=list)
    reliability_score: float = 0.9
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


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hyp"))
    title: str
    rationale: str
    confidence: float = 0.35
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "open"


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    source: str
    title: str
    summary: str
    url: str | None = None
    confidence: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    connector: str
    action: str
    status: str
    summary: str
    artifact_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    confidence: float = 0.0
    replans: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    memory_notes: list[str] = Field(default_factory=list)


class WebhookSignalRequest(BaseModel):
    source: str = "webhook"
    type: str = "operational_signal"
    summary: str
    entities: list[str] = Field(default_factory=list)
    urgency: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)
