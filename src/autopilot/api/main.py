from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.actions import ArtifactConnector, LinearConnector, NotificationConnector
from autopilot.connectors.gmail import GmailConnector
from autopilot.connectors.google_drive import GoogleDriveConnector
from autopilot.connectors.notion import NotionConnector
from autopilot.connectors.pagerduty import PagerDutyConnector
from autopilot.connectors.tavily import TavilyConnector
from autopilot.connectors.weather import WeatherConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import ActionResult, ApprovalStatus, AuthMode, GraphNodeKind, MissionGraphNode, Signal, StepStatus, WebhookSignalRequest, new_id, utc_now
from autopilot.operators.llm import active_provider_name
from autopilot.storage import ARTIFACT_DIR, ROOT, Store

CONNECTOR_CLASSES = {
    "github": GitHubConnector,
    "web_search": KnowledgeConnector,
    "local_artifacts": ArtifactConnector,
    "linear": LinearConnector,
    "slack": NotificationConnector,
    "sentry": SentryConnector,
    "webhook": WebhookConnector,
    "pagerduty": PagerDutyConnector,
    "notion": NotionConnector,
    "tavily": TavilyConnector,
    "weather": WeatherConnector,
    "gmail": GmailConnector,
    "google_drive": GoogleDriveConnector,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

store = Store()
registry = default_registry()
directory = ConnectorDirectory(store)

for cid, cls in CONNECTOR_CLASSES.items():
    manifest = cls().manifest
    if manifest.auth_required:
        if cid not in [c.connector_id for c in store.list_connector_connections()]:
            registry.unregister(manifest.name)

runtime = RuntimeKernel(store, registry)


def require_write_access(request: Request) -> None:
    expected = os.getenv("AUTOPILOT_API_KEY", "").strip()
    if not expected:
        return
    supplied = request.headers.get("x-autopilot-key", "").strip()
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="AUTOPILOT_API_KEY is required for write operations")

@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.resume_active()
    # Optionally auto-connect demo-capable connectors on startup when enabled.
    try:
        if os.getenv("AUTOPILOT_AUTO_CONNECT_DEMO", "").lower() in {"1", "true", "yes"}:
            for item in directory.catalog.values():
                # Only auto-connect demo-capable connectors that don't require real credentials
                if item.demo_available:
                    try:
                        directory.connect(item.id, AuthMode.DEMO)
                        store.trace(None, "connector.autoconnect", "complete", {"connector": item.id})
                    except Exception:
                        store.trace(None, "connector.autoconnect", "failed", {"connector": item.id})
    except Exception:
        # Be conservative on startup — errors should not prevent the app from running.
        pass
    yield

