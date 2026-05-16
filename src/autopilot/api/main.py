from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
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
from autopilot.connectors.actions import ArtifactConnector, NotificationConnector
from autopilot.connectors.linear import LinearConnector
from autopilot.connectors.gmail import GmailConnector
from autopilot.connectors.google_drive import GoogleDriveConnector
from autopilot.connectors.notion import NotionConnector
from autopilot.connectors.tavily import TavilyConnector
from autopilot.connectors.weather import WeatherConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector
from autopilot.connectors.oauth import (
    OAuthTokenStore,
    SCOPES_COMBINED,
    SCOPES_DRIVE,
    SCOPES_GMAIL,
    build_google_auth_url,
    build_github_auth_url,
    exchange_code,
    exchange_github_code,
    google_configured,
    github_configured,
    mirror_google_oauth_to_siblings,
)
from autopilot.kernel import RuntimeKernel
from autopilot.models import ActionResult, ApprovalStatus, AuthMode, Capability, GraphNodeKind, MissionGraphNode, Signal, StepStatus, WebhookSignalRequest, new_id, utc_now
from autopilot.operators.llm import active_provider_name
from autopilot.storage import ARTIFACT_DIR, ROOT, Store
from fastapi.responses import RedirectResponse

CONNECTOR_CLASSES = {
    "github": GitHubConnector,
    "web_search": KnowledgeConnector,
    "local_artifacts": ArtifactConnector,
    "linear": LinearConnector,
    "slack": NotificationConnector,
    "sentry": SentryConnector,
    "webhook": WebhookConnector,
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

runtime = RuntimeKernel(store, registry)
monitor_task: asyncio.Task | None = None


def _frontend_redirect(query: str) -> RedirectResponse:
    """Redirect back to the dashboard after OAuth (env-overridable for non-local setups)."""
    base = os.getenv("AUTOPILOT_FRONTEND_URL", "http://localhost:3000").rstrip("/")
    q = query.lstrip("?&")
    return RedirectResponse(url=f"{base}/?{q}")


def require_write_access(request: Request) -> None:
    _require_api_key(request, allow_query_key=False)


def require_read_access(request: Request) -> None:
    _require_api_key(request, allow_query_key=True)


def _require_api_key(request: Request, allow_query_key: bool) -> None:
    expected = os.getenv("AUTOPILOT_API_KEY", "").strip()
    if not expected:
        return
    supplied = request.headers.get("x-autopilot-key", "").strip()
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if allow_query_key:
        supplied = supplied or request.query_params.get("access_key", "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="AUTOPILOT_API_KEY is required")


# ---------------------------------------------------------------------------
# HMAC webhook signature verification
# ---------------------------------------------------------------------------

def _verify_webhook_signature(body: bytes, request: Request) -> None:
    """Verify inbound webhook HMAC signature if AUTOPILOT_WEBHOOK_SECRET is set.

    Supports:
      - X-Hub-Signature-256   (GitHub)
      - X-Sentry-Hook-Signature (Sentry)
      - X-Autopilot-Signature (generic)
    """
    secret = os.getenv("AUTOPILOT_WEBHOOK_SECRET", "").strip()
    if not secret:
        return  # No secret configured — skip verification

    sig_header = (
        request.headers.get("x-hub-signature-256", "")
        or request.headers.get("x-sentry-hook-signature", "")
        or request.headers.get("x-autopilot-signature", "")
    )
    if not sig_header:
        raise HTTPException(status_code=401, detail="Missing webhook signature header")

    # Strip prefix (e.g. "sha256=<digest>")
    if "=" in sig_header:
        sig_hex = sig_header.split("=", 1)[1]
    else:
        sig_hex = sig_header

    expected_digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected_digest, sig_hex.lower()):
        raise HTTPException(status_code=401, detail="Webhook signature mismatch")


# ---------------------------------------------------------------------------
# Token-bucket rate limiter for /api/signals
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple in-process token-bucket rate limiter."""

    def __init__(self, rate: float, capacity: float):
        self._rate = rate          # tokens per second
        self._capacity = capacity  # max burst
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False


# 30 req/min default; override with AUTOPILOT_SIGNAL_RATE_LIMIT=N (per minute)
_signal_rate = int(os.getenv("AUTOPILOT_SIGNAL_RATE_LIMIT", "30"))
_signal_bucket = _TokenBucket(rate=_signal_rate / 60.0, capacity=float(_signal_rate))


def _init_omium_sdk_if_configured() -> None:
    """Optional official Omium SDK (see https://docs.omium.ai/docs/sdk/python-sdk).

    Enabled with OMIUM_SDK_INIT=1 + OMIUM_API_KEY + ``pip install omium``.
    TraceSink SQLite events are unchanged; dashboard tracing uses SDK / decorators.
    """
    log_api = logging.getLogger("autopilot.api")
    if os.getenv("OMIUM_SDK_INIT", "").lower() not in {"1", "true", "yes"}:
        return
    key = os.getenv("OMIUM_API_KEY", "").strip()
    if not key:
        log_api.warning("OMIUM_SDK_INIT is set but OMIUM_API_KEY is empty")
        return
    try:
        import omium
    except ImportError:
        log_api.warning(
            "OMIUM_SDK_INIT is set but the 'omium' package is not installed "
            "(install with: pip install omium   or   pip install '.[omium]')"
        )
        return
    base = os.getenv("OMIUM_API_URL", "").strip() or None
    try:
        omium.init(
            api_key=key,
            project=os.getenv("OMIUM_PROJECT", "autopilot"),
            api_base_url=base,
            debug=os.getenv("OMIUM_DEBUG", "").lower() in {"1", "true", "yes"},
        )
        log_api.info(
            "Omium SDK initialized for project=%s",
            os.getenv("OMIUM_PROJECT", "autopilot"),
        )
    except Exception as exc:
        log_api.warning("Omium SDK init failed (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the DB schema has the composite PK migration applied
    try:
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("migrate_db", ROOT / "../../../migrate_db.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[arg-type]
            mod.migrate()
    except Exception:
        pass  # Non-fatal — migration already applied or script not present
    _init_omium_sdk_if_configured()
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
    global monitor_task
    if os.getenv("AUTOPILOT_PERSISTENT_MONITORING", "").lower() in {"1", "true", "yes"}:
        monitor_task = asyncio.create_task(_monitor_connected_services_loop())
        store.trace(None, "monitor.started", "started", {"interval_seconds": _monitor_interval_seconds()})
    try:
        yield
    finally:
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            store.trace(None, "monitor.stopped", "complete", {})

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


def _connector_for_operator(connector_id: str):
    if connector_id in CONNECTOR_CLASSES:
        return CONNECTOR_CLASSES[connector_id]()
    if registry.has_name(connector_id):
        return registry.get(connector_id)
    for cls in CONNECTOR_CLASSES.values():
        connector = cls()
        if connector.manifest.name == connector_id:
            return connector
    raise KeyError(f"Unknown operator '{connector_id}'")


@app.get("/api/operators")
async def operators() -> list[dict[str, Any]]:
    """Return the Scira-style operator surface: tools, actions, and readiness."""
    connected = {item.connector_id: item for item in store.list_connector_connections()}
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for catalog_item in directory.catalog.values():
        connector = _connector_for_operator(catalog_item.id) if catalog_item.id in CONNECTOR_CLASSES else None
        readiness = connector.readiness() if connector else {"configured": False, "action_ready": False, "missing": [], "mode": "catalog_only", "detail": "No runtime adapter.", "action": None}
        connection = connected.get(catalog_item.id)
        items.append({
            "id": catalog_item.id,
            "name": catalog_item.name,
            "runtime_name": connector.manifest.name if connector else catalog_item.id,
            "category": catalog_item.category,
            "description": catalog_item.description,
            "status": connection.status.value if connection else "available",
            "implemented": catalog_item.implemented,
            "configured": readiness.get("configured", False),
            "readiness": readiness,
            "capabilities": [cap.value for cap in catalog_item.capabilities],
            "tools": [tool.model_dump() for tool in catalog_item.tools],
            "safe_actions": catalog_item.safe_actions,
        })
        seen.add(catalog_item.id)
        if connector:
            seen.add(connector.manifest.name)

    for connector in registry._connectors.values():
        if connector.manifest.name in seen:
            continue
        readiness = connector.readiness()
        items.append({
            "id": connector.manifest.name,
            "name": connector.manifest.name,
            "runtime_name": connector.manifest.name,
            "category": connector.manifest.category,
            "description": connector.manifest.description,
            "status": "runtime",
            "implemented": True,
            "configured": readiness.get("configured", False),
            "readiness": readiness,
            "capabilities": [cap.value for cap in connector.manifest.capabilities],
            "tools": [tool.model_dump() for tool in connector.manifest.tools],
            "safe_actions": connector.manifest.safe_actions,
        })
    return sorted(items, key=lambda item: (item["category"], item["name"]))


@app.post("/api/operators/{connector_id}/probe")
async def probe_operator(connector_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a bounded operator smoke probe.

    Search/read probes are read-only. Artifact and notification probes are local
    side effects so judges can verify that the system really acted.
    """
    require_write_access(request)
    p = payload or {}
    query = str(p.get("query") or p.get("ref") or "AUTOPILOT export rollout incident")
    connector = _connector_for_operator(connector_id)
    readiness = connector.readiness()
    started = time.monotonic()
    store.trace(None, "operator.probe.start", "started", {"operator": connector_id, "query": query})
    try:
        if connector.manifest.name == "artifact":
            result = await connector.write(
                f"operator-probe-{new_id('artifact')}",
                "# AUTOPILOT Operator Probe\n\nLocal artifact connector wrote this file.",
                {"operator": connector_id},
            )
            output: Any = result.model_dump()
            kind = "write"
        elif connector.manifest.name == "notification":
            result = await connector.action("notify_ops", {"mission_id": "operator_probe", "text": "AUTOPILOT operator probe notification"})
            output = result.model_dump()
            kind = "notify"
        elif connector.has(Capability.SEARCH):
            output = [item.model_dump() for item in await connector.search(query)]
            kind = "search"
        else:
            signal = await connector.normalize_event({
                "summary": query,
                "type": "operator.probe",
                "entities": ["autopilot", connector_id],
                "urgency": "medium",
            })
            output = signal.model_dump()
            kind = "normalize"
        elapsed = round((time.monotonic() - started) * 1000, 1)
        response = {
            "operator": connector_id,
            "runtime_name": connector.manifest.name,
            "kind": kind,
            "status": "complete",
            "readiness": readiness,
            "duration_ms": elapsed,
            "output": output,
        }
        store.trace(None, "operator.probe.complete", "complete", response)
        return response
    except Exception as exc:
        response = {
            "operator": connector_id,
            "runtime_name": connector.manifest.name,
            "status": "failed",
            "readiness": readiness,
            "error": str(exc),
        }
        store.trace(None, "operator.probe.failed", "failed", response)
        return response


def _monitor_interval_seconds() -> int:
    try:
        configured = int(os.getenv("AUTOPILOT_MONITOR_INTERVAL_SECONDS", "300"))
    except ValueError:
        configured = 300
    return max(30, configured)


def _monitor_targets() -> list[dict[str, Any]]:
    """Connected services the persistent monitor should watch.

    This intentionally uses the connector directory state, not a canned export
    incident. Demo/fallback connections are reported as monitored fallback
    services, but missing credentials only create missions when a connector is
    explicitly connected and not action/search ready.
    """
    targets: list[dict[str, Any]] = []
    for item in directory.list():
        if item.get("status") == "connected" or item.get("live_connected") or item.get("oauth_token_present"):
            targets.append(item)
    return targets


async def _run_connected_service_monitor(source: str) -> dict[str, Any]:
    started = time.monotonic()
    targets = _monitor_targets()
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for item in targets:
        connector_id = item["id"]
        try:
            connector = _connector_for_operator(connector_id)
            readiness = connector.readiness()
            mode = str(readiness.get("mode") or "")
            live = bool(readiness.get("integration_live") or item.get("live_connected") or item.get("oauth_token_present"))
            fallback = bool(item.get("is_demo_connection") or mode.endswith("fallback") or mode in {"anonymous_public", "webhook_only"})
            healthy = bool(readiness.get("configured") and readiness.get("action_ready"))
            status = "healthy" if healthy else "degraded"
            if healthy and fallback and not live:
                status = "fallback"
            check = {
                "id": connector_id,
                "name": item.get("name", connector_id),
                "runtime_name": connector.manifest.name,
                "status": status,
                "live": live,
                "fallback": fallback,
                "readiness": readiness,
            }
            checks.append(check)
            if not healthy:
                issues.append(check)
        except Exception as exc:
            issue = {
                "id": connector_id,
                "name": item.get("name", connector_id),
                "status": "failed",
                "live": False,
                "fallback": False,
                "error": str(exc),
            }
            checks.append(issue)
            issues.append(issue)

    mission_id: str | None = None
    if issues:
        names = [str(issue.get("name") or issue.get("id")) for issue in issues]
        signal = Signal(
            source="autopilot_monitor",
            type="connected_service.degraded",
            summary=f"Connected service monitoring found {len(issues)} service(s) needing attention: {', '.join(names[:5])}.",
            entities=[str(issue.get("id")) for issue in issues],
            urgency="high" if any(issue.get("live") for issue in issues) else "medium",
            payload={"source": source, "checks": checks, "issues": issues},
            idempotency_key=f"monitor:{hashlib.sha256(json.dumps(issues, sort_keys=True, default=str).encode()).hexdigest()[:16]}",
        )
        mission = await runtime.ingest(signal)
        mission_id = mission.id

    result = {
        "status": "complete",
        "source": source,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "targets": len(targets),
        "checks": checks,
        "issues": issues,
        "mission_id": mission_id,
    }
    store.trace(None, "monitor.connected_services.check", "complete", result)
    return result


async def _monitor_connected_services_loop() -> None:
    while True:
        try:
            await _run_connected_service_monitor("persistent_loop")
        except Exception as exc:
            store.trace(None, "monitor.connected_services.failed", "failed", {"error": str(exc)})
        await asyncio.sleep(_monitor_interval_seconds())


@app.post("/api/monitoring/check")
async def monitor_connected_services(request: Request) -> dict[str, Any]:
    require_write_access(request)
    return await _run_connected_service_monitor("manual")

@app.post("/api/connector-directory/{connector_id}/connect")
async def connect_connector(connector_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
    try:
        auth_mode = None
        if payload and payload.get("auth_mode"):
            auth_mode = AuthMode(payload["auth_mode"])
        connection = directory.connect(
            connector_id,
            auth_mode=auth_mode,
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
    payload = await _verified_json_payload(request)
    connector = registry.get(connector_name) if registry.has_name(connector_name) else registry.get("webhook")
    signal = await connector.normalize_event(payload)
    signal.source = connector_name
    store.trace(None, "webhook.received", "complete", {"connector": connector_name, "payload": payload})
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id, "status": mission.status}


async def _verified_json_payload(request: Request) -> dict[str, Any]:
    body = await request.body()
    _verify_webhook_signature(body, request)
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook JSON body must be an object")
    return payload

@app.post("/api/signals")
async def create_signal(signal_request: WebhookSignalRequest, request: Request) -> dict[str, Any]:
    require_write_access(request)
    if not await _signal_bucket.consume():
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_signal_rate} signals/minute. Set AUTOPILOT_SIGNAL_RATE_LIMIT to change.",
            headers={"Retry-After": "60"},
        )
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
    """Legacy endpoint: run connected-service monitoring, not a canned incident."""
    require_write_access(request)
    result = await _run_connected_service_monitor("legacy_demo_endpoint")
    return {
        **result,
        "deprecated": True,
        "message": "The canned export-service simulation was removed. This endpoint now checks connected services.",
    }

@app.get("/api/missions")
async def list_missions(request: Request) -> list[dict[str, Any]]:
    require_read_access(request)
    return store.list_missions()

@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: str, request: Request) -> dict[str, Any]:
    require_read_access(request)
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
async def list_approvals(request: Request, status: ApprovalStatus | None = ApprovalStatus.PENDING) -> list[dict[str, Any]]:
    require_read_access(request)
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
async def traces(request: Request) -> list[dict[str, Any]]:
    require_read_access(request)
    return store.list_traces()

