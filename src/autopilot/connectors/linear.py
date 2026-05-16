"""Linear — API Key Connector.

Uses the Linear GraphQL API to create issues from high-confidence missions.

Env: LINEAR_API_KEY (required)
     LINEAR_TEAM_ID  (optional — auto-detected from first team if not set)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, ActionRisk, Capability, ConnectorManifest, ConnectorToolSpec, Evidence

LINEAR_GQL = "https://api.linear.app/graphql"


class LinearConnector(Connector):
    manifest = ConnectorManifest(
        name="linear",
        description="Create Linear issues for high-confidence missions. Requires LINEAR_API_KEY.",
        category="Engineering",
        auth_mode="api_key",
        capabilities=[Capability.WRITE, Capability.ACTION, Capability.SEARCH],
        scopes=["issues.write", "issues.read"],
        objects=["issues", "teams", "projects"],
        event_types=[],
        safe_actions=["create_issue"],
        tools=[
            ConnectorToolSpec(
                name="linear_search_issues",
                description="Search Linear issues for context on current incidents.",
                capability=Capability.SEARCH,
                input_schema={"query": "Search terms"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="linear_create_issue",
                description="Create a Linear issue after policy approval.",
                capability=Capability.ACTION,
                input_schema={
                    "title": "Issue title",
                    "description": "Issue description (Markdown)",
                    "priority": "Priority 0-4 (0=none, 1=urgent, 4=low)",
                },
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
                mcp_tool=True,
            ),
        ],
        reliability_score=0.88,
        auth_required=True,
    )

    def _api_key(self) -> str | None:
        return os.getenv("LINEAR_API_KEY", "").strip() or None

    def _team_id(self) -> str | None:
        return os.getenv("LINEAR_TEAM_ID", "").strip() or None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key() or "",
            "Content-Type": "application/json",
        }

    def readiness(self, action: str | None = None) -> dict:
        key = self._api_key()
        return {
            "configured": bool(key),
            "action_ready": bool(key),
            "missing": [] if key else ["LINEAR_API_KEY"],
            "mode": "api_key" if key else "missing_credentials",
            "detail": "Linear API ready." if key else "Missing: LINEAR_API_KEY",
            "action": action,
            "integration_live": bool(key),
        }

    async def _gql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                LINEAR_GQL,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
            return resp.json()

    async def _resolve_team_id(self) -> str | None:
        """Get the first available team ID if LINEAR_TEAM_ID not set."""
        tid = self._team_id()
        if tid:
            return tid
        try:
            data = await self._gql("{ teams { nodes { id name } } }")
            nodes = data.get("data", {}).get("teams", {}).get("nodes", [])
            return nodes[0]["id"] if nodes else None
        except Exception:
            return None

    async def search(self, query: str) -> list[Evidence]:
        if not self._api_key():
            return [Evidence(source="linear", title="Linear not configured", summary="Set LINEAR_API_KEY env var", confidence=0.0)]
        gql = """
        query SearchIssues($query: String!) {
            issueSearch(query: $query, first: 8) {
                nodes {
                    id
                    title
                    description
                    priority
                    state { name }
                    team { name }
                    url
                    updatedAt
                }
            }
        }
        """
        try:
            data = await self._gql(gql, {"query": query})
            nodes = data.get("data", {}).get("issueSearch", {}).get("nodes", [])
            results = []
            for issue in nodes:
                state = (issue.get("state") or {}).get("name", "Unknown")
                team = (issue.get("team") or {}).get("name", "")
                results.append(Evidence(
                    source="linear",
                    title=issue.get("title", "Untitled"),
                    summary=f"[{team}] Status: {state} | {(issue.get('description') or '')[:200]}",
                    url=issue.get("url", ""),
                    confidence=0.72,
                    metadata={"priority": issue.get("priority"), "state": state},
                ))
            return results or [Evidence(source="linear", title="No matching issues", summary=f"No Linear issues found for '{query[:60]}'", confidence=0.1)]
        except Exception as e:
            return [Evidence(source="linear", title="Linear search failed", summary=str(e), confidence=0.0)]

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        if name == "create_issue":
            return await self._create_issue(payload)
        return ActionResult(connector="linear", action=name, status="skipped", summary=f"Unknown action: {name}")

    async def _create_issue(self, payload: dict[str, Any]) -> ActionResult:
        if not self._api_key():
            return ActionResult(connector="linear", action="create_issue", status="blocked", summary="LINEAR_API_KEY not configured")

        team_id = await self._resolve_team_id()
        if not team_id:
            return ActionResult(connector="linear", action="create_issue", status="failed", summary="No Linear team found. Set LINEAR_TEAM_ID env var.")

        # Map severity string → Linear priority (0=none, 1=urgent, 2=high, 3=medium, 4=low)
        sev = str(payload.get("severity", "medium")).lower()
        priority_map = {"critical": 1, "high": 2, "medium": 3, "low": 4}
        priority = payload.get("priority", priority_map.get(sev, 3))

        title = payload.get("title", "[AUTOPILOT] Incident")[:250]
        description = payload.get("description") or payload.get("body", "")

        gql = """
        mutation CreateIssue($teamId: String!, $title: String!, $description: String, $priority: Int) {
            issueCreate(input: {
                teamId: $teamId
                title: $title
                description: $description
                priority: $priority
            }) {
                success
                issue { id identifier url title }
            }
        }
        """
        try:
            data = await self._gql(gql, {
                "teamId": team_id,
                "title": title,
                "description": description[:10000],
                "priority": priority,
            })
            result = data.get("data", {}).get("issueCreate", {})
            if result.get("success"):
                issue = result.get("issue", {})
                return ActionResult(
                    connector="linear",
                    action="create_issue",
                    status="complete",
                    summary=f"Created Linear issue {issue.get('identifier')}: {issue.get('title')}",
                    artifact_path=issue.get("url", ""),
                    metadata={"issue_id": issue.get("id"), "identifier": issue.get("identifier")},
                )
            errors = data.get("errors", [])
            return ActionResult(connector="linear", action="create_issue", status="failed", summary=f"Linear API error: {errors}")
        except Exception as e:
            return ActionResult(connector="linear", action="create_issue", status="failed", summary=str(e))

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="linear_search_issues",
                description="Search Linear issues for incident context, on-call history, or known bugs.",
                parameters={"query": "Issue search query (e.g. 'export failure checkout')"},
                fn=self.search,
            )
        ]