app = FastAPI(
    title="AUTOPILOT",
    description="Autonomous Operator Runtime for connected systems.",
    version="0.2.0",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("AUTOPILOT_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
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
    """Return runtime connector readiness based on actual configuration."""
    result = []
    for connector in registry._connectors.values():
        m = connector.manifest
        readiness = connector.readiness()
        tool_count = len(connector.as_tools()) if hasattr(connector, "as_tools") else 0
        result.append({
            **m.model_dump(),
            "configured": readiness["configured"],
            "readiness": readiness,
            "tool_count": tool_count,
        })
    return result


@app.get("/api/connector-directory")
async def connector_directory() -> list[dict[str, Any]]:
    return directory.list()

@app.post("/api/connector-directory/{connector_id}/connect")
async def connect_connector(connector_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
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
        if connector_id in CONNECTOR_CLASSES:
            registry.register(CONNECTOR_CLASSES[connector_id]())
        store.trace(None, "connector.connected", "complete", connection.model_dump())
        return connection.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/api/connector-directory/{connector_id}/disconnect")
async def disconnect_connector(connector_id: str, request: Request) -> dict[str, Any]:
    require_write_access(request)
    try:
        connection = directory.disconnect(connector_id)
        if connector_id in CONNECTOR_CLASSES:
            manifest_name = CONNECTOR_CLASSES[connector_id]().manifest.name
            registry.unregister(manifest_name)
        store.trace(None, "connector.disconnected", "complete", connection.model_dump())
        return connection.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/webhooks/{connector_name}")
async def webhook(connector_name: str, request: Request) -> dict[str, Any]:
    require_write_access(request)
    payload = await request.json()
    connector = registry.get(connector_name) if registry.has_name(connector_name) else registry.get("webhook")
    signal = await connector.normalize_event(payload)
    signal.source = connector_name
    store.trace(None, "webhook.received", "complete", {"connector": connector_name, "payload": payload})
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id, "status": mission.status}

@app.post("/api/signals")
async def create_signal(signal_request: WebhookSignalRequest, request: Request) -> dict[str, Any]:
    require_write_access(request)
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
async def fire_demo(request: Request) -> dict[str, Any]:
    require_write_access(request)
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
async def cancel_mission(mission_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
    reason = (payload or {}).get("reason") or "Canceled from AUTOPILOT API"
    if not runtime.cancel(mission_id, reason):
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"canceled": True, "mission_id": mission_id, "reason": reason}


@app.get("/api/approvals")
async def list_approvals(status: ApprovalStatus | None = ApprovalStatus.PENDING) -> list[dict[str, Any]]:
    return store.list_action_approvals(status)


@app.post("/api/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
    found = store.get_action_approval(approval_id)
    if not found:
        raise HTTPException(status_code=404, detail="Approval not found")
    mission, index = found
    approval = mission.approvals[index]
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status.value}")
    if not registry.has_name(approval.connector):
        approval.status = ApprovalStatus.FAILED
        approval.error = f"Connector '{approval.connector}' is not registered"
        approval.decided_at = utc_now()
        approval.decided_by = (payload or {}).get("decided_by") or "operator"
        mission.approvals[index] = approval
        store.update_mission(mission)
        raise HTTPException(status_code=400, detail=approval.error)

    connector = registry.get(approval.connector)
    readiness = connector.readiness(approval.action)
    if not readiness.get("action_ready"):
        approval.status = ApprovalStatus.FAILED
        approval.error = f"Connector is not ready: {readiness.get('detail')}"
        approval.decided_at = utc_now()
        approval.decided_by = (payload or {}).get("decided_by") or "operator"
        mission.approvals[index] = approval
        store.update_mission(mission)
        store.trace(mission.id, "approval.failed", "failed", approval.model_dump())
        return approval.model_dump()
    try:
        result = await connector.action(approval.action, approval.payload)
        approval.result = result
        approval.decided_at = utc_now()
        approval.decided_by = (payload or {}).get("decided_by") or "operator"
        approval.status = ApprovalStatus.EXECUTED if result.status in {"complete", "skipped"} else ApprovalStatus.FAILED
        approval.error = None if approval.status == ApprovalStatus.EXECUTED else result.summary
        mission.approvals[index] = approval
        mission.actions.append(result)
        mission.graph.append(
            MissionGraphNode(
                kind=GraphNodeKind.ACTION,
                title=f"Approved action: {approval.connector}.{approval.action}",
                status=StepStatus.COMPLETE if approval.status == ApprovalStatus.EXECUTED else StepStatus.FAILED,
                ref_id=result.id,
                summary=result.summary,
                completed_at=utc_now(),
                metadata={"approval": approval.model_dump(), "result": result.model_dump()},
            )
        )
        store.update_mission(mission)
        store.trace(mission.id, "approval.executed", approval.status.value, approval.model_dump())
        return approval.model_dump()
    except Exception as exc:
        approval.status = ApprovalStatus.FAILED
        approval.error = str(exc)
        approval.decided_at = utc_now()
        approval.decided_by = (payload or {}).get("decided_by") or "operator"
        approval.result = ActionResult(
            connector=approval.connector,
            action=approval.action,
            status="failed",
            summary=str(exc),
        )
        mission.approvals[index] = approval
        store.update_mission(mission)
        store.trace(mission.id, "approval.failed", "failed", approval.model_dump())
        return approval.model_dump()


@app.post("/api/approvals/{approval_id}/reject")
async def reject_action(approval_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
    found = store.get_action_approval(approval_id)
    if not found:
        raise HTTPException(status_code=404, detail="Approval not found")
    mission, index = found
    approval = mission.approvals[index]
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status.value}")
    approval.status = ApprovalStatus.REJECTED
    approval.decided_at = utc_now()
    approval.decided_by = (payload or {}).get("decided_by") or "operator"
    approval.error = (payload or {}).get("reason") or "Rejected by operator"
    mission.approvals[index] = approval
    store.update_mission(mission)
    store.trace(mission.id, "approval.rejected", "rejected", approval.model_dump())
    return approval.model_dump()

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


# ── Scoped agent routes ─────────────────────────────────────────────────────

from autopilot.agents.specialized import CloudInfraAgent, ProjectMgmtAgent, SecurityAuditAgent
from autopilot.state_store import StateStore as _StateStore

@app.post("/api/agents/project-mgmt/create-issue")
async def agent_project_mgmt_create_issue(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a Linear follow-up issue through the ProjectMgmtAgent policy gate."""
    require_write_access(request)
    p = payload or {}
    agent = ProjectMgmtAgent(store=_StateStore(), registry=registry)
    result = await agent.create_follow_up_issue(
        mission_id=p.get("mission_id"),
        title=p.get("title"),
        description=p.get("description"),
        labels=p.get("labels"),
    )
    store.trace(p.get("mission_id"), "agent.project_mgmt.create_issue", result.status, result.model_dump())
    return result.model_dump()


@app.post("/api/agents/cloud-infra/trigger-deployment")
async def agent_cloud_infra_trigger_deployment(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Trigger a deployment through the CloudInfraAgent (webhook or GitHub Actions dispatch)."""
    require_write_access(request)
    p = payload or {}
    agent = CloudInfraAgent(store=_StateStore(), registry=registry)
    result = await agent.trigger_deployment(
        mission_id=p.get("mission_id"),
        environment=p.get("environment", "staging"),
        ref=p.get("ref", "main"),
        reason=p.get("reason", "AUTOPILOT deployment trigger"),
        approved=bool(p.get("approved", False)),
    )
    store.trace(p.get("mission_id"), "agent.cloud_infra.trigger_deployment", result.status, result.model_dump())
    return result.model_dump()


@app.post("/api/agents/security-audit/pr-open")
async def agent_security_audit_pr_open(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a PR-open security audit: writes a durable artifact and emits an ops notification."""
    require_write_access(request)
    p = payload or {}
    agent = SecurityAuditAgent(store=_StateStore(), registry=registry)
    result = await agent.run_pr_open_audit(payload=p, mission_id=p.get("mission_id"))
    store.trace(p.get("mission_id"), "agent.security_audit.pr_open", result.status, result.model_dump())
    return result.model_dump()


# ── Analytics API ────────────────────────────────────────────────────────────

@app.get("/api/analytics/missions")
async def analytics_missions() -> dict:
    """Aggregate mission statistics."""
    analytics = _StateStore(store.path)
    return analytics.mission_stats()


@app.get("/api/analytics/agents")
async def analytics_agents() -> list[dict]:
    """Per-role agent performance metrics."""
    analytics = _StateStore(store.path)
    return analytics.agent_performance()


@app.get("/api/analytics/connectors")
async def analytics_connectors() -> list[dict]:
    """Per-connector action health."""
    analytics = _StateStore(store.path)
    return analytics.connector_health()


# ── PagerDuty webhook ────────────────────────────────────────────────────────

@app.post("/webhooks/pagerduty")
async def webhook_pagerduty(request: Request) -> dict[str, Any]:
    """Ingest PagerDuty incident webhooks."""
    payload = await request.json()
    connector = PagerDutyConnector()
    signal = await connector.normalize_event(payload)
    mission = await kernel.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


@app.post("/webhooks/jira")
async def webhook_jira(request: Request) -> dict[str, Any]:
    """Ingest Jira issue webhooks."""
    payload = await request.json()
    connector = JiraConnector()
    signal = await connector.normalize_event(payload)
    mission = await kernel.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


@app.post("/webhooks/weather")
async def webhook_weather(request: Request) -> dict[str, Any]:
    """Ingest weather alert webhooks."""
    payload = await request.json()
    connector = WeatherConnector()
    signal = await connector.normalize_event(payload)
    mission = await kernel.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


# ── OAuth2 Flow ───────────────────────────────────────────────────────────────

from autopilot.connectors.oauth import (
    OAuthTokenStore,
    build_google_auth_url,
    exchange_code,
    google_configured,
    SCOPES_GMAIL,
    SCOPES_DRIVE,
    SCOPES_COMBINED,
)
from fastapi.responses import RedirectResponse


@app.get("/oauth/authorize/{connector_id}")
async def oauth_authorize(connector_id: str) -> RedirectResponse:
    """Redirect to Google OAuth2 consent screen for the given connector."""
    if not google_configured():
        return RedirectResponse(url="/?error=google_oauth_not_configured")
    scopes_map = {
        "gmail": SCOPES_GMAIL,
        "google_drive": SCOPES_DRIVE,
        "google": SCOPES_COMBINED,
    }
    scopes = scopes_map.get(connector_id, SCOPES_COMBINED)
    auth_url = build_google_auth_url(state=connector_id, scopes=scopes)
    return RedirectResponse(url=auth_url)


@app.get("/oauth/callback/google")
async def oauth_callback_google(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google OAuth2 callback: exchange code → store tokens → redirect to UI."""
    if error:
        return RedirectResponse(url=f"/?oauth_error={error}")
    if not code:
        return RedirectResponse(url="/?oauth_error=missing_code")
    try:
        token_data = await exchange_code(code)
        connector_id = state or "google"
        token_store = OAuthTokenStore(store.path)
        # Save tokens for the specific connector requested
        token_store.save(connector_id, token_data)
        # If combined scope, also mark both gmail and drive as connected
        if connector_id == "google":
            token_store.save("gmail", token_data)
            token_store.save("google_drive", token_data)
        return RedirectResponse(url=f"/?oauth_success={connector_id}")
    except Exception as e:
        return RedirectResponse(url=f"/?oauth_error={str(e)[:100]}")


@app.delete("/oauth/revoke/{connector_id}")
async def oauth_revoke(connector_id: str) -> dict[str, str]:
    """Disconnect an OAuth connector by deleting stored tokens."""
    token_store = OAuthTokenStore(store.path)
    token_store.delete(connector_id)
    return {"status": "revoked", "connector_id": connector_id}


@app.get("/oauth/status")
async def oauth_status() -> dict[str, Any]:
    """Return OAuth readiness for all connectors that use OAuth."""
    token_store = OAuthTokenStore(store.path)
    oauth_connectors = ["gmail", "google_drive"]
    result = {}
    for cid in oauth_connectors:
        token = token_store.load(cid)
        result[cid] = {
            "authorized": bool(token and token.get("access_token")),
            "google_configured": google_configured(),
            "auth_url": f"/oauth/authorize/{cid}" if not token else None,
        }
    return result

