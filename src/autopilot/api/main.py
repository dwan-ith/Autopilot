from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autopilot.connectors import default_registry, default_maas_registry
from autopilot.kernel import RuntimeKernel
from autopilot.models import Signal, WebhookSignalRequest
from autopilot.operators.llm import active_provider_name
from autopilot.storage import ARTIFACT_DIR, ROOT, Store


store = Store()
registry = default_registry()
runtime = RuntimeKernel(store, registry)

app = FastAPI(
    title="AUTOPILOT",
    description="Autonomous Operator Runtime for connected systems.",
    version="0.2.0",
    lifespan=lifespan,
)

dashboard_dir = ROOT / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=dashboard_dir), name="static")


@app.on_event("startup")
async def startup() -> None:
    runtime.resume_active()


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
    return [manifest.model_dump() for manifest in registry.manifests()]


@app.get("/api/agents")
async def agents() -> list[dict[str, Any]]:
    # list registered MAAS agents and health
    agents = []
    if maas:
        for agent in maas.agents():
            agents.append({"agent_type": agent.agent_type.value, "healthy": await agent.health_check()})
    return agents


@app.post("/webhooks/{connector_name}")
async def webhook(connector_name: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    connector = registry.get(connector_name) if connector_name in {m.name for m in registry.manifests()} else registry.get("webhook")
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
        source_platform=signal_request.source_platform,
        raw_payload=signal_request.raw_payload,
    )
    mission = await runtime.ingest(signal)
    return {"accepted": True, "mission_id": mission.id, "signal_id": signal.id}


@app.get("/api/missions/{mission_id}/actions")
async def mission_actions(mission_id: str) -> list[dict[str, Any]]:
    return [rec.model_dump() for rec in store.get_action_records(mission_id)]


@app.post("/demo/fire")
async def fire_demo() -> dict[str, Any]:
    first = Signal(
        source="support_webhook",
        type="support_escalation",
        summary="Enterprise customer reports failed exports after today's rollout.",
        entities=["export service", "enterprise customer", "rollout"],
        urgency="high",
        payload={"customer": "Northstar Analytics", "channel": "support"},
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
                payload={"metric": "export_job_failure_rate", "value": 0.38},
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
                payload={"flag": "experimental_export_pipeline", "state": "enabled"},
            )
        )

    asyncio.create_task(delayed_events())
    return {"started": True, "mission_id": mission.id, "message": "Demo events are being emitted asynchronously."}


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
