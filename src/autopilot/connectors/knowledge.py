
from __future__ import annotations

import httpx
from autopilot.config import settings

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, Evidence


LOCAL_KNOWLEDGE = [
    {
        "keywords": ["export", "rollout", "job", "failure"],
        "title": "Runbook: Export failures after rollout",
        "summary": "Recent export failures are commonly caused by schema flag drift. Check rollout metadata, compare job error signatures, and disable the experimental export pipeline if failures exceed 20%.",
        "confidence": 0.82,
    },
    {
        "keywords": ["customer", "enterprise", "sla", "urgent"],
        "title": "Policy: Enterprise escalation handling",
        "summary": "Enterprise-impacting incidents require an internal alert, customer-safe update draft, and owner assignment within 15 minutes.",
        "confidence": 0.88,
    },
    {
        "keywords": ["latency", "error", "spike", "service"],
        "title": "Runbook: Error spike investigation",
        "summary": "Correlate error spikes with deploys, dependencies, traffic, and feature flags. Prefer rollback or flag-disable actions with validation.",
        "confidence": 0.76,
    },
]


class KnowledgeConnector(Connector):
    manifest = ConnectorManifest(
        name="knowledge",
        description="Searches local runbooks and optionally live web sources.",
        capabilities=[Capability.SEARCH, Capability.READ],
        event_types=[],
        safe_actions=[],
        reliability_score=0.86,
    )

    async def search(self, query: str) -> list[Evidence]:
        query_l = query.lower()
        evidence: list[Evidence] = []
        for item in LOCAL_KNOWLEDGE:
            hits = sum(1 for keyword in item["keywords"] if keyword in query_l)
            if hits:
                evidence.append(
                    Evidence(
                        source=self.manifest.name,
                        title=item["title"],
                        summary=item["summary"],
                        confidence=min(0.95, item["confidence"] + hits * 0.03),
                        metadata={"kind": "local_runbook", "matched_keywords": hits},
                    )
                )

        tavily_key = settings.TAVILY_API_KEY
        if tavily_key:
            evidence.extend(await self._tavily_search(query, tavily_key))

        if not evidence:
            evidence.append(
                Evidence(
                    source=self.manifest.name,
                    title="No strong local source found",
                    summary=f"No local runbook directly matched '{query}'. Follow-up research is recommended.",
                    confidence=0.25,
                    metadata={"kind": "gap"},
                )
            )
        return evidence[:5]

    async def _tavily_search(self, query: str, api_key: str) -> list[Evidence]:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": 3, "search_depth": "basic"},
                )
                response.raise_for_status()
        except Exception as exc:
            return [
                Evidence(
                    source=self.manifest.name,
                    title="Live web search unavailable",
                    summary=f"Tavily search failed: {exc}",
                    confidence=0.2,
                    metadata={"kind": "tool_error"},
                )
            ]

        data = response.json()
        results = []
        for item in data.get("results", []):
            results.append(
                Evidence(
                    source="tavily",
                    title=item.get("title") or item.get("url") or "Web result",
                    summary=item.get("content") or "No summary returned.",
                    url=item.get("url"),
                    confidence=0.62,
                    metadata={"kind": "web_search"},
                )
            )
        return results