@app.get("/api/artifacts/{artifact_name}")
async def artifact(artifact_name: str, request: Request) -> FileResponse:
    require_read_access(request)
    path = ARTIFACT_DIR / artifact_name
    if not path.exists() or not path.resolve().is_relative_to(ARTIFACT_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path)

@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    require_read_access(request)
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
async def analytics_missions(request: Request) -> dict:
    """Aggregate mission statistics."""
    require_read_access(request)
    analytics = _StateStore(store.path)
    return analytics.mission_stats()


@app.get("/api/analytics/agents")
async def analytics_agents(request: Request) -> list[dict]:
    """Per-role agent performance metrics."""
    require_read_access(request)
    analytics = _StateStore(store.path)
    return analytics.agent_performance()


@app.get("/api/analytics/connectors")
async def analytics_connectors(request: Request) -> list[dict]:
    """Per-connector health: merges live connection status with action history.
    Only includes connectors that are genuinely connected/configured — never fakes data.
    """
    require_read_access(request)

    # 1. Get historical action stats (only connectors with actual mission actions)
    analytics = _StateStore(store.path)
    history: dict[str, dict] = {
        c["connector"]: c for c in analytics.connector_health()
    }

    # 2. Get live readiness from every registered connector
    result: list[dict] = []
    seen: set[str] = set()

    for connector in registry._connectors.values():
        name = connector.manifest.name
        if name in seen:
            continue
        seen.add(name)

        readiness = connector.readiness()
        if not readiness.get("configured", False):
            continue  # skip connectors that are not genuinely connected

        stats = history.get(name, {
            "connector": name,
            "total": 0,
            "complete": 0,
            "failed": 0,
            "skipped": 0,
            "blocked": 0,
        })

        result.append({
            **stats,
            "connector": name,
            "mode": readiness.get("mode", "unknown"),
            "detail": readiness.get("detail", ""),
            "configured": True,
        })

    # Sort: connectors with action history first, then alphabetically
    result.sort(key=lambda c: (-c["total"], c["connector"]))
    return result


