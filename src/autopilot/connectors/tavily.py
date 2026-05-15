"""Tavily — Web Search Connector.

Provides real-time web search via Tavily API for grounded investigation evidence.
Complements the existing KnowledgeConnector's local runbook search.

Env: TAVILY_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence


TAVILY_API = "https://api.tavily.com"


class TavilyConnector(Connector):
    manifest = ConnectorManifest(
        name="tavily",
        description="Real-time web search for grounded investigation evidence via Tavily API.",
        category="Research",
        auth_mode="api_key",
        capabilities=[Capability.SEARCH],
        scopes=["web.search"],
        objects=["web pages", "articles", "documentation"],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="tavily_web_search",
                description="Search the web for current information, documentation, and incident context.",
                capability=Capability.SEARCH,
                input_schema={"query": "Web search query", "max_results": "Maximum results (default 5)"},
                output="Evidence[]",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.90,
    )

    def _configured(self) -> bool:
        return bool(os.getenv("TAVILY_API_KEY"))

    def readiness(self, action: str | None = None) -> dict:
        configured = self._configured()
        return {
            "configured": configured,
            "action_ready": configured,
            "missing": [] if configured else ["TAVILY_API_KEY"],
            "mode": "api_key",
            "detail": "Tavily web search ready." if configured else "Missing: TAVILY_API_KEY",
            "action": action,
        }

    async def search(self, query: str) -> list[Evidence]:
        if not self._configured():
            return [Evidence(source="tavily", title="Tavily not configured", summary="Set TAVILY_API_KEY env var", confidence=0.0)]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{TAVILY_API}/search",
                    json={
                        "api_key": os.getenv("TAVILY_API_KEY"),
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": 5,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                # Include the AI-generated answer if available
                answer = data.get("answer")
                if answer:
                    results.append(Evidence(
                        source="tavily",
                        title=f"Web answer: {query[:60]}",
                        summary=answer[:500],
                        confidence=0.80,
                    ))
                for item in data.get("results", []):
                    results.append(Evidence(
                        source="tavily",
                        title=item.get("title", "Web result"),
                        summary=item.get("content", "")[:300],
                        url=item.get("url", ""),
                        confidence=min(item.get("score", 0.5), 1.0),
                    ))
                return results
        except Exception as e:
            return [Evidence(source="tavily", title="Tavily search failed", summary=str(e), confidence=0.0)]
