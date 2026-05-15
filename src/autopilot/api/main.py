from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autopilot.agents import CloudInfraAgent, ProjectMgmtAgent, SecurityAuditAgent
from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.kernel import RuntimeKernel
from autopilot.models import AuthMode, ConnectorActionRequest, Signal, WebhookSignalRequest, new_id
from autopilot.operators.llm import active_provider_name
from autopilot.policy import PolicyEngine
from autopilot.state_store import StateStore
from autopilot.storage import ARTIFACT_DIR, ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

store = StateStore()
registry = default_registry()
directory = ConnectorDirectory(store)
runtime = RuntimeKernel(store, registry)
policy = PolicyEngine()
project_mgmt_agent = ProjectMgmtAgent(store, registry, policy)
cloud_infra_agent = CloudInfraAgent(store, registry, policy)
security_audit_agent = SecurityAuditAgent(store, registry, policy)

@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.resume_active()
    yield

app = FastAPI(
    title="AUTOPILOT",
    description="Autonomous Operator Runtime for connected systems.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

dashboard_dir = ROOT / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=dashboard_dir), name="static")

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = dashboard_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>AUTOPILOT</h1><p>Dashboard not found.</p>")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "runtime": "autonomous-operator-runtime", "provider": active_provider_name()}

@app.get("/api/provider")
async def provider() -> dict[str, str]:
    """Expose the active LLM provider name to the dashboard."""
    return {"provider": active_provider_name()}

@app.get("/api/connectors")
async def connectors() -> list[dict[str, Any]]:
    """Return real connector status based on actual env var configuration."""
    import os
    result = []
    for connector in registry._connectors.values():
        m = connector.manifest
        # Determine if this connector is actually configured
        env_checks = {
            "github": bool(os.getenv("GITHUB_TOKEN")),
            "linear": bool(os.getenv("LINEAR_API_KEY") and os.getenv("LINEAR_TEAM_ID")),
            "notification": bool(os.getenv("SLACK_WEBHOOK_URL")),
            "knowledge": True,  # Always works; Tavily is optional enhancement
            "artifact": True,   # Always works; no credentials needed
            "webhook": True,
            "sentry": True,     # Inbound only; no credentials needed
        }
        configured = env_checks.get(m.name, not m.auth_required)
        # Count available tools for this connector
        tool_count = len(connector.as_tools()) if hasattr(connector, "as_tools") else 0
        result.append({
            **m.model_dump(),
            "configured": configured,
            "tool_count": tool_count,
        })
    return result


@app.get("/api/connector-directory")
async def connector_directory() -> list[dict[str, Any]]:
    return directory.list()

@app.post("/api/connector-directory/{connector_id}/connect")
async def connect_connector(connector_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        auth_mode = None
        if payload and payload.get("auth_mode"):
            auth_mode = AuthMode(payload["auth_mode"])
        connection = directory.connect(
            connector_id,
            auth_mode,
            credentials_ref=(payload or {}).get("credentials_ref"),
            metadata=(payload or {}).get("metadata") or {},
        )
        store.trace(None, "connector.connected", "complete", connection.model_dump())
        return connection.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/api/connector-directory/{connector_id}/disconnect")
async def disconnect_connector(connector_id: str) -> dict[str, Any]:
    try:
        connection = directory.disconnect(connector_id)
        store.trace(None, "connector.disconnected", "complete", connection.model_dump())
        return connection.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/api/actions/{connector_name}/{action_name}")
async def connector_action(connector_name: str, action_name: str, request: ConnectorActionRequest) -> dict[str, Any]:
    if not registry.has_name(connector_name):
        raise HTTPException(status_code=404, detail="Connector not found")

    connector = registry.get(connector_name)
    mission = store.get_mission(request.mission_id) if request.mission_id else None
    payload = {**request.payload}
    if request.mission_id:
        payload.setdefault("mission_id", request.mission_id)

    if mission:
        decision = policy.decide(mission, connector, action_name)
        mission.policy_decisions.append(decision)
        store.update_mission(mission)
        if not decision.allowed:
            return {"allowed": False, "decision": decision.model_dump(), "action": None}
    elif action_name not in connector.manifest.safe_actions:
        raise HTTPException(status_code=400, detail="Action is not declared safe by connector")
    else:
        decision = None

    result = await connector.action(action_name, payload)
    store.trace(request.mission_id, f"connector.{connector_name}.{action_name}", result.status, result.model_dump())
    return {
        "allowed": True,
        "decision": decision.model_dump() if decision else None,
        "action": result.model_dump(),
    }


@app.post("/api/agents/project-mgmt/create-issue")
async def run_project_mgmt_agent(payload: dict[str, Any]) -> dict[str, Any]:
    result = await project_mgmt_agent.create_follow_up_issue(
        mission_id=payload.get("mission_id"),
        title=payload.get("title"),
        description=payload.get("description"),
        labels=payload.get("labels"),
    )
    return result.model_dump()


@app.post("/api/agents/cloud-infra/trigger-deployment")
async def run_cloud_infra_agent(payload: dict[str, Any]) -> dict[str, Any]:
    result = await cloud_infra_agent.trigger_deployment(
        mission_id=payload.get("mission_id"),
        environment=payload.get("environment", "staging"),
        ref=payload.get("ref", "main"),
        reason=payload.get("reason", "AUTOPILOT deployment trigger"),
        approved=bool(payload.get("approved", False)),
    )
    return result.model_dump()


@app.post("/api/agents/security-audit/pr-open")
async def run_security_audit_agent(payload: dict[str, Any]) -> dict[str, Any]:
    result = await security_audit_agent.run_pr_open_audit(
        payload=payload.get("payload", payload),
        mission_id=payload.get("mission_id"),
    )
    return result.model_dump()


@app.post("/webhooks/{connector_name}")
async def webhook(connector_name: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    connector = registry.get(connector_name) if registry.has_name(connector_name) else registry.get("webhook")
    signal = await connector.normalize_event(payload)
    signal.source = connector_name
    store.trace(None, "webhook.received", "complete", {"connector": connector_name, "payload": payload})
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id, "status": mission.status}

@app.post("/api/signals")
async def create_signal(signal_request: WebhookSignalRequest) -> dict[str, Any]:
    signal = Signal(
        source=signal_request.source,
        type=signal_request.type,
        summary=signal_request.summary,
        entities=signal_request.entities,
        urgency=signal_request.urgency,
        payload=signal_request.payload,
    )
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id}

