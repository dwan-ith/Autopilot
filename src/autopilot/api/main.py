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
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from autopilot.connectors import default_registry
from autopilot.connectors.actions import ArtifactConnector, NotificationConnector
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.gmail import GmailConnector
from autopilot.connectors.google_drive import GoogleDriveConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.linear import LinearConnector
from autopilot.connectors.notion import NotionConnector
from autopilot.connectors.oauth import (
    SCOPES_COMBINED,
    SCOPES_DRIVE,
    SCOPES_GMAIL,
    OAuthTokenStore,
    build_github_auth_url,
    build_google_auth_url,
    build_oauth_state,
    exchange_code,
    exchange_github_code,
    github_configured,
    google_configured,
    mirror_google_oauth_to_siblings,
    verify_oauth_state,
)
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.tavily import TavilyConnector
from autopilot.connectors.weather import WeatherConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector
from autopilot.kernel import RuntimeKernel
from autopilot.mcp_server import AutopilotMCPHTTP, AutopilotMCPServer
from autopilot.models import (
    ActionResult,
    ApprovalStatus,
    AuthMode,
    Capability,
    GraphNodeKind,
    MissionGraphNode,
    Signal,
    StepStatus,
    WebhookSignalRequest,
    new_id,
    utc_now,
)
from autopilot.operators.llm import (
    active_provider_name,
    preflight_providers,
    provider_health,
)
from autopilot.storage import ARTIFACT_DIR, ROOT, StateStore

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

store = StateStore()
registry = default_registry()
directory = ConnectorDirectory(store)

runtime = RuntimeKernel(store, registry)
monitor_task: asyncio.Task | None = None
mcp_task: asyncio.Task | None = None
mcp_http_app: Any = None


