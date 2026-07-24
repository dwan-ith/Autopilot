from __future__ import annotations

import os

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence

LOCAL_KNOWLEDGE = [
    {
        "title": "Export failure after rollout runbook",
        "summary": (
            "For export failures after a release, compare the latest deployment, "
            "schema migrations, queue health, and feature flags before escalating."
        ),
        "keywords": ["export", "failure", "rollout", "deployment", "schema"],
        "confidence": 0.74,
    },
    {
        "title": "Customer incident triage checklist",
        "summary": "Correlate support reports with monitoring, recent changes, and known incidents.",
        "keywords": ["customer", "incident", "support", "monitoring"],
        "confidence": 0.68,
    },
]


class KnowledgeConnector(Connector):
    manifest = ConnectorManifest(
        name="knowledge",
        description="Searches local runbooks and optionally live web sources via Tavily.",
        category="Knowledge",
        auth_mode="api_key",
        capabilities=[Capability.SEARCH, Capability.READ],
        scopes=["runbooks.read", "web.search"],
        objects=["runbooks", "web pages", "documentation"],
        event_types=[],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="knowledge_search",
                description="Search local runbooks and optional live web search for evidence.",
                capability=Capability.SEARCH,
                input_schema={"query": "Investigation query string"},
                output="Evidence[]",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.86,
    )

    def readiness(self, action: str | None = None) -> dict:
        tavily = bool(os.getenv("TAVILY_API_KEY"))
        return {
            "configured": True, "action_ready": True, "missing": [],
            "mode": "local+tavily" if tavily else "local_only",
            "detail": "Local runbooks + Tavily web search active." if tavily else "Local runbooks active. Set TAVILY_API_KEY for web search.",
            "action": action,
            "integration_live": tavily,
        }

    async def search(self, query: str) -> list[Evidence]:
        query_l = query.lower()
        evidence: list[Evidence] = []
        for item in LOCAL_KNOWLEDGE:
            hits = sum(1 for kw in item["keywords"] if kw in query_l)
            if hits:
                evidence.append(Evidence(
                    source=self.manifest.name,
                    title=item["title"],
                    summary=item["summary"],
                    confidence=min(0.95, item["confidence"] + hits * 0.02),
                    metadata={"kind": "local_runbook", "matched_keywords": hits},
                ))

        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            evidence.extend(await self._tavily_search(query, tavily_key))

        if not evidence:
            evidence.append(Evidence(
                source=self.manifest.name,
                title="No matching runbook found",
                summary=f"No local runbook directly matched '{query[:80]}'. Follow-up research is recommended.",
                confidence=0.2,
                metadata={"kind": "gap"},
            ))
        return evidence[:6]

    async def _tavily_search(self, query: str, api_key: str) -> list[Evidence]:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": 3, "search_depth": "basic"},
                )
                response.raise_for_status()
        except (KeyError, ValueError, OSError) as exc:
            return [Evidence(
                source=self.manifest.name,
                title="Live web search unavailable",
                summary=f"Tavily search failed: {exc}",
                confidence=0.15,
                metadata={"kind": "tool_error"},
            )]

        results = []
        for item in response.json().get("results", []):
            results.append(Evidence(
                source="tavily",
                title=item.get("title") or item.get("url") or "Web result",
                summary=item.get("content") or "No summary returned.",
                url=item.get("url"),
                confidence=0.62,
                metadata={"kind": "web_search"},
            ))
        return results

    def as_tools(self):
        """Expose the knowledge search as a SubAgent tool."""
        # Import here to avoid circular imports at module level
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="knowledge_search",
                description=(
                    "Search local runbooks and incident patterns for context. "
                    "Use this before planning investigation branches."
                ),
                parameters={"query": "Search query (e.g. 'export failure after rollout')"},
                fn=self.search,
            )
        ]
