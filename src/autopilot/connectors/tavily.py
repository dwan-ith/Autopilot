"""Tavily — Web Search Connector.

TAVILY_API_KEY is optional. When not set, the connector falls back to:
  1. DuckDuckGo Instant Answer API (no key, fast factual answers)
  2. Hacker News Algolia search (no key, developer-focused results)

The connector always reports configured:True because it can always perform
web-search-quality lookups, just at different depth tiers.
"""

from __future__ import annotations

import json
import os

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence

TAVILY_API = "https://api.tavily.com"
DDG_API = "https://api.duckduckgo.com/"
HN_API = "https://hn.algolia.com/api/v1/search"

# Module-level DDG availability flag — probed once on first call, cached thereafter.
# None = not yet probed, True = available, False = blocked/rate-limited.
_ddg_available: bool | None = None


async def _probe_ddg_once() -> bool:
    """Check if DDG Instant Answer API is responding. Cached after first call."""
    global _ddg_available
    if _ddg_available is not None:
        return _ddg_available
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                DDG_API,
                params={"q": "ping", "format": "json", "no_redirect": "1"},
                headers={"User-Agent": "AUTOPILOT/1.0"},
            )
            _ddg_available = resp.status_code == 200
    except Exception:
        _ddg_available = False
    return _ddg_available


class TavilyConnector(Connector):
    manifest = ConnectorManifest(
        name="tavily",
        description=(
            "Real-time web search for grounded investigation evidence. "
            "Uses Tavily AI search when configured, or DuckDuckGo + Hacker News as free fallback."
        ),
        category="Research",
        auth_mode="api_key",
        capabilities=[Capability.SEARCH],
        scopes=["web.search"],
        objects=["web pages", "articles", "documentation", "discussions"],
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

    def _api_key(self) -> str | None:
        return os.getenv("TAVILY_API_KEY", "").strip() or None

    def readiness(self, action: str | None = None) -> dict:
        key = self._api_key()
        # Use cached DDG availability if known, otherwise assume available (will be probed on first search)
        ddg_ok = _ddg_available  # None = not yet known, treated as optimistically available
        fallback_available = ddg_ok is not False  # False = known blocked
        fallback_mode = "duckduckgo+hackernews"
        fallback_detail = (
            "Free fallback active: DuckDuckGo + Hacker News. Set TAVILY_API_KEY to upgrade."
            if fallback_available or ddg_ok is None
            else "DuckDuckGo appears blocked or rate-limited. Set TAVILY_API_KEY for reliable search."
        )
        configured = bool(key) or fallback_available or ddg_ok is None
        return {
            "configured": configured,
            "action_ready": configured,
            "missing": [] if key else ["TAVILY_API_KEY (optional — enables AI-powered search)"],
            "mode": "tavily_ai" if key else fallback_mode,
            "detail": "Tavily AI search ready." if key else fallback_detail,
            "action": action,
            "integration_live": bool(key),
            "ddg_available": ddg_ok,
        }

    async def search(self, query: str) -> list[Evidence]:
        if self._api_key():
            return await self._tavily_search(query)
        # Free tier: probe DDG availability, then combine DDG + HN results
        await _probe_ddg_once()
        results: list[Evidence] = []
        results.extend(await self._ddg_search(query))
        results.extend(await self._hn_search(query))
        real = [r for r in results if r.metadata.get("kind") != "tool_error"]
        errors = [r for r in results if r.metadata.get("kind") == "tool_error"]
        if not real and errors:
            # Surface provider failures honestly instead of masking them as
            # "no results found".
            return errors[:6]
        if not real:
            results = [Evidence(
                source="web_search",
                title="No results found",
                summary=f"No results found for '{query[:80]}'. Set TAVILY_API_KEY for reliable web search.",
                confidence=0.1,
                metadata={"mode": "duckduckgo+hackernews", "ddg_available": _ddg_available},
            )]
        return results[:6]

    async def _tavily_search(self, query: str) -> list[Evidence]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{TAVILY_API}/search",
                    json={
                        "api_key": self._api_key(),
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": 5,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                answer = data.get("answer")
                if answer:
                    results.append(Evidence(
                        source="tavily",
                        title=f"Web answer: {query[:60]}",
                        summary=answer[:500],
                        confidence=0.82,
                        metadata={"mode": "tavily_ai"},
                    ))
                for item in data.get("results", []):
                    try:
                        score = min(max(float(item.get("score", 0.5)), 0.0), 1.0)
                    except (TypeError, ValueError):
                        score = 0.5
                    results.append(Evidence(
                        source="tavily",
                        title=item.get("title", "Web result"),
                        summary=item.get("content", "")[:300],
                        url=item.get("url", ""),
                        confidence=score,
                        metadata={"mode": "tavily_ai"},
                    ))
                return results
        except Exception as e:
            return [Evidence(source="tavily", title="Tavily search failed", summary=str(e), confidence=0.0, metadata={"kind": "tool_error"})]

    async def _ddg_search(self, query: str) -> list[Evidence]:
        """DuckDuckGo Instant Answer API — no key required."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    DDG_API,
                    params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
                    headers={"User-Agent": "AUTOPILOT/1.0 (autonomous operator runtime)"},
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError):
            return [Evidence(
                source="duckduckgo",
                title="DuckDuckGo search unavailable",
                summary=f"DDG instant-answer lookup failed for '{query[:80]}'",
                confidence=0.0,
                metadata={"kind": "tool_error", "mode": "ddg_instant_answer"},
            )]

        results: list[Evidence] = []

        # Abstract (best factual answer)
        abstract = data.get("Abstract", "").strip()
        if abstract:
            results.append(Evidence(
                source="duckduckgo",
                title=data.get("Heading", query[:60]),
                summary=abstract[:400],
                url=data.get("AbstractURL", ""),
                confidence=0.70,
                metadata={"mode": "ddg_instant_answer", "source": data.get("AbstractSource", "")},
            ))

        # Related topics
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(Evidence(
                    source="duckduckgo",
                    title=topic.get("Text", "")[:80],
                    summary=topic.get("Text", "")[:300],
                    url=topic.get("FirstURL", ""),
                    confidence=0.52,
                    metadata={"mode": "ddg_related"},
                ))

        return results

    async def _hn_search(self, query: str) -> list[Evidence]:
        """Hacker News Algolia search — no key required."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    HN_API,
                    params={"query": query, "tags": "story", "hitsPerPage": 4},
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError):
            return [Evidence(
                source="hackernews",
                title="Hacker News search unavailable",
                summary=f"HN lookup failed for '{query[:80]}'",
                confidence=0.0,
                metadata={"kind": "tool_error", "mode": "hn_search"},
            )]

        results: list[Evidence] = []
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            if not title:
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            results.append(Evidence(
                source="hackernews",
                title=title,
                summary=f"HN discussion ({hit.get('num_comments', 0)} comments, {hit.get('points', 0)} pts) — {url}",
                url=url,
                confidence=0.55,
                metadata={"mode": "hn_search", "points": hit.get("points"), "comments": hit.get("num_comments")},
            ))
        return results

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="tavily_web_search",
                description=(
                    "Search the live web for current information, documentation, and incident context. "
                    "Works without API key via DuckDuckGo + Hacker News fallback."
                ),
                parameters={"query": "Web search query"},
                fn=self.search,
            )
        ]
