from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from autopilot.agents.base import BaseAgent
from autopilot.models import AgentTask, ActionRecord, AgentType
from autopilot.config import settings

logger = logging.getLogger(__name__)


class GitHubAgent(BaseAgent):
    agent_type = AgentType.GITHUB

    GITHUB_API: str = settings.GITHUB_API_URL

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def handle(self, task: AgentTask) -> ActionRecord:
        token = settings.GITHUB_TOKEN
        if not token:
            logger.warning("GitHubAgent: missing GITHUB_TOKEN for task %s", task.id)
            return ActionRecord(
                agent_type=self.agent_type,
                platform="github",
                action_type="handle_task",
                payload=task.payload,
                result={"status": "failed", "reason": "missing GITHUB_TOKEN"},
                created_at=datetime.now(timezone.utc),
            )

        command = (task.payload or {}).get("command")
        repo = (task.payload or {}).get("repo")

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                if command == "list_prs":
                    if not repo:
                        return ActionRecord(
                            agent_type=self.agent_type,
                            platform="github",
                            action_type="list_prs",
                            payload=task.payload,
                            result={"status": "failed", "reason": "repo missing"},
                            created_at=datetime.now(timezone.utc),
                        )
                    url = f"{self.GITHUB_API}/repos/{repo}/pulls?state=open"
                    resp = await client.get(url, headers=self._headers(token))
                    resp.raise_for_status()
                    prs = resp.json()
                    summary = [
                        {
                            "number": p.get("number"),
                            "title": p.get("title"),
                            "user": p.get("user", {}).get("login"),
                        }
                        for p in prs
                    ]
                    return ActionRecord(
                        agent_type=self.agent_type,
                        platform="github",
                        action_type="list_prs",
                        payload=task.payload,
                        result={"status": "ok", "prs": summary},
                        created_at=datetime.now(timezone.utc),
                    )

                if command == "create_pr_comment":
                    pr_number = (task.payload or {}).get("pr_number")
                    body = (task.payload or {}).get("body")
                    if not repo or not pr_number or not body:
                        return ActionRecord(
                            agent_type=self.agent_type,
                            platform="github",
                            action_type="create_pr_comment",
                            payload=task.payload,
                            result={
                                "status": "failed",
                                "reason": "repo/pr_number/body missing",
                            },
                            created_at=datetime.now(timezone.utc),
                        )
                    url = f"{self.GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
                    resp = await client.post(
                        url, headers=self._headers(token), json={"body": body}
                    )
                    resp.raise_for_status()
                    comment = resp.json()
                    return ActionRecord(
                        agent_type=self.agent_type,
                        platform="github",
                        action_type="create_pr_comment",
                        payload=task.payload,
                        result={"status": "ok", "comment_id": comment.get("id")},
                        created_at=datetime.now(timezone.utc),
                    )

                # default behavior: inspect signal, try to annotate matching PRs by commit/ref if present
                # not implemented: return a no-op record
                return ActionRecord(
                    agent_type=self.agent_type,
                    platform="github",
                    action_type="noop",
                    payload=task.payload,
                    result={"status": "noop"},
                    created_at=datetime.now(timezone.utc),
                )

            except httpx.HTTPStatusError as exc:
                logger.exception("GitHub API error for task %s: %s", task.id, exc)
                return ActionRecord(
                    agent_type=self.agent_type,
                    platform="github",
                    action_type="error",
                    payload=task.payload,
                    result={
                        "status": "failed",
                        "http_status": exc.response.status_code,
                        "text": exc.response.text,
                    },
                    created_at=datetime.now(timezone.utc),
                )
            except Exception as exc:  # pragma: no cover - network/runtime
                logger.exception("GitHubAgent unexpected error for task %s", task.id)
                return ActionRecord(
                    agent_type=self.agent_type,
                    platform="github",
                    action_type="error",
                    payload=task.payload,
                    result={"status": "failed", "error": str(exc)},
                    created_at=datetime.now(timezone.utc),
                )

    async def health_check(self) -> bool:
        token = settings.GITHUB_TOKEN
        if not token:
            return False
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get(
                    f"{self.GITHUB_API}/user", headers=self._headers(token)
                )
                return resp.status_code == 200
            except Exception:
                return False
