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

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from autopilot.agents.base import Tool
from autopilot.connectors.base import Connector
from autopilot.connectors.oauth import get_valid_token, github_configured, is_authorized
from autopilot.models import (
    ActionResult,
    ActionRisk,
    Capability,
    ConnectorManifest,
    ConnectorToolSpec,
    Evidence,
    Signal,
)
from autopilot.storage import DB_PATH

log = logging.getLogger("autopilot.connectors.github")

GITHUB_API = "https://api.github.com"
REPO_QUALIFIER = re.compile(r"\brepo:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b")
UNSUPPORTED_ISSUE_QUALIFIERS = re.compile(
    r"\b(commits?|commit|since:\S+|after:\S+|before:\S+|pushed:\S+|author:\S+|committer:\S+)\b|(?<!\w):\S+",
    re.IGNORECASE,
)
SEARCH_TOKEN = re.compile(r"[A-Za-z0-9_.-]+")


def _token() -> str | None:
    return os.getenv("GITHUB_TOKEN", "").strip() or None


class GitHubConnector(Connector):
    manifest = ConnectorManifest(
        name="github",
        description="Connects to GitHub: search issues/PRs, read repository context, create issues, post comments.",
        category="Engineering",
        auth_mode="api_key",
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
        scopes=["repo", "issues:read", "issues:write", "pull_requests:read", "contents:read"],
        objects=["repositories", "issues", "pull requests", "deployments", "commits", "workflow runs"],
        safe_actions=["create_issue", "post_comment"],
        tools=[
            ConnectorToolSpec(
                name="github_search_issues",
                description="Search GitHub issues and pull requests by query, optionally scoped to a repository.",
                capability=Capability.SEARCH,
                input_schema={"query": "Search terms", "repo": "Optional owner/repo scope"},
                output="Evidence[]",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="github_read_issue",
                description="Read issue, pull request, or repository content details by ref.",
                capability=Capability.READ,
                input_schema={"ref": "owner/repo/issues/123, owner/repo/pulls/456, or owner/repo/contents/path"},
                output="GitHub resource JSON",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="github_create_issue",
                description="Create a follow-up issue after policy approval.",
                capability=Capability.ACTION,
                input_schema={"repo": "owner/repo", "title": "Issue title", "body": "Markdown body", "labels": "Optional labels"},
                output="ActionResult",
                risk=ActionRisk.MEDIUM,
                requires_confirmation=True,
                mcp_tool=True,
            ),
        ],
        reliability_score=0.96,
        auth_required=True,
    )

    def _default_repo(self) -> str | None:
        return os.getenv("GITHUB_REPO", "").strip() or None

    def _db_path(self) -> Path:
        return DB_PATH

    def _authorized(self) -> bool:
        return (github_configured() and is_authorized("github", self._db_path())) or bool(_token())

    async def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = await get_valid_token("github", self._db_path()) if github_configured() else None
        if not token:
            token = _token()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def readiness(self, action: str | None = None) -> dict[str, Any]:
        has_pat = bool(_token())
        is_oauth_configured = github_configured()
        is_oauth_authorized = is_oauth_configured and is_authorized("github", self._db_path())

        authorized = is_oauth_authorized or has_pat
        write_action = action in {"create_issue", "post_comment"}
        missing = []
        if write_action and not authorized:
            missing.append("GitHub Authorization (required for write actions)")
        if write_action and not self._default_repo():
            missing.append("GITHUB_REPO (required for write actions)")

        mode = "oauth" if is_oauth_configured else ("authenticated" if has_pat else "anonymous_public")

        detail = "GitHub API ready (authenticated)." if authorized else "Anonymous public GitHub search active."
        if not is_oauth_configured and not has_pat:
            detail += " Set GITHUB_CLIENT_ID/SECRET for OAuth, or GITHUB_TOKEN for PAT."

        return {
            "configured": authorized,
            "read_ready": True,
            "action_ready": authorized and (not write_action or bool(self._default_repo())),
            "missing": missing if missing else ([] if authorized else ["GitHub OAuth or GITHUB_TOKEN (optional — enables private repos + write actions)"]),
            "mode": mode,
            "auth_url": "/oauth/authorize/github" if is_oauth_configured and not is_oauth_authorized else None,
            "detail": detail,
            "action": action,
            "integration_live": authorized,
        }

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
        """Search GitHub issues and PRs using the GitHub Search API.
        Works anonymously for public repos (60 req/hr). Token enables private repos.
        """
        results: list[Evidence] = []
        data: dict[str, Any] = {}
        candidates = self._search_candidates(query, repo)
        async with httpx.AsyncClient(timeout=15) as client:
            headers = await self._headers()
            for attempt, search_query in enumerate(candidates, start=1):
                try:
                    data = await self._search_issues(client, search_query, headers)
                    if attempt > 1:
                        log.info("GitHub search succeeded after fallback query: %s", search_query)
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 422 and attempt < len(candidates):
                        log.warning(
                            "GitHub search query rejected; retrying with safer query (%d/%d)",
                            attempt + 1,
                            len(candidates),
                        )
                        continue
                    log.error("GitHub search failed: %s", exc)
                    return [self._search_error_evidence(query, str(exc))]
                except (httpx.RequestError, json.JSONDecodeError, KeyError) as exc:
                    log.error("GitHub search failed: %s", exc)
                    return [self._search_error_evidence(query, str(exc))]

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

    def _search_error_evidence(self, query: str, detail: str) -> Evidence:
        """Distinguish 'search failed' from 'no matches' so agents don't read a
        network/API failure as negative evidence."""
        return Evidence(
            source="github",
            title="GitHub search unavailable",
            summary=f"Search for '{query[:80]}' failed: {detail}",
            confidence=0.0,
            metadata={"kind": "tool_error"},
        )

    async def _search_issues(self, client: httpx.AsyncClient, search_query: str, headers: dict) -> dict[str, Any]:
        resp = await client.get(
            f"{GITHUB_API}/search/issues",
            headers=headers,
            params={"q": search_query, "per_page": 5, "sort": "updated"},
        )
        resp.raise_for_status()
        return resp.json()

    def _search_candidates(self, query: str, repo: str | None = None) -> list[str]:
        """Progressively safer GitHub issue search queries.

        Agent-generated search strings often contain commit/date qualifiers that
        are valid elsewhere but rejected by GitHub's issue search. Try a scoped
        rich query first, then fall back to simple issue searches that GitHub
        consistently accepts for public anonymous search.
        """
        candidates: list[str] = []
        rich = self._build_search_query(query, repo)
        candidates.append(rich)

        unscoped = self._build_search_query(query, repo="", use_default=False, use_inline_repo=False)
        candidates.append(unscoped)

        terms = self._simple_terms(query)
        candidates.append(f"{terms} is:issue")
        if target := (repo or self._default_repo()):
            candidates.append(f"{terms} is:issue repo:{target}")

        seen: set[str] = set()
        final: list[str] = []
        for item in candidates:
            normalized = re.sub(r"\s+", " ", item).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                final.append(normalized)
        return final

    def _simple_terms(self, query: str) -> str:
        stripped = REPO_QUALIFIER.sub(" ", query or "")
        stripped = UNSUPPORTED_ISSUE_QUALIFIERS.sub(" ", stripped)
        tokens = [
            token
            for token in SEARCH_TOKEN.findall(stripped)
            if len(token) > 1 and ":" not in token
        ]
        return " ".join(tokens[:8]) or "incident"

    def _build_search_query(
        self,
        query: str,
        repo: str | None = None,
        use_default: bool = True,
        use_inline_repo: bool = True,
    ) -> str:
        target = repo or (self._default_repo() if use_default else None)
        inline_repo = REPO_QUALIFIER.search(query or "")
        if use_inline_repo and not target and inline_repo:
            target = inline_repo.group(0).split(":", 1)[1]
        terms = REPO_QUALIFIER.sub(" ", query or "")
        terms = UNSUPPORTED_ISSUE_QUALIFIERS.sub(" ", terms)
        terms = self._simple_terms(terms)
        parts = [terms, "in:title,body"]
        if target:
            parts.append(f"repo:{target}")
        return " ".join(parts)

    async def read(self, ref: str) -> dict[str, Any]:
        """Read a GitHub resource. ref formats:
          - "owner/repo/issues/123"
          - "owner/repo/pulls/123"
          - "owner/repo/contents/path/to/file"
        Works anonymously for public repos.
        """
        parts = ref.split("/")
        if len(parts) < 4:
            return {"error": f"Invalid GitHub ref format: {ref}. Expected owner/repo/type/id"}

        owner, repo, resource_type = parts[0], parts[1], parts[2]
        resource_id = "/".join(parts[3:])
        url = f"{GITHUB_API}/repos/{owner}/{repo}/{resource_type}/{resource_id}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=await self._headers())
                resp.raise_for_status()
                return resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as exc:
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
        if not self._authorized():
            return ActionResult(
                connector=self.manifest.name,
                action=name,
                status="skipped",
                summary="GitHub not authorized; skipping GitHub action.",
                metadata={"required_env": "GITHUB_CLIENT_ID or GITHUB_TOKEN"},
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
                    headers=await self._headers(),
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
                    headers=await self._headers(),
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
        ]