@app.post("/demo/fire")
async def fire_demo() -> dict[str, Any]:
    run_id = new_id("demo")
    first = Signal(
        source="support_webhook",
        type="support_escalation",
        summary="Enterprise customer reports failed exports after today's rollout.",
        entities=["export service", "enterprise customer", "rollout"],
        urgency="high",
        payload={"customer": "Northstar Analytics", "channel": "support", "run_id": run_id},
        idempotency_key=f"{run_id}:support",
    )
    mission = await runtime.ingest(first)

    async def delayed_events() -> None:
        await asyncio.sleep(0.1)
        await runtime.ingest(
            Signal(
                source="monitoring_webhook",
                type="error_spike",
                summary="Export job failures increased from 1% to 38% in the last 20 minutes.",
                entities=["export service", "job failures"],
                urgency="high",
                payload={"metric": "export_job_failure_rate", "value": 0.38, "run_id": run_id},
                idempotency_key=f"{run_id}:monitoring",
            )
        )
        await asyncio.sleep(0.1)
        await runtime.ingest(
            Signal(
                source="status_webhook",
                type="rollout_status",
                summary="Experimental export pipeline was enabled for enterprise accounts earlier today.",
                entities=["export service", "rollout", "enterprise customer"],
                urgency="medium",
                payload={"flag": "experimental_export_pipeline", "state": "enabled", "run_id": run_id},
                idempotency_key=f"{run_id}:status",
            )
        )

    asyncio.create_task(delayed_events())
    return {"started": True, "mission_id": mission.id, "run_id": run_id, "message": "Demo events are being emitted asynchronously."}

@app.get("/api/missions")
async def list_missions() -> list[dict[str, Any]]:
    return store.list_missions()

@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: str) -> dict[str, Any]:
    mission = store.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {
        "mission": mission.model_dump(),
        "steps": store.list_steps(mission_id),
        "traces": store.list_traces(mission_id),
    }

@app.post("/api/missions/{mission_id}/cancel")
async def cancel_mission(mission_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = (payload or {}).get("reason") or "Canceled from AUTOPILOT API"
    if not runtime.cancel(mission_id, reason):
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"canceled": True, "mission_id": mission_id, "reason": reason}

@app.get("/api/traces")
async def traces() -> list[dict[str, Any]]:
    return store.list_traces()

@app.get("/api/artifacts/{artifact_name}")
async def artifact(artifact_name: str) -> FileResponse:
    path = ARTIFACT_DIR / artifact_name
    if not path.exists() or not path.resolve().is_relative_to(ARTIFACT_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path)

@app.get("/api/events")
async def events() -> StreamingResponse:
    async def stream():
        last_payload = ""
        while True:
            payload = json.dumps({"missions": store.list_missions(), "traces": store.list_traces(limit=20)}, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")