class _MCPMountProxy:
    """ASGI app that delegates /mcp requests to the live transport once the
    session manager has finished starting."""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        target = mcp_http_app
        if target is None or scope.get("type") != "http":
            await PlainTextResponse("MCP transport unavailable", status_code=503)(scope, receive, send)
            return
        await target(scope, receive, send)


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

    Fail-closed: when neither a webhook secret nor an API key is configured,
    unverified ingest is refused unless AUTOPILOT_ALLOW_UNVERIFIED_WEBHOOKS=1
    is explicitly set. A silently-open webhook endpoint lets anyone forge
    operational signals that trigger autonomous missions.
    """
    secret = os.getenv("AUTOPILOT_WEBHOOK_SECRET", "").strip()
    if not secret:
        api_key_set = bool(os.getenv("AUTOPILOT_API_KEY", "").strip())
        allow_unverified = os.getenv("AUTOPILOT_ALLOW_UNVERIFIED_WEBHOOKS", "").lower() in {"1", "true", "yes"}
        if not api_key_set and not allow_unverified:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook ingest refused: no AUTOPILOT_API_KEY and no AUTOPILOT_WEBHOOK_SECRET "
                    "is configured. Set one of them, or AUTOPILOT_ALLOW_UNVERIFIED_WEBHOOKS=1 to "
                    "explicitly accept unsigned local traffic."
                ),
            )
        return

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

# Webhook ingest gets its own bucket: each event spawns an LLM investigation
# swarm, so an unthrottled flood is expensive by construction.
_webhook_bucket = _TokenBucket(rate=_signal_rate / 60.0, capacity=float(_signal_rate))


async def _webhook_rate_limit() -> None:
    if not await _webhook_bucket.consume():
        raise HTTPException(
            status_code=429,
            detail=f"Webhook rate limit exceeded: max {_signal_rate}/minute.",
            headers={"Retry-After": "60"},
        )


def _init_omium_sdk_if_configured() -> dict[str, Any]:
    """Best-effort official Omium SDK initialization.

    The product must not pretend that arbitrary HTTP trace POSTs prove Omium
    visibility. SDK initialization is reported explicitly and local SQLite
    tracing remains authoritative when the SDK is unavailable.
    """
    log_api = logging.getLogger("autopilot.api")
    sdk_requested = any(
        os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
        for name in ["OMIUM_SDK_INIT", "OMIUM_TRACING", "OMIUM_CHECKPOINTS"]
    )
    if not sdk_requested:
        return {"requested": False, "initialized": False, "detail": "Set OMIUM_SDK_INIT=1 or OMIUM_TRACING=1 to enable the SDK."}
    key = os.getenv("OMIUM_API_KEY", "").strip()
    if not key:
        log_api.warning("OMIUM_SDK_INIT is set but OMIUM_API_KEY is empty")
        return {"requested": True, "initialized": False, "detail": "OMIUM_API_KEY is empty."}
    try:
        import omium
    except ImportError:
        log_api.warning(
            "OMIUM_SDK_INIT is set but the 'omium' package is not installed "
            "(install with: pip install omium   or   pip install '.[omium]')"
        )
        return {"requested": True, "initialized": False, "detail": "omium package is not installed."}
    base = os.getenv("OMIUM_API_URL", "").strip() or None
    try:
        init_kwargs = {
            "api_key": key,
            "project": os.getenv("OMIUM_PROJECT", "autopilot"),
            "auto_trace": os.getenv("OMIUM_TRACING", "1").strip().lower() not in {"0", "false", "no", "off"},
            "auto_checkpoint": os.getenv("OMIUM_CHECKPOINTS", "1").strip().lower() not in {"0", "false", "no", "off"},
            "debug": os.getenv("OMIUM_DEBUG", "").lower() in {"1", "true", "yes"},
        }
        if base:
            init_kwargs["api_base_url"] = base
        try:
            omium.init(**init_kwargs)
        except TypeError:
            # SDK versions have changed names for the base-url/project fields.
            fallback_kwargs = {"api_key": key}
            if base:
                fallback_kwargs["api_url"] = base
            omium.init(**fallback_kwargs)
        log_api.info(
            "Omium SDK initialized for project=%s",
            os.getenv("OMIUM_PROJECT", "autopilot"),
        )
        return {"requested": True, "initialized": True, "detail": "Omium SDK initialized."}
    except Exception as exc:
        log_api.warning("Omium SDK init failed (non-fatal): %s", exc)
        return {"requested": True, "initialized": False, "detail": str(exc)[:200]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the DB schema has the composite PK migration applied
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("migrate_db", ROOT / "../../../migrate_db.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[arg-type]
            mod.migrate()
    except Exception as exc:
        logging.getLogger("autopilot.api").warning("Startup migration skipped: %s", exc)
    try:
        pruned = store.prune_stale_traces(days=7)
        if pruned:
            logging.getLogger("autopilot.api").info("Pruned %d stale trace events", pruned)
    except Exception as exc:
        logging.getLogger("autopilot.api").warning("Trace cleanup failed: %s", exc)
    if not os.getenv("AUTOPILOT_API_KEY", "").strip():
        logging.getLogger("autopilot.api").warning(
            "AUTOPILOT_API_KEY is not set — every endpoint (including mission cancel, "
            "action approval, and webhook ingest) accepts unauthenticated requests. "
            "Set it before exposing this service beyond loopback."
        )
    omium_status = _init_omium_sdk_if_configured()
    store.trace(None, "omium.sdk.status", "complete" if omium_status.get("initialized") else "skipped", omium_status)
    if os.getenv("AUTOPILOT_PROVIDER_PREFLIGHT_ON_STARTUP", "").lower() in {"1", "true", "yes"}:
        try:
            health_result = await preflight_providers()
            store.trace(None, "provider.preflight.startup", "complete", health_result)
        except Exception as exc:
            store.trace(None, "provider.preflight.startup", "failed", {"error": str(exc)})
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
    global monitor_task, mcp_task
    if os.getenv("AUTOPILOT_PERSISTENT_MONITORING", "").lower() in {"1", "true", "yes"}:
        monitor_task = asyncio.create_task(_monitor_connected_services_loop())
        store.trace(None, "monitor.started", "started", {"interval_seconds": _monitor_interval_seconds()})

    # Periodic trace retention — startup-only pruning misses long-running
    # processes, and trace_events grows without bound without it.
    async def _retention_loop() -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                store.prune_stale_traces(days=7)
            except Exception:
                logging.getLogger("autopilot.api").warning("Periodic trace pruning failed", exc_info=True)

    retention_task = asyncio.create_task(_retention_loop())

    # Serve the authenticated MCP Streamable HTTP transport under /mcp/.
    # StreamableHTTPSessionManager requires its run() context to stay open
    # for the lifetime of the app; a parked task holds it open.
    async def _hold_mcp_transport() -> None:
        global mcp_http_app
        http = AutopilotMCPHTTP(AutopilotMCPServer(registry=registry, store=store, runtime=runtime))
        try:
            async with http.run():
                # Bind the module-level target only once the session manager is
                # actually serving, so the proxy never hands requests to a dead app.
                mcp_http_app = http
                await asyncio.Event().wait()  # park until cancelled
        except Exception:
            logging.getLogger("autopilot.api").warning("MCP transport task crashed", exc_info=True)

    # Mount immediately: until the parked task finishes bring-up (milliseconds),
    # _MCPMountProxy answers 503 while unbound, and the transport itself answers
    # 503 "starting" until its session manager is live — no startup ordering race.
    mcp_task = asyncio.create_task(_hold_mcp_transport())
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/mcp"]
    app.mount("/mcp", _MCPMountProxy())

    try:
        yield
    finally:
        for task in (monitor_task, mcp_task, retention_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if monitor_task:
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


@app.get("/api/provider/health")
async def provider_health_endpoint(request: Request) -> dict[str, Any]:
    """Expose safe provider-pool health without leaking API keys."""
    require_read_access(request)
    return provider_health()


@app.post("/api/provider/preflight")
async def provider_preflight_endpoint(request: Request) -> dict[str, Any]:
    """Run a bounded live provider preflight and quarantine bad slots."""
    require_write_access(request)
    result = await preflight_providers()
    store.trace(None, "provider.preflight.manual", "complete", result)
    return result


@app.get("/api/tracing/status")
async def tracing_status(request: Request) -> dict[str, Any]:
    """Show whether tracing is local-only, SDK-requested, or relay-delivered."""
    require_read_access(request)
    status = runtime.tracer.status()
    status["local_trace_events"] = len(store.list_traces(limit=1000))
    return status


@app.post("/api/tracing/probe")
async def tracing_probe(request: Request) -> dict[str, Any]:
    """Emit a local trace and optional relay event so trace proof is explicit."""
    require_write_access(request)
    payload = {"source": "manual_probe", "timestamp": utc_now().isoformat()}
    runtime.tracer.emit(None, "tracing.probe", "complete", payload)
    return runtime.tracer.status()

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


@app.post("/api/operators/{connector_id}/smoke")
async def smoke_operator(connector_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Credential-gated live proof for account connectors.

    This is intentionally read-only for Gmail, Drive, Notion, Linear, and
    GitHub. It proves live access when credentials exist and returns a blocked
    state when setup is incomplete instead of pretending the connector is ready.
    """
    require_read_access(request)
    connector = _connector_for_operator(connector_id)
    p = payload or {}
    query = str(p.get("query") or "AUTOPILOT smoke test")
    started = time.monotonic()
    readiness = connector.readiness()
    if not readiness.get("action_ready") and not readiness.get("configured"):
        result = {
            "operator": connector_id,
            "runtime_name": connector.manifest.name,
            "status": "blocked",
            "live": False,
            "readiness": readiness,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "detail": readiness.get("detail", "Connector is not configured."),
        }
        store.trace(None, "operator.smoke.blocked", "blocked", result)
        return result

    try:
        if connector.has(Capability.SEARCH):
            evidence = await connector.search(query)
            failed = any(
                getattr(item, "confidence", 1.0) <= 0.0
                or "failed" in getattr(item, "title", "").lower()
                or "not authorized" in getattr(item, "title", "").lower()
                or "not configured" in getattr(item, "title", "").lower()
                for item in evidence
            )
            status = "failed" if failed else "live"
            result = {
                "operator": connector_id,
                "runtime_name": connector.manifest.name,
                "status": status,
                "live": status == "live",
                "readiness": readiness,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "evidence_count": len(evidence),
                "sample": [item.model_dump() for item in evidence[:3]],
            }
        else:
            result = {
                "operator": connector_id,
                "runtime_name": connector.manifest.name,
                "status": "live" if readiness.get("action_ready") else "blocked",
                "live": bool(readiness.get("action_ready")),
                "readiness": readiness,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "detail": readiness.get("detail", ""),
            }
        store.trace(None, f"operator.smoke.{result['status']}", result["status"], result)
        return result
    except Exception as exc:
        result = {
            "operator": connector_id,
            "runtime_name": connector.manifest.name,
            "status": "failed",
            "live": False,
            "readiness": readiness,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "error": str(exc),
        }
        store.trace(None, "operator.smoke.failed", "failed", result)
        return result


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
    await _webhook_rate_limit()
    payload = await _verified_json_payload(request)
    connector = registry.get(connector_name) if registry.has_name(connector_name) else registry.get("webhook")
    signal = await connector.normalize_event(payload)
    signal.source = connector_name
    store.trace(None, "webhook.received", "complete", {"connector": connector_name, "payload": _redacted_payload(payload)})
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id, "status": mission.status}