@app.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Real-time operational metrics for monitoring and observability.

    Returns mission counts, agent performance, connector health, and LLM pool status.
    Safe to scrape: read-only, no side effects.
    """
    require_read_access(request)
    analytics = _StateStore(store.path)

    # Mission stats
    mission_data = analytics.mission_stats()

    # Agent performance
    agent_data = analytics.agent_performance()
    total_tool_calls = sum(a["total_runs"] * a["avg_tool_calls"] for a in agent_data)
    avg_failure_rate = (
        sum(a["failure_rate"] for a in agent_data) / len(agent_data)
        if agent_data else 0.0
    )

    # Connector health
    connector_data = analytics.connector_health()
    total_actions = sum(c["total"] for c in connector_data)
    failed_actions = sum(c["failed"] for c in connector_data)

    # LLM pool health
    from autopilot.operators.llm import active_provider_name, _BAD_SLOTS, _build_slots
    all_slots = _build_slots()
    quarantined_count = len(_BAD_SLOTS)
    active_slot_count = len(all_slots) - quarantined_count

    # Storage health
    wal_writes = getattr(store, "_write_count", 0)

    return {
        "status": "ok",
        "missions": {
            "total": mission_data.get("total", 0),
            "by_status": mission_data.get("by_status", {}),
            "avg_confidence": mission_data.get("avg_confidence", 0.0),
            "avg_replans": mission_data.get("avg_replans", 0.0),
            "avg_evidence_per_mission": mission_data.get("avg_evidence", 0.0),
        },
        "agents": {
            "total_roles": len(agent_data),
            "total_tool_calls": int(total_tool_calls),
            "avg_failure_rate": round(avg_failure_rate, 4),
            "by_role": agent_data,
        },
        "connectors": {
            "total_actions": total_actions,
            "failed_actions": failed_actions,
            "action_failure_rate": round(failed_actions / total_actions, 4) if total_actions else 0.0,
            "active_connectors": len(connector_data),
        },
        "llm_pool": {
            "provider": active_provider_name(),
            "total_slots_configured": len(all_slots),
            "active_slots": active_slot_count,
            "quarantined_slots": quarantined_count,
            "quarantined_names": list(_BAD_SLOTS.keys()),
        },
        "storage": {
            "wal_writes": wal_writes,
            "wal_checkpoint_interval": getattr(store, "_wal_checkpoint_every", 50),
        },
    }




@app.post("/webhooks/jira")
async def webhook_jira(request: Request) -> dict[str, Any]:
    """Ingest Jira issue webhooks (normalized via generic WebhookConnector)."""
    require_write_access(request)
    payload = await _verified_json_payload(request)
    connector = WebhookConnector()
    signal = await connector.normalize_event(payload)
    signal.source = "jira"
    signal.type = payload.get("webhookEvent", "jira.event")
    mission = await runtime.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


@app.post("/webhooks/weather")
async def webhook_weather(request: Request) -> dict[str, Any]:
    """Ingest weather alert webhooks."""
    require_write_access(request)
    payload = await _verified_json_payload(request)
    connector = WeatherConnector()
    signal = await connector.normalize_event(payload)
    mission = await runtime.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


# ── OAuth2 Flow ───────────────────────────────────────────────────────────────


@app.get("/oauth/authorize/{connector_id}")
async def oauth_authorize(connector_id: str) -> RedirectResponse:
    """Redirect to OAuth2 consent screen for the given connector."""
    if connector_id == "github":
        if not github_configured():
            return _frontend_redirect("error=github_oauth_not_configured")
        return RedirectResponse(url=build_github_auth_url(state=connector_id))
        
    if not google_configured():
        return _frontend_redirect("error=google_oauth_not_configured")
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
        return _frontend_redirect(f"oauth_error={error}")
    if not code:
        return _frontend_redirect("oauth_error=missing_code")
    try:
        token_data = await exchange_code(code)
        connector_id = state or "google"
        token_store = OAuthTokenStore(store.path)
        # Default user mapping
        user_id = "default_user"
        token_store.save(connector_id, token_data, user_id)
        if connector_id == "google":
            token_store.save("gmail", token_data, user_id)
            token_store.save("google_drive", token_data, user_id)
        else:
            mirror_google_oauth_to_siblings(token_store, token_data, user_id, connector_id)
        return _frontend_redirect(f"oauth_success={connector_id}")
    except Exception as e:
        return _frontend_redirect(f"oauth_error={str(e)[:100]}")


@app.get("/oauth/callback/github")
async def oauth_callback_github(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle GitHub App OAuth2 callback."""
    if error:
        return _frontend_redirect(f"oauth_error={error}")
    if not code:
        return _frontend_redirect("oauth_error=missing_code")
    try:
        token_data = await exchange_github_code(code)
        connector_id = state or "github"
        token_store = OAuthTokenStore(store.path)
        user_id = "default_user" # Real app extracts this from request session
        token_store.save(connector_id, token_data, user_id)
        return _frontend_redirect(f"oauth_success={connector_id}")
    except Exception as e:
        return _frontend_redirect(f"oauth_error={str(e)[:100]}")


@app.delete("/oauth/revoke/{connector_id}")
async def oauth_revoke(connector_id: str, request: Request) -> dict[str, str]:
    """Disconnect an OAuth connector by deleting stored tokens."""
    require_write_access(request)
    token_store = OAuthTokenStore(store.path)
    token_store.delete(connector_id)
    return {"status": "revoked", "connector_id": connector_id}


@app.get("/oauth/status")
async def oauth_status(request: Request) -> dict[str, Any]:
    """Return OAuth readiness for all connectors that use OAuth."""
    require_read_access(request)
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

