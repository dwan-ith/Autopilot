"""Real GitHub connector — talks to the actual GitHub REST API.

Capabilities:
  - SEARCH: search issues, PRs, code
  - READ: get issue details, PR details, file contents
  - WRITE: create issues, post comments
  - ACTION: create issue (safe-declared)

Requires: GITHUB_TOKEN env var (Personal Access Token or GitHub App token)
Optional: GITHUB_REPO env var (default repo, e.g. "owner/repo")
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from autopilot.agents.base import Tool
from autopilot.connectors.base import Connector
from autopilot.models import (
    ActionResult,
    Capability,
    ConnectorManifest,
    Evidence,
    Signal,
)

log = logging.getLogger("autopilot.connectors.github")

GITHUB_API = "https://api.github.com"


def _token() -> str | None:
    return os.getenv("GITHUB_TOKEN", "").strip() or None


def _headers() -> dict[str, str]:
    token = _token()
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class GitHubConnector(Connector):
    manifest = ConnectorManifest(
        name="github",
        description="Connects to GitHub: search issues/PRs, read repository context, create issues, post comments.",
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE, Capability.ACTION],
        event_types=[
            "issues.opened",
            "issues.closed",
            "pull_request.opened",
            "pull_request.merged",
            "deployment.created",
            "push",
            "workflow_run.completed",
        ],
        safe_actions=["create_issue", "post_comment"],
        reliability_score=0.96,
        auth_required=True,
    )

    def _default_repo(self) -> str | None:
        return os.getenv("GITHUB_REPO", "").strip() or None

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        """Normalize a real GitHub webhook payload into a Signal."""
        event_type = payload.get("action", "push")
        repo = (payload.get("repository") or {}).get("full_name", "unknown/repo")

        # Issue events
        issue = payload.get("issue") or {}
        pr = payload.get("pull_request") or {}
        deployment = payload.get("deployment") or {}
        workflow_run = payload.get("workflow_run") or {}

        if issue:
            title = issue.get("title", "GitHub issue event")
            labels = [lb["name"] for lb in issue.get("labels", []) if isinstance(lb, dict)]
            assignees = [a["login"] for a in issue.get("assignees", [])]
            entities = [repo, *labels, *assignees]
            urgency = "high" if any(lb in {"bug", "critical", "p0", "incident"} for lb in labels) else "medium"
            return Signal(
                source="github",
                type=f"issues.{event_type}",
                summary=f"[{repo}] Issue #{issue.get('number')}: {title}",
                entities=entities,
                urgency=urgency,
                payload=payload,
            )

        if pr:
            title = pr.get("title", "PR event")
            entities = [repo, pr.get("head", {}).get("ref", ""), pr.get("user", {}).get("login", "")]
            entities = [e for e in entities if e]
            return Signal(
                source="github",
                type=f"pull_request.{event_type}",
                summary=f"[{repo}] PR #{pr.get('number')}: {title}",
                entities=entities,
                urgency="medium",
                payload=payload,
            )

        if deployment:
            env = deployment.get("environment", "production")
            return Signal(
                source="github",
                type="deployment.created",
                summary=f"[{repo}] Deployment to {env}: {deployment.get('description', '')}",
                entities=[repo, env],
                urgency="medium",
                payload=payload,
            )

        if workflow_run:
            conclusion = workflow_run.get("conclusion", "")
            urgency = "high" if conclusion == "failure" else "low"
            return Signal(
                source="github",
                type="workflow_run.completed",
                summary=f"[{repo}] Workflow '{workflow_run.get('name')}' {conclusion}",
                entities=[repo, workflow_run.get("name", "")],
                urgency=urgency,
                payload=payload,
            )

        # Generic push / other
        pusher = (payload.get("pusher") or {}).get("name", "unknown")
        ref = payload.get("ref", "")
        commits = payload.get("commits", [])
        summary = f"[{repo}] Push to {ref} by {pusher}: {len(commits)} commit(s)"
        if commits:
            summary += f" — {commits[0].get('message', '')[:80]}"
        return Signal(
            source="github",
            type="push",
            summary=summary,
            entities=[repo, pusher, ref],
            urgency="low",
            payload=payload,
        )

    async def search(self, query: str, repo: str | None = None) -> list[Evidence]:
        """Search GitHub issues and PRs using the GitHub Search API."""
        token = _token()
        if not token:
            return [Evidence(
                source="github",
                title="GitHub not configured",
                summary="Set GITHUB_TOKEN to enable GitHub issue search.",
                confidence=0.0,
                metadata={"kind": "config_error"},
            )]

        target = repo or self._default_repo()
        search_query = f"{query} in:title,body"
        if target:
            search_query += f" repo:{target}"

        results: list[Evidence] = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{GITHUB_API}/search/issues",
                    headers=_headers(),
                    params={"q": search_query, "per_page": 5, "sort": "updated"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.error("GitHub search failed: %s", exc)
            return [Evidence(
                source="github",
                title="GitHub search failed",
                summary=str(exc),
                confidence=0.1,
                metadata={"kind": "api_error"},
            )]

        for item in data.get("items", []):
            results.append(Evidence(
                source="github",
                title=f"#{item['number']}: {item.get('title', '')}",
                summary=item.get("body", "")[:500] or "No description.",
                url=item.get("html_url"),
                confidence=0.75,
                metadata={
                    "kind": "github_issue",
                    "state": item.get("state"),
                    "labels": [lb["name"] for lb in item.get("labels", [])],
                    "comments": item.get("comments", 0),
                    "repo": item.get("repository_url", "").replace(f"{GITHUB_API}/repos/", ""),
                },
            ))
        return results

    async def read(self, ref: str) -> dict[str, Any]:
        """Read a GitHub resource. ref formats:
          - "owner/repo/issues/123"
          - "owner/repo/pulls/123"
          - "owner/repo/contents/path/to/file"
        """
        token = _token()
        if not token:
            return {"error": "GITHUB_TOKEN not set"}

        parts = ref.split("/")
        if len(parts) < 4:
            return {"error": f"Invalid GitHub ref format: {ref}. Expected owner/repo/type/id"}

        owner, repo, resource_type = parts[0], parts[1], parts[2]
        resource_id = "/".join(parts[3:])
        url = f"{GITHUB_API}/repos/{owner}/{repo}/{resource_type}/{resource_id}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=_headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.error("GitHub read failed for %s: %s", ref, exc)
            return {"error": str(exc)}

    async def write(self, name: str, content: str, metadata: dict | None = None) -> ActionResult:
        """Create a GitHub issue or post a comment."""
        meta = metadata or {}
        repo = meta.get("repo") or self._default_repo()
        if not repo:
            return ActionResult(
                connector=self.manifest.name,
                action="create_issue",
                status="skipped",
                summary="GITHUB_REPO not configured; no issue created.",
                metadata={"required_env": "GITHUB_REPO"},
            )
        return await self.action("create_issue", {"repo": repo, "title": name, "body": content})

    async def action(self, name: str, payload: dict) -> ActionResult:
        token = _token()
        if not token:
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="skipped",
                summary="GITHUB_TOKEN not configured; skipping GitHub action.",
                metadata={"required_env": "GITHUB_TOKEN"},
            )

        if name == "create_issue":
            return await self._create_issue(payload)
        if name == "post_comment":
            return await self._post_comment(payload)

        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="failed",
            summary=f"Unknown GitHub action: {name}",
            metadata={},
        )

    async def _create_issue(self, payload: dict) -> ActionResult:
        repo = payload.get("repo") or self._default_repo()
        if not repo:
            return ActionResult(
                connector=self.manifest.name,
                action="create_issue",
                status="skipped",
                summary="No repo specified. Set GITHUB_REPO or pass repo in payload.",
                metadata={},
            )

        body = {
            "title": payload.get("title", "AUTOPILOT mission follow-up"),
            "body": payload.get("body", payload.get("description", "")),
        }
        if payload.get("labels"):
            body["labels"] = payload["labels"]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{GITHUB_API}/repos/{repo}/issues",
                    headers=_headers(),
                    json=body,
                )
                resp.raise_for_status()
                issue = resp.json()
            return ActionResult(
                connector=self.manifest.name,
                action="create_issue",
                status="complete",
                summary=f"Created GitHub issue #{issue['number']}: {issue['title']}",
                metadata={
                    "url": issue["html_url"],
                    "number": issue["number"],
                    "repo": repo,
                },
            )
        except Exception as exc:
            log.error("GitHub create_issue failed: %s", exc)
            return ActionResult(
                connector=self.manifest.name,
                action="create_issue",
                status="failed",
                summary=f"Failed to create GitHub issue: {exc}",
                metadata={"repo": repo},
            )

    async def _post_comment(self, payload: dict) -> ActionResult:
        repo = payload.get("repo") or self._default_repo()
        issue_number = payload.get("issue_number")
        if not repo or not issue_number:
            return ActionResult(
                connector=self.manifest.name,
                action="post_comment",
                status="skipped",
                summary="repo and issue_number are required.",
                metadata={},
            )

        body = {"body": payload.get("body", "")}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments",
                    headers=_headers(),
                    json=body,
                )
                resp.raise_for_status()
                comment = resp.json()
            return ActionResult(
                connector=self.manifest.name,
                action="post_comment",
                status="complete",
                summary=f"Posted comment on #{issue_number} in {repo}",
                metadata={"url": comment.get("html_url"), "comment_id": comment.get("id")},
            )
        except Exception as exc:
            log.error("GitHub post_comment failed: %s", exc)
            return ActionResult(
                connector=self.manifest.name,
                action="post_comment",
                status="failed",
                summary=f"Failed to post GitHub comment: {exc}",
                metadata={},
            )

    # ── Tool factory for sub-agents ──────────────────────────────────────

    def as_tools(self) -> list[Tool]:
        """Expose GitHub capabilities as sub-agent tools."""
        return [
            Tool(
                name="github_search_issues",
                description="Search GitHub issues and PRs for context about a problem.",
                parameters={
                    "query": "Search terms (e.g. 'export failure pipeline')",
                    "repo": "(optional) 'owner/repo' to scope search",
                },
                fn=self.search,
            ),
            Tool(
                name="github_read_issue",
                description="Read the full details of a GitHub issue or PR.",
                parameters={
                    "ref": "Format: 'owner/repo/issues/123' or 'owner/repo/pulls/456'",
                },
                fn=self.read,
            ),
            Tool(
                name="github_create_issue",
                description="Create a new GitHub issue as a follow-up action.",
                parameters={
                    "repo": "owner/repo",
                    "title": "Issue title",
                    "body": "Issue body in Markdown",
                    "labels": "(optional) list of label names",
                },
                fn=lambda **kw: self.action("create_issue", kw),
            ),
        ]
