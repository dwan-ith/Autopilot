from __future__ import annotations

from typing import Any

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Signal


class WebhookConnector(Connector):
    manifest = ConnectorManifest(
        name="webhook",
        description="Generic inbound event connector for arbitrary external systems.",
        category="Inbound",
        auth_mode="webhook",
        capabilities=[Capability.READ],
        objects=["events", "alerts", "signals"],
        event_types=["support_escalation", "monitoring_alert", "status_update", "operational_signal"],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="webhook_ingest_event",
                description="Normalize an inbound webhook payload into an AUTOPILOT signal.",
                capability=Capability.READ,
                input_schema={"payload": "Webhook JSON payload"},
                output="Signal",
            ),
        ],
        reliability_score=0.98,
    )


class SentryConnector(Connector):
    manifest = ConnectorManifest(
        name="sentry",
        description=(
            "Normalizes Sentry issue and error webhooks into operational signals. "
            "Searches the Sentry REST API when SENTRY_TOKEN and SENTRY_ORG are configured."
        ),
        category="Observability",
        auth_mode="webhook",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.ACTION],
        scopes=["events.read", "issues.read", "issues.write"],
        objects=["errors", "issues", "stack traces", "projects"],
        event_types=["error.created", "issue.regression", "issue.created"],
        safe_actions=["mark_investigating"],
        tools=[
            ConnectorToolSpec(
                name="sentry_ingest_issue",
                description="Normalize Sentry issue and regression webhooks into mission signals.",
                capability=Capability.READ,
                input_schema={"payload": "Sentry webhook JSON"},
                output="Signal",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="sentry_search_issues",
                description="Search the Sentry REST API for recent errors, regressions, and stack traces.",
                capability=Capability.SEARCH,
                input_schema={"query": "Error, culprit, project, or issue query"},
                output="Evidence[]",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.92,
    )

    def readiness(self, action: str | None = None) -> dict:
        import os
        token = bool(os.getenv("SENTRY_TOKEN"))
        org = bool(os.getenv("SENTRY_ORG"))
        missing = ([] if token else ["SENTRY_TOKEN"]) + ([] if org else ["SENTRY_ORG"])
        return {
            "configured": not missing,
            "action_ready": not missing,
            "missing": missing,
            "mode": "api+webhook" if not missing else "webhook_only",
            "detail": "Sentry API + webhook active." if not missing else f"Webhook active. Set {', '.join(missing)} for API search.",
            "action": action,
        }

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        event = payload.get("event") or payload.get("data", {}).get("event") or {}
        issue = payload.get("issue") or payload.get("data", {}).get("issue") or {}
        project = payload.get("project") or payload.get("data", {}).get("project") or {}
        title = issue.get("title") or event.get("title") or event.get("message") or "Sentry error event"
        culprit = event.get("culprit") or issue.get("culprit")
        project_name = project.get("slug") or project.get("name")
        entities = [item for item in [project_name, culprit, issue.get("shortId")] if item]
        event_id = event.get("event_id") or issue.get("id") or payload.get("id")
        return Signal(
            source=self.manifest.name,
            type=str(payload.get("action", "error.created")),
            summary=f"Sentry reported {title}",
            entities=entities,
            urgency="high" if payload.get("action") in {"created", "regression"} else "medium",
            payload=payload,
            idempotency_key=f"sentry:{event_id}" if event_id else None,
        )

    async def search(self, query: str) -> list:
        """Search Sentry issues via the REST API."""
        import os
        import httpx
        from autopilot.models import Evidence

        token = os.getenv("SENTRY_TOKEN", "").strip()
        org = os.getenv("SENTRY_ORG", "").strip()

        if not token or not org:
            missing = ([] if token else ["SENTRY_TOKEN"]) + ([] if org else ["SENTRY_ORG"])
            return [Evidence(
                source="sentry",
                title="Sentry not configured",
                summary=f"Set these env vars to enable Sentry search: {', '.join(missing)}",
                confidence=0.0,
                metadata={"missing": missing},
            )]

        project = os.getenv("SENTRY_PROJECT", "").strip()
        url = f"https://sentry.io/api/0/organizations/{org}/issues/"
        params: dict[str, Any] = {
            "query": query,
            "limit": 10,
            "sort": "date",
            "statsPeriod": "24h",
        }
        if project:
            params["project"] = project

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                issues = resp.json()
                results = []
                for issue in issues:
                    results.append(Evidence(
                        source="sentry",
                        title=issue.get("title", "Sentry issue"),
                        summary=(
                            f"[{issue.get('project', {}).get('slug', '?')}] "
                            f"culprit={issue.get('culprit', 'unknown')} | "
                            f"events={issue.get('count', '?')} | "
                            f"users={issue.get('userCount', '?')} | "
                            f"status={issue.get('status', '?')}"
                        ),
                        url=issue.get("permalink", ""),
                        confidence=0.82,
                        metadata={
                            "issue_id": issue.get("id"),
                            "short_id": issue.get("shortId"),
                            "status": issue.get("status"),
                            "culprit": issue.get("culprit"),
                            "first_seen": issue.get("firstSeen"),
                            "last_seen": issue.get("lastSeen"),
                        },
                    ))
                if not results:
                    results.append(Evidence(
                        source="sentry",
                        title="No Sentry issues found",
                        summary=f"No issues matched '{query[:80]}' in the last 24h.",
                        confidence=0.15,
                    ))
                return results
        except httpx.HTTPStatusError as exc:
            return [Evidence(source="sentry", title="Sentry API error", summary=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}", confidence=0.0)]
        except Exception as exc:
            return [Evidence(source="sentry", title="Sentry search failed", summary=str(exc), confidence=0.0)]

    async def action(self, name: str, payload: dict[str, Any]) -> Any:
        """Execute a Sentry action (mark_investigating → acknowledge)."""
        import os
        import httpx
        from autopilot.models import ActionResult

        if name != "mark_investigating":
            return ActionResult(connector="sentry", action=name, status="skipped", summary=f"Unknown action: {name}")

        token = os.getenv("SENTRY_TOKEN", "").strip()
        org = os.getenv("SENTRY_ORG", "").strip()
        issue_id = payload.get("issue_id", "")

        if not token or not org or not issue_id:
            return ActionResult(
                connector="sentry", action=name, status="skipped",
                summary="Missing SENTRY_TOKEN, SENTRY_ORG, or issue_id in payload.",
            )

        url = f"https://sentry.io/api/0/organizations/{org}/issues/{issue_id}/"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"status": "acknowledged"},
                )
                resp.raise_for_status()
                return ActionResult(
                    connector="sentry", action=name, status="complete",
                    summary=f"Marked Sentry issue {issue_id} as acknowledged.",
                )
        except Exception as exc:
            return ActionResult(connector="sentry", action=name, status="failed", summary=str(exc))

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="sentry_search_issues",
                description=(
                    "Search Sentry for recent errors and issue regressions. "
                    "Use to find error spikes, stack traces, and affected projects. "
                    "Requires SENTRY_TOKEN and SENTRY_ORG env vars."
                ),
                parameters={"query": "Sentry issue search query (e.g. 'checkout TypeError last 24h')"},
                fn=self.search,
            )
        ]
