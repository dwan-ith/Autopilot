from __future__ import annotations

import os

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence


LOCAL_KNOWLEDGE = [
    {
        "keywords": ["export", "rollout", "job", "failure", "pipeline"],
        "title": "Runbook: Export failures after rollout",
        "summary": (
            "Recent export failures are commonly caused by schema flag drift. "
            "Check rollout metadata, compare job error signatures, and disable "
            "the experimental export pipeline if failures exceed 20%."
        ),
        "confidence": 0.82,
    },
    {
        "keywords": ["customer", "enterprise", "sla", "urgent"],
        "title": "Policy: Enterprise escalation handling",
        "summary": (
            "Enterprise-impacting incidents require an internal alert, "
            "customer-safe update draft, and owner assignment within 15 minutes."
        ),
        "confidence": 0.88,
    },
    {
        "keywords": ["latency", "error", "spike", "service", "dependency"],
        "title": "Runbook: Error spike investigation",
        "summary": (
            "Correlate error spikes with deploys, dependencies, traffic, and feature flags. "
            "Prefer rollback or flag-disable actions with validation."
        ),
        "confidence": 0.76,
    },
    {
        "keywords": ["deploy", "deployment", "regression", "config", "flag"],
        "title": "Runbook: Deployment regression response",
        "summary": (
            "If a deployment is correlated with a regression, capture the diff, "
            "verify with canary metrics, and prepare a rollback PR. "
            "Do not rollback without verifying impact scope."
        ),
        "confidence": 0.80,
    },
    {
        "keywords": ["timeout", "queue", "worker", "async", "background"],
        "title": "Runbook: Async worker and queue failures",
        "summary": (
            "Check dead-letter queues, worker memory usage, and connection pool exhaustion. "
            "Common causes: thundering herd after outage recovery, lock contention."
        ),
        "confidence": 0.74,
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
        except Exception as exc:
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
