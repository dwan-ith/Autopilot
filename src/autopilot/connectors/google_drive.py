"""Google Drive — OAuth2 Connector.

Full Google OAuth2 implementation. Supports:
- Searching Drive files
- Reading file content (Docs, Sheets exported as text, PDFs)
- Creating mission brief documents

OAuth flow triggered via /oauth/authorize/google_drive → Google consent → /oauth/callback/google
Tokens shared with Gmail via the same connector_connections row (connector_id="google_drive").

Env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.connectors.oauth import (
    SCOPES_DRIVE,
    build_google_auth_url,
    get_valid_token,
    google_configured,
    is_authorized,
)
from autopilot.models import ActionResult, Capability, ConnectorManifest, ConnectorToolSpec, Evidence
from autopilot.storage import DB_PATH

DRIVE_API = "https://www.googleapis.com/drive/v3"
DOCS_API = "https://docs.googleapis.com/v1/documents"
CONNECTOR_ID = "google_drive"

EXPORT_MIME_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


class GoogleDriveConnector(Connector):
    manifest = ConnectorManifest(
        name="google_drive",
        description="Search Drive files, read content, and publish reviewed mission briefs. OAuth2.",
        category="Knowledge",
        auth_mode="oauth",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE],
        scopes=["drive.readonly", "drive.file"],
        objects=["files", "folders", "docs", "sheets"],
        safe_actions=["create_doc", "append_to_doc"],
        tools=[
            ConnectorToolSpec(
                name="drive_search_files",
                description="Search Drive files by query.",
                capability=Capability.SEARCH,
                input_schema={"query": "Drive search query", "mime_type": "Optional mime type filter"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="drive_read_file",
                description="Read a Drive file's text content.",
                capability=Capability.READ,
                input_schema={"file_id": "Drive file id"},
                output="File content",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="drive_create_doc",
                description="Create a reviewed mission brief Google Doc.",
                capability=Capability.WRITE,
                input_schema={"title": "Document title", "body": "Document body"},
                output="ActionResult",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.85,
    )

    def _db_path(self) -> Path:
        return DB_PATH

    def _authorized(self) -> bool:
        return google_configured() and is_authorized(CONNECTOR_ID, self._db_path())

    def readiness(self, action: str | None = None) -> dict:
        if not google_configured():
            return {
                "configured": False, "action_ready": False,
                "missing": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
                "mode": "oauth",
                "detail": "Google OAuth2 credentials not configured.",
                "action": action,
            }
        authorized = self._authorized()
        return {
            "configured": authorized,
            "action_ready": authorized,
            "missing": [],
            "mode": "oauth",
            "auth_url": build_google_auth_url(state="google_drive", scopes=SCOPES_DRIVE) if not authorized else None,
            "detail": "Google Drive OAuth2 connected." if authorized else "Not authorized — visit auth_url to connect.",
            "action": action,
        }

    async def _headers(self) -> dict[str, str] | None:
        token = await get_valid_token(CONNECTOR_ID, self._db_path())
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def search(self, query: str) -> list[Evidence]:
        headers = await self._headers()
        if not headers:
            return [Evidence(
                source="google_drive", title="Google Drive not authorized",
                summary=f"Visit /oauth/authorize/google_drive to connect.",
                confidence=0.0,
            )]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                params: dict[str, Any] = {
                    "q": f"fullText contains '{query}' and trashed = false",
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
                    "pageSize": 10,
                    "orderBy": "relevance",
                }
                resp = await client.get(f"{DRIVE_API}/files", headers=headers, params=params)
                resp.raise_for_status()
                results = []
                for f in resp.json().get("files", []):
                    size_kb = int(f.get("size", 0)) // 1024 if f.get("size") else 0
                    mime = f.get("mimeType", "")
                    kind = "Google Doc" if "document" in mime else "Sheet" if "spreadsheet" in mime else "File"
                    results.append(Evidence(
                        source="google_drive",
                        title=f.get("name", "Untitled"),
                        summary=f"{kind} | Modified: {f.get('modifiedTime', '?')[:10]} | {size_kb} KB",
                        url=f.get("webViewLink", ""),
                        confidence=0.72,
                    ))
                return results
        except Exception as e:
            return [Evidence(source="google_drive", title="Drive search failed", summary=str(e), confidence=0.0)]

    async def read(self, ref: str) -> dict[str, Any]:
        """Read file content by file_id. Exports Google Docs as plain text."""
        headers = await self._headers()
        if not headers:
            return {"error": "Google Drive not authorized"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Get file metadata first
                meta_resp = await client.get(
                    f"{DRIVE_API}/files/{ref}",
                    headers=headers,
                    params={"fields": "id,name,mimeType,size"},
                )
                meta_resp.raise_for_status()
                meta = meta_resp.json()
                mime = meta.get("mimeType", "")

                if mime in EXPORT_MIME_MAP:
                    # Export Google Workspace files as plain text
                    export_resp = await client.get(
                        f"{DRIVE_API}/files/{ref}/export",
                        headers=headers,
                        params={"mimeType": EXPORT_MIME_MAP[mime]},
                    )
                    export_resp.raise_for_status()
                    content = export_resp.text[:5000]
                else:
                    # Download raw file (text, markdown, etc.)
                    dl_resp = await client.get(
                        f"{DRIVE_API}/files/{ref}",
                        headers=headers,
                        params={"alt": "media"},
                    )
                    dl_resp.raise_for_status()
                    content = dl_resp.text[:5000]

                return {
                    "file_id": ref,
                    "name": meta.get("name", ""),
                    "mime_type": mime,
                    "content": content,
                }
        except Exception as e:
            return {"file_id": ref, "error": str(e)}

    async def write(self, name: str, content: str, metadata: dict[str, Any] | None = None) -> ActionResult:
        """Create a Google Doc with the given title and plain-text body."""
        headers = await self._headers()
        if not headers:
            return ActionResult(connector="google_drive", action="create_doc", status="blocked", summary="Not authorized")

        # Use Google Docs API to create the document
        docs_headers = {**headers, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # 1. Create empty doc
                create_resp = await client.post(
                    "https://docs.googleapis.com/v1/documents",
                    headers=docs_headers,
                    json={"title": name},
                )
                create_resp.raise_for_status()
                doc = create_resp.json()
                doc_id = doc["documentId"]

                # 2. Insert body text via batchUpdate
                update_resp = await client.post(
                    f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                    headers=docs_headers,
                    json={
                        "requests": [{
                            "insertText": {
                                "location": {"index": 1},
                                "text": content[:50000],
                            }
                        }]
                    },
                )
                update_resp.raise_for_status()

                doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
                return ActionResult(
                    connector="google_drive", action="create_doc", status="complete",
                    summary=f"Created doc: {name}", artifact_path=doc_url,
                )
        except Exception as e:
            return ActionResult(connector="google_drive", action="create_doc", status="failed", summary=str(e))
