"""Notion — Internal Integration Connector (API key, no OAuth needed).

Uses Notion API v1 with an internal integration token.
Supports searching pages/databases, reading page content, and creating mission brief pages.

Env: NOTION_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import ActionResult, Capability, ConnectorManifest, ConnectorToolSpec, Evidence


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionConnector(Connector):
    manifest = ConnectorManifest(
        name="notion",
        description="Search Notion pages and databases, read page content, and create mission brief pages.",
        category="Knowledge",
        auth_mode="api_key",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE],
        scopes=["pages.read", "pages.write", "databases.read"],
        objects=["pages", "databases"],
        safe_actions=["create_page"],
        tools=[
            ConnectorToolSpec(
                name="notion_search",
                description="Search Notion pages and databases by query.",
                capability=Capability.SEARCH,
                input_schema={"query": "Search query"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="notion_read_page",
                description="Read a Notion page's content.",
                capability=Capability.READ,
                input_schema={"page_id": "Notion page ID"},
                output="Page content",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="notion_create_page",
                description="Create a mission brief page in Notion.",
                capability=Capability.WRITE,
                input_schema={"parent_id": "Parent page or database ID", "title": "Page title", "body": "Page body"},
                output="ActionResult",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.85,
    )

    def _headers(self) -> dict[str, str]:
        token = os.getenv("NOTION_API_KEY", "")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    def _configured(self) -> bool:
        return bool(os.getenv("NOTION_API_KEY"))

    def readiness(self, action: str | None = None) -> dict:
        configured = self._configured()
        return {
            "configured": configured,
            "action_ready": configured,
            "missing": [] if configured else ["NOTION_API_KEY"],
            "mode": "api_key",
            "detail": "Notion API ready." if configured else "Missing: NOTION_API_KEY",
            "action": action,
        }

    async def search(self, query: str) -> list[Evidence]:
        if not self._configured():
            return [Evidence(source="notion", title="Notion not configured", summary="Set NOTION_API_KEY env var", confidence=0.0)]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{NOTION_API}/search",
                    headers=self._headers(),
                    json={"query": query, "page_size": 10},
                )
                resp.raise_for_status()
                results = []
                for item in resp.json().get("results", []):
                    obj_type = item.get("object", "page")
                    title_parts = []
                    if obj_type == "page":
                        props = item.get("properties", {})
                        for prop in props.values():
                            if prop.get("type") == "title":
                                title_parts = [t.get("plain_text", "") for t in prop.get("title", [])]
                                break
                    elif obj_type == "database":
                        title_parts = [t.get("plain_text", "") for t in item.get("title", [])]
                    title = " ".join(title_parts) or f"Untitled {obj_type}"
                    results.append(Evidence(
                        source="notion",
                        title=title,
                        summary=f"Notion {obj_type} | Last edited: {item.get('last_edited_time', 'unknown')}",
                        url=item.get("url", ""),
                        confidence=0.70,
                    ))
                return results
        except Exception as e:
            return [Evidence(source="notion", title="Notion search failed", summary=str(e), confidence=0.0)]

    async def read(self, ref: str) -> dict[str, Any]:
        if not self._configured():
            return {"error": "Notion not configured"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{NOTION_API}/blocks/{ref}/children", headers=self._headers(), params={"page_size": 100})
                resp.raise_for_status()
                blocks = resp.json().get("results", [])
                text_parts = []
                for block in blocks:
                    block_type = block.get("type", "")
                    content = block.get(block_type, {})
                    rich_text = content.get("rich_text", [])
                    text = " ".join(t.get("plain_text", "") for t in rich_text)
                    if text:
                        text_parts.append(text)
                return {"ref": ref, "content": "\n".join(text_parts), "block_count": len(blocks)}
        except Exception as e:
            return {"ref": ref, "error": str(e)}

    async def write(self, name: str, content: str, metadata: dict[str, Any] | None = None) -> ActionResult:
        if not self._configured():
            return ActionResult(connector="notion", action="create_page", status="blocked", summary="Notion not configured")
        meta = metadata or {}
        parent_id = meta.get("parent_id", "")
        if not parent_id:
            return ActionResult(connector="notion", action="create_page", status="blocked", summary="parent_id required")
        body = {
            "parent": {"page_id": parent_id},
            "properties": {"title": {"title": [{"text": {"content": name}}]}},
            "children": [
                {"object": "block", "type": "paragraph", "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                }}
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{NOTION_API}/pages", headers=self._headers(), json=body)
                resp.raise_for_status()
                data = resp.json()
                return ActionResult(
                    connector="notion", action="create_page", status="complete",
                    summary=f"Created page: {name}", artifact_path=data.get("url", ""),
                )
        except Exception as e:
            return ActionResult(connector="notion", action="create_page", status="failed", summary=str(e))

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        if name != "create_page":
            return ActionResult(connector="notion", action=name, status="skipped", summary=f"Unknown action: {name}")
        return await self.write(
            payload.get("title", "AUTOPILOT Mission Brief"),
            payload.get("body", payload.get("content", "")),
            payload,
        )

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="notion_search",
                description="Search Notion pages and databases for runbooks, incident records, or knowledge.",
                parameters={"query": "Search query"},
                fn=self.search,
            )
        ]

