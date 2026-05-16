from __future__ import annotations

from typing import Any

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, Signal


class SecurityAuditConnector(Connector):
    manifest = ConnectorManifest(
        name="security_audit",
        description="PR-open security audit trigger with an artifact-plus-alert output contract.",
        capabilities=[Capability.READ],
        event_types=["pull_request.opened", "security_audit.scheduled"],
        safe_actions=[],
        reliability_score=0.84,
    )

    def readiness(self, action: str | None = None) -> dict:
        return {
            "configured": True, "action_ready": True, "missing": [],
            "mode": "webhook", "detail": "Security audit webhook receiver is active.", "action": action,
            "integration_live": False,
        }

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        action = str(payload.get("action", "opened"))
        pull_request = payload.get("pull_request", {}) if isinstance(payload.get("pull_request"), dict) else {}
        repository = payload.get("repository", {}) if isinstance(payload.get("repository"), dict) else {}
        repo_name = repository.get("full_name") or repository.get("name") or payload.get("repo") or "unknown repo"
        pr_number = pull_request.get("number") or payload.get("number") or payload.get("pr")
        pr_title = pull_request.get("title") or payload.get("title") or "Pull request opened"
        branch = (pull_request.get("head") or {}).get("ref") if isinstance(pull_request.get("head"), dict) else None

        return Signal(
            source=self.manifest.name,
            type="pull_request.opened" if action == "opened" else "security_audit.scheduled",
            summary=f"Security audit requested for {repo_name} PR {pr_number or 'unknown'}: {pr_title}",
            entities=[str(item) for item in [repo_name, branch, f"PR {pr_number}" if pr_number else None] if item],
            urgency=str(payload.get("urgency", "medium")),
            payload={
                "trigger": "pr_open" if action == "opened" else "schedule",
                "output_contract": "write local audit artifact and emit Slack notification when configured",
                **payload,
            },
        )
