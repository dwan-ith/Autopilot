"""Gmail — OAuth2 Connector.

Full Google OAuth2 implementation. Supports:
- Searching inbox threads
- Reading individual threads
- Drafting and sending replies (with human approval gate)

OAuth flow triggered via /oauth/authorize/gmail → Google consent → /oauth/callback/google
Tokens stored in connector_connections SQLite table.

Env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.connectors.oauth import (
    get_valid_token,
    google_configured,
    is_authorized,
)
from autopilot.models import (
    ActionResult,
    Capability,
    ConnectorManifest,
    ConnectorToolSpec,
    Evidence,
    Signal,
)
from autopilot.storage import DB_PATH

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CONNECTOR_ID = "gmail"


class GmailConnector(Connector):
    manifest = ConnectorManifest(
        name="gmail",
        description="Search inbox threads, read emails, draft replies, and send after approval. OAuth2.",
        category="Communication",
        auth_mode="oauth",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE, Capability.ACTION],
        scopes=["gmail.readonly", "gmail.modify", "gmail.send"],
        objects=["messages", "threads", "labels", "attachments"],
        event_types=["message.received", "thread.updated"],
        safe_actions=["draft_reply", "send_approved_reply"],
        tools=[
            ConnectorToolSpec(
                name="gmail_search_threads",
                description="Search Gmail threads by query.",
                capability=Capability.SEARCH,
                input_schema={"query": "Gmail search query", "max_results": "Maximum results"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="gmail_read_thread",
                description="Read a Gmail conversation thread.",
                capability=Capability.READ,
                input_schema={"thread_id": "Gmail thread id"},
                output="Thread JSON",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="gmail_draft_reply",
                description="Create a Gmail draft for human review.",
                capability=Capability.WRITE,
                input_schema={"thread_id": "Thread ID", "to": "Recipient", "subject": "Subject", "body": "Draft body"},
                output="ActionResult",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="gmail_send_approved_reply",
                description="Send an approved email message.",
                capability=Capability.ACTION,
                input_schema={"to": "Recipient", "subject": "Subject", "body": "Message body"},
                output="ActionResult",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.88,
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
                "integration_live": False,
            }
        authorized = self._authorized()
        return {
            "configured": authorized,
            "action_ready": authorized,
            "missing": [],
            "mode": "oauth",
            "auth_url": "/oauth/authorize/gmail" if not authorized else None,
            "detail": "Gmail OAuth2 connected." if authorized else "Not authorized — visit auth_url to connect.",
            "action": action,
            "integration_live": authorized,
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
                source="gmail", title="Gmail not authorized",
                summary="Visit /oauth/authorize/gmail to connect.",
                confidence=0.0,
            )]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{GMAIL_API}/threads",
                    headers=headers,
                    params={"q": query, "maxResults": 8},
                )
                resp.raise_for_status()
                threads = resp.json().get("threads", [])
                results = []
                for t in threads[:5]:
                    thread_resp = await client.get(
                        f"{GMAIL_API}/threads/{t['id']}",
                        headers=headers,
                        params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                    )
                    if thread_resp.status_code == 200:
                        thread = thread_resp.json()
                        headers_list = thread.get("messages", [{}])[0].get("payload", {}).get("headers", [])
                        h = {h["name"]: h["value"] for h in headers_list}
                        results.append(Evidence(
                            source="gmail",
                            title=h.get("Subject", "No subject"),
                            summary=f"From: {h.get('From', '?')} | Date: {h.get('Date', '?')} | {len(thread.get('messages', []))} messages",
                            url=f"https://mail.google.com/mail/u/0/#thread/{t['id']}",
                            confidence=0.75,
                        ))
                return results
        except (httpx.RequestError, httpx.HTTPStatusError, KeyError, ValueError) as e:
            return [Evidence(source="gmail", title="Gmail search failed", summary=str(e), confidence=0.0)]

    async def read(self, ref: str) -> dict[str, Any]:
        headers = await self._headers()
        if not headers:
            return {"error": "Gmail not authorized"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{GMAIL_API}/threads/{ref}",
                    headers=headers,
                    params={"format": "full"},
                )
                resp.raise_for_status()
                thread = resp.json()
                messages = []
                for msg in thread.get("messages", [])[:5]:
                    payload = msg.get("payload", {})
                    hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}
                    body = ""
                    # Extract plain text body
                    if payload.get("mimeType") == "text/plain":
                        data = payload.get("body", {}).get("data", "")
                        if data:
                            body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    else:
                        for part in payload.get("parts", []):
                            if part.get("mimeType") == "text/plain":
                                data = part.get("body", {}).get("data", "")
                                if data:
                                    body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                                    break
                    messages.append({
                        "from": hdrs.get("From", ""),
                        "subject": hdrs.get("Subject", ""),
                        "date": hdrs.get("Date", ""),
                        "body": body[:800],
                    })
                return {"thread_id": ref, "message_count": len(thread.get("messages", [])), "messages": messages}
        except Exception as e:
            return {"ref": ref, "error": str(e)}

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        if name in {"draft_reply", "gmail_draft_reply"}:
            return await self._draft_reply(payload)
        if name in {"send_approved_reply", "gmail_send_approved_reply", "gmail_send_message"}:
            return await self._send(payload, action_name=name)
        return ActionResult(connector="gmail", action=name, status="skipped", summary=f"Unknown action: {name}")

    def _raw_email(self, payload: dict[str, Any]) -> str:
        headers = [
            f"To: {payload.get('to', '')}",
            f"Subject: {payload.get('subject', 'AUTOPILOT notification')}",
            "Content-Type: text/plain; charset=utf-8",
        ]
        raw_email = "\r\n".join(headers) + "\r\n\r\n" + payload.get("body", "")
        return base64.urlsafe_b64encode(raw_email.encode()).decode().rstrip("=")

    async def _draft_reply(self, payload: dict[str, Any]) -> ActionResult:
        headers = await self._headers()
        if not headers:
            return ActionResult(connector="gmail", action="draft_reply", status="blocked", summary="Gmail not authorized")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{GMAIL_API}/drafts",
                    headers=headers,
                    json={"message": {"raw": self._raw_email(payload), "threadId": payload.get("thread_id")}},
                )
                resp.raise_for_status()
                draft = resp.json()
                return ActionResult(
                    connector="gmail",
                    action="draft_reply",
                    status="complete",
                    summary=f"Created Gmail draft {draft.get('id', '')}.",
                    metadata={"draft_id": draft.get("id"), "thread_id": payload.get("thread_id")},
                )
        except Exception as e:
            return ActionResult(connector="gmail", action="draft_reply", status="failed", summary=str(e))

    async def _send(self, payload: dict[str, Any], action_name: str = "send_approved_reply") -> ActionResult:
        headers = await self._headers()
        if not headers:
            return ActionResult(connector="gmail", action=action_name, status="blocked", summary="Gmail not authorized")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if payload.get("draft_id"):
                    resp = await client.post(
                        f"{GMAIL_API}/drafts/send",
                        headers=headers,
                        json={"id": payload["draft_id"]},
                    )
                else:
                    resp = await client.post(
                        f"{GMAIL_API}/messages/send",
                        headers=headers,
                        json={"raw": self._raw_email(payload), "threadId": payload.get("thread_id")},
                    )
                resp.raise_for_status()
                msg_id = resp.json().get("id", "")
                return ActionResult(
                    connector="gmail", action=action_name, status="complete",
                    summary=f"Sent approved Gmail message (id={msg_id})",
                    metadata={"message_id": msg_id, "draft_id": payload.get("draft_id")},
                )
        except Exception as e:
            return ActionResult(connector="gmail", action=action_name, status="failed", summary=str(e))

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        msg = payload.get("message", {})
        return Signal(
            source="gmail",
            type="message.received",
            summary=f"Gmail: {payload.get('subject', 'New email')}",
            entities=[payload.get("from", ""), payload.get("to", "")],
            urgency="low",
            payload=payload,
        )

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="gmail_search_threads",
                description=(
                    "Search Gmail inbox for relevant email threads. "
                    "Use to find customer escalations, support tickets, or team communications. "
                    "Requires Gmail OAuth authorization."
                ),
                parameters={"query": "Gmail search query (e.g. 'subject:incident from:customer')"},
                fn=self.search,
            ),
            Tool(
                name="gmail_read_thread",
                description="Read the messages in an authorized Gmail thread.",
                parameters={"ref": "Gmail thread id"},
                fn=self.read,
            ),
        ]

