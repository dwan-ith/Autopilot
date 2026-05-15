"""PagerDuty — Webhook + REST API Connector.

Normalizes PagerDuty incident webhooks (V3 Events API) into AUTOPILOT signals.
Searches incidents and adds notes via the PagerDuty REST API.

Env: PAGERDUTY_TOKEN (required for search + add_note)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, ActionRisk, Capability, ConnectorManifest, ConnectorToolSpec, Evidence, Signal

PAGERDUTY_API = "https://api.pagerduty.com"


class PagerDutyConnector(Connector):
    manifest = ConnectorManifest(
        name="pagerduty",
        description=(
            "Ingests PagerDuty incident webhooks and normalizes them into operational signals. "
            "Searches incidents and adds notes via the PagerDuty REST API when PAGERDUTY_TOKEN is configured."
        ),
        category="Observability",
        auth_mode="webhook",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.ACTION, Capability.NOTIFY],
        scopes=["incidents.read", "incidents.write"],
        objects=["incidents", "services", "escalation policies"],
        event_types=["incident.triggered", "incident.acknowledged", "incident.resolved"],
        safe_actions=["add_note"],
        tools=[
            ConnectorToolSpec(
                name="pagerduty_ingest_incident",
                description="Normalize PagerDuty incident webhooks into mission signals.",
                capability=Capability.READ,
                input_schema={"payload": "PagerDuty webhook JSON"},
                output="Signal",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="pagerduty_search_incidents",
                description="Search PagerDuty incidents via the REST API.",
                capability=Capability.SEARCH,
                input_schema={"query": "Service name, team, or status (triggered/acknowledged/resolved)"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="pagerduty_add_note",
                description="Add an approved responder note to a PagerDuty incident.",
                capability=Capability.ACTION,
                input_schema={"incident_id": "Incident ID", "note": "Note text to add"},
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
                mcp_tool=True,
            ),
        ],
        reliability_score=0.94,
    )

    def _token(self) -> str | None:
        return os.getenv("PAGERDUTY_TOKEN", "").strip() or None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self._token() or ''}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
        }

    def readiness(self, action: str | None = None) -> dict:
        token = bool(self._token())
        missing = [] if token else ["PAGERDUTY_TOKEN"]
        return {
            "configured": not missing,
            "action_ready": not missing,
            "missing": missing,
            "mode": "api+webhook" if not missing else "webhook_only",
            "detail": "PagerDuty API + webhook active." if not missing else "Webhook active. Set PAGERDUTY_TOKEN for search/actions.",
            "action": action,
        }

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        # PagerDuty V3 webhook format
        event = payload.get("event", payload)
        event_data = event.get("data", event) if isinstance(event, dict) else payload
        incident = event_data.get("incident", event_data) if isinstance(event_data, dict) else {}

        title = incident.get("title") or incident.get("summary") or payload.get("summary", "PagerDuty incident")
        service = incident.get("service", {})
        service_name = service.get("name") or service.get("summary") if isinstance(service, dict) else None
        incident_id = incident.get("id") or incident.get("incident_number") or payload.get("id")
        incident_key = incident.get("incident_key") or incident.get("dedup_key")
        urgency = incident.get("urgency", "high")
        status = incident.get("status", "triggered")
        event_type = event.get("event_type", payload.get("event_type", "incident.triggered")) if isinstance(event, dict) else "incident.triggered"

        entities = [item for item in [
            service_name,
            incident.get("escalation_policy", {}).get("summary") if isinstance(incident.get("escalation_policy"), dict) else None,
            f"incident-{incident_id}" if incident_id else None,
        ] if item]

        urgency_map = {"high": "critical", "low": "medium"}
        mapped_urgency = urgency_map.get(urgency, "high") if status == "triggered" else "low"

        return Signal(
            source=self.manifest.name,
            type=str(event_type),
            summary=f"PagerDuty: {title}",
            entities=entities,
            urgency=mapped_urgency,
            payload=payload,
            idempotency_key=f"pagerduty:{incident_key or incident_id}" if (incident_key or incident_id) else None,
        )

    async def search(self, query: str) -> list[Evidence]:
        """Search PagerDuty incidents via the REST API."""
        token = self._token()
        if not token:
            return [Evidence(
                source="pagerduty",
                title="PagerDuty not configured",
                summary="Set PAGERDUTY_TOKEN env var to enable PagerDuty search.",
                confidence=0.0,
                metadata={"missing": ["PAGERDUTY_TOKEN"]},
            )]

        # Build status filter from query keywords
        statuses = []
        q_lower = query.lower()
        if "trigger" in q_lower or "active" in q_lower or "open" in q_lower:
            statuses = ["triggered"]
        elif "ack" in q_lower or "acknowledged" in q_lower:
            statuses = ["acknowledged"]
        elif "resolved" in q_lower or "closed" in q_lower:
            statuses = ["resolved"]
        else:
            statuses = ["triggered", "acknowledged"]

        params: dict[str, Any] = {
            "limit": 10,
            "statuses[]": statuses,
            "sort_by": "created_at:desc",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{PAGERDUTY_API}/incidents",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                incidents = resp.json().get("incidents", [])
                results = []
                for inc in incidents:
                    service_name = (inc.get("service") or {}).get("summary", "?")
                    assignees = [a.get("assignee", {}).get("summary", "?") for a in inc.get("assignments", [])]
                    results.append(Evidence(
                        source="pagerduty",
                        title=inc.get("title", "PagerDuty incident"),
                        summary=(
                            f"[{service_name}] status={inc.get('status', '?')} | "
                            f"urgency={inc.get('urgency', '?')} | "
                            f"assignees={', '.join(assignees) or 'none'} | "
                            f"created={inc.get('created_at', '?')}"
                        ),
                        url=inc.get("html_url", ""),
                        confidence=0.80,
                        metadata={
                            "incident_id": inc.get("id"),
                            "incident_number": inc.get("incident_number"),
                            "status": inc.get("status"),
                            "urgency": inc.get("urgency"),
                            "service": service_name,
                        },
                    ))
                if not results:
                    results.append(Evidence(
                        source="pagerduty",
                        title="No PagerDuty incidents found",
                        summary=f"No {'/'.join(statuses)} incidents found.",
                        confidence=0.15,
                    ))
                return results
        except httpx.HTTPStatusError as exc:
            return [Evidence(source="pagerduty", title="PagerDuty API error", summary=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}", confidence=0.0)]
        except Exception as exc:
            return [Evidence(source="pagerduty", title="PagerDuty search failed", summary=str(exc), confidence=0.0)]

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        if name == "add_note":
            return await self._add_note(payload)
        return ActionResult(connector="pagerduty", action=name, status="skipped", summary=f"Unknown action: {name}")

    async def _add_note(self, payload: dict[str, Any]) -> ActionResult:
        token = self._token()
        if not token:
            return ActionResult(
                connector="pagerduty", action="add_note", status="blocked",
                summary="PAGERDUTY_TOKEN not configured.",
            )

        incident_id = payload.get("incident_id", "")
        note = payload.get("note", "")

        if not incident_id or not note:
            return ActionResult(
                connector="pagerduty", action="add_note", status="failed",
                summary="Both incident_id and note are required.",
            )

        # PagerDuty requires a From header (email of user making the request)
        from_email = os.getenv("PAGERDUTY_FROM_EMAIL", "autopilot@system.local")
        headers = {**self._headers(), "From": from_email}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{PAGERDUTY_API}/incidents/{incident_id}/notes",
                    headers=headers,
                    json={"note": {"content": note}},
                )
                resp.raise_for_status()
                note_data = resp.json().get("note", {})
                return ActionResult(
                    connector="pagerduty",
                    action="add_note",
                    status="complete",
                    summary=f"Added note to PagerDuty incident {incident_id} (note id={note_data.get('id', '?')})",
                    metadata={"incident_id": incident_id, "note_id": note_data.get("id")},
                )
        except httpx.HTTPStatusError as exc:
            return ActionResult(
                connector="pagerduty", action="add_note", status="failed",
                summary=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except Exception as exc:
            return ActionResult(connector="pagerduty", action="add_note", status="failed", summary=str(exc))

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="pagerduty_search_incidents",
                description=(
                    "Search PagerDuty for triggered or acknowledged incidents. "
                    "Use to find active on-call incidents and their assignees. "
                    "Requires PAGERDUTY_TOKEN env var."
                ),
                parameters={"query": "Incident query (e.g. 'triggered database service')"},
                fn=self.search,
            )
        ]