async def _verified_json_payload(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > 512 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload exceeds 512 KiB limit")
    _verify_webhook_signature(body, request)
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook JSON body must be an object")
    return payload


def _redacted_payload(payload: dict[str, Any], limit: int = 4000) -> dict[str, Any]:
    """Bound what we persist from inbound webhooks: full raw payloads can be
    large and can contain third-party secrets; the trace only needs enough to
    reconstruct the ingest decision."""
    try:
        serialized = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return {"truncated": True}
    if len(serialized) <= limit:
        return payload
    return {"truncated": True, "preview": serialized[:limit]}

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


# Per-approval execution locks: approving the same action twice concurrently
# must not double-fire the connector side effect.
_approval_locks: dict[str, asyncio.Lock] = {}


@app.post("/api/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_write_access(request)
    lock = _approval_locks.setdefault(approval_id, asyncio.Lock())
    async with lock:
        return await _approve_action_locked(approval_id, request, payload)


async def _approve_action_locked(approval_id: str, request: Request, payload: dict[str, Any] | None) -> dict[str, Any]:
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

    # Persist the decision BEFORE executing so a crash mid-execution cannot
    # leave the approval PENDING (which would let a retry fire the side
    # effect twice).
    approval.status = ApprovalStatus.EXECUTING
    approval.decided_at = utc_now()
    approval.decided_by = (payload or {}).get("decided_by") or "operator"
    mission.approvals[index] = approval
    store.update_mission(mission)

    try:
        result = await connector.action(approval.action, approval.payload)
        approval.result = result
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
        last_version = ""
        last_payload = ""
        while True:
            if await request.is_disconnected():
                return
            try:
                version = store.stream_version()
                if version != last_version:
                    # Serialize the snapshot only when something actually changed;
                    # bounds keep a single slow client from re-reading the whole DB.
                    totals = {"missions": store.count_missions(), "traces": store.count_traces(), "approvals": len(store.list_action_approvals())}
                    payload = json.dumps(
                        {
                            "missions": store.list_missions(limit=200),
                            "traces": store.list_traces(limit=500),
                            "totals": totals,
                        },
                        default=str,
                    )
                    if payload != last_payload:
                        yield f"data: {payload}\n\n"
                        last_payload = payload
                    last_version = version
            except Exception:  # never let one bad poll kill the stream
                logging.getLogger("autopilot.api").warning("SSE poll failed", exc_info=True)
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/missions/{mission_id}/continue")
async def continue_mission(mission_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resume a mission paused after analysis (`defer_delivery`), finalizing delivery.

    The continuation secret is issued to the operator when the mission pauses;
    presenting it proves an authorized party saw the paused state."""
    require_write_access(request)
    body = payload or {}
    secret = str(body.get("continuation_secret") or "")
    if not secret:
        raise HTTPException(status_code=400, detail="continuation_secret is required")
    resumed = runtime.resume_pipeline_delivery(mission_id, secret)
    if not resumed:
        mission = store.get_mission(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        if mission.status.value == "waiting":
            raise HTTPException(status_code=403, detail="Invalid continuation secret or delivery not pending")
        raise HTTPException(status_code=409, detail=f"Mission is {mission.status.value}; only waiting missions can be continued")
    store.trace(mission_id, "pipeline.continued", "started", {"operator": True})
    mission = store.get_mission(mission_id)
    return {"resumed": True, "mission": mission.model_dump() if mission else None}


# ── Scoped agent routes ─────────────────────────────────────────────────────

# Imported here deliberately: specialized agents pull in the operator layer,
# which must be fully initialized before these route factories exist.
from autopilot.agents.specialized import (  # noqa: E402
    CloudInfraAgent,
    ProjectMgmtAgent,
    SecurityAuditAgent,
)


@app.post("/api/agents/project-mgmt/create-issue")
async def agent_project_mgmt_create_issue(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a Linear follow-up issue through the ProjectMgmtAgent policy gate."""
    require_write_access(request)
    p = payload or {}
    agent = ProjectMgmtAgent(store=store, registry=registry)
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
    """Queue a deployment through the CloudInfraAgent.

    Deployments are HIGH-risk and can never be self-approved from the request
    body: the agent evaluates policy with the human-approval rule enforced and
    queues a pending ActionApproval on the scoped mission (or refuses when no
    mission_id is supplied). Execution happens only after operator sign-off
    via POST /api/approvals/{id}/approve.
    """
    require_write_access(request)
    p = payload or {}
    agent = CloudInfraAgent(store=store, registry=registry)
    result = await agent.trigger_deployment(
        mission_id=p.get("mission_id"),
        environment=p.get("environment", "staging"),
        ref=p.get("ref", "main"),
        reason=p.get("reason", "AUTOPILOT deployment trigger"),
    )
    store.trace(p.get("mission_id"), "agent.cloud_infra.trigger_deployment", result.status, result.model_dump())
    return result.model_dump()


@app.post("/api/agents/security-audit/pr-open")
async def agent_security_audit_pr_open(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a PR-open security audit: writes a durable artifact and emits an ops notification."""
    require_write_access(request)
    p = payload or {}
    agent = SecurityAuditAgent(store=store, registry=registry)
    result = await agent.run_pr_open_audit(payload=p, mission_id=p.get("mission_id"))
    store.trace(p.get("mission_id"), "agent.security_audit.pr_open", result.status, result.model_dump())
    return result.model_dump()


# ── Analytics API ────────────────────────────────────────────────────────────

@app.get("/api/analytics/missions")
async def analytics_missions(request: Request) -> dict:
    """Aggregate mission statistics."""
    require_read_access(request)
    analytics = store  # shared StateStore — no schema rebuild per request
    return analytics.mission_stats()


@app.get("/api/analytics/agents")
async def analytics_agents(request: Request) -> list[dict]:
    """Per-role agent performance metrics."""
    require_read_access(request)
    analytics = store  # shared StateStore — no schema rebuild per request
    return analytics.agent_performance()


@app.get("/api/analytics/connectors")
async def analytics_connectors(request: Request) -> list[dict]:
    """Per-connector health: merges live connection status with action history.
    Only includes connectors that are genuinely connected/configured — never fakes data.
    """
    require_read_access(request)

    # 1. Get historical action stats (only connectors with actual mission actions)
    analytics = store  # shared StateStore — no schema rebuild per request
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
    analytics = store  # shared StateStore — no schema rebuild per request

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
    from autopilot.operators.llm import _BAD_SLOTS, _build_slots, active_provider_name
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
    await _webhook_rate_limit()
    payload = await _verified_json_payload(request)
    store.trace(None, "webhook.received", "complete", {"connector": "jira", "payload": _redacted_payload(payload)})
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
    await _webhook_rate_limit()
    payload = await _verified_json_payload(request)
    store.trace(None, "webhook.received", "complete", {"connector": "weather", "payload": _redacted_payload(payload)})
    connector = WeatherConnector()
    signal = await connector.normalize_event(payload)
    mission = await runtime.ingest(signal)
    return {"status": "accepted", "mission_id": mission.id, "signal_id": signal.id}


# ── OAuth2 Flow ───────────────────────────────────────────────────────────────


@app.get("/oauth/authorize/{connector_id}")
async def oauth_authorize(connector_id: str) -> RedirectResponse:
    """Redirect to OAuth2 consent screen for the given connector.

    The state parameter is a signed, short-lived token binding provider and
    connector id — the callback refuses any state it did not issue, so an
    attacker cannot choose which connector row a code is stored under
    (login CSRF / token-store poisoning)."""
    if connector_id == "github":
        if not github_configured():
            return _frontend_redirect("error=github_oauth_not_configured")
        return RedirectResponse(url=build_github_auth_url(state=build_oauth_state("github", "github")))

    if not google_configured():
        return _frontend_redirect("error=google_oauth_not_configured")
    scopes_map = {
        "gmail": SCOPES_GMAIL,
        "google_drive": SCOPES_DRIVE,
        "google": SCOPES_COMBINED,
    }
    scopes = scopes_map.get(connector_id, SCOPES_COMBINED)
    auth_url = build_google_auth_url(state=build_oauth_state("google", connector_id), scopes=scopes)
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
        # The signed state decides which connector the tokens belong to;
        # attacker-supplied values are rejected before any exchange.
        try:
            connector_id = verify_oauth_state(state, "google")
        except ValueError as exc:
            return _frontend_redirect(f"oauth_error={str(exc)}")
        token_data = await exchange_code(code)
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
    except Exception:
        log_api = logging.getLogger("autopilot.api")
        log_api.warning("Google OAuth exchange failed", exc_info=True)
        return _frontend_redirect("oauth_error=token_exchange_failed")


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
        try:
            connector_id = verify_oauth_state(state, "github")
        except ValueError as exc:
            return _frontend_redirect(f"oauth_error={str(exc)}")
        token_data = await exchange_github_code(code)
        token_store = OAuthTokenStore(store.path)
        user_id = "default_user" # Real app extracts this from request session
        token_store.save(connector_id, token_data, user_id)
        return _frontend_redirect(f"oauth_success={connector_id}")
    except Exception:
        logging.getLogger("autopilot.api").warning("GitHub OAuth exchange failed", exc_info=True)
        return _frontend_redirect("oauth_error=token_exchange_failed")


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

