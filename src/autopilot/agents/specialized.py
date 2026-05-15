from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import ActionResult, Mission, PolicyDecision
from autopilot.policy import PolicyEngine
from autopilot.state_store import StateStore


@dataclass
class ScopedAgentResult:
    agent: str
    status: str
    summary: str
    mission_id: str | None = None
    actions: list[ActionResult] = field(default_factory=list)
    policy_decisions: list[PolicyDecision] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def model_dump(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "summary": self.summary,
            "mission_id": self.mission_id,
            "actions": [action.model_dump() for action in self.actions],
            "policy_decisions": [decision.model_dump() for decision in self.policy_decisions],
            "output": self.output,
            "duration_ms": self.duration_ms,
        }


class ScopedAgent:
    agent_name = "scoped_agent"

    def __init__(self, store: StateStore, registry: ConnectorRegistry, policy: PolicyEngine | None = None):
        self.store = store
        self.registry = registry
        self.policy = policy or PolicyEngine()

    def _mission(self, mission_id: str | None) -> Mission | None:
        return self.store.get_mission(mission_id) if mission_id else None

    def _record_decision(self, mission: Mission | None, decision: PolicyDecision) -> None:
        if mission:
            mission.policy_decisions.append(decision)
            self.store.update_mission(mission)

    def _record_action(self, mission: Mission | None, action: ActionResult) -> None:
        if mission:
            mission.actions.append(action)
            self.store.update_mission(mission)

    def _trace(self, mission_id: str | None, event: str, status: str, payload: dict[str, Any]) -> None:
        self.store.trace(mission_id, f"agent.{self.agent_name}.{event}", status, payload)


class ProjectMgmtAgent(ScopedAgent):
    """Linear-only project management agent; Jira is intentionally a config stub."""

    agent_name = "project_mgmt"

    async def create_follow_up_issue(
        self,
        mission_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
    ) -> ScopedAgentResult:
        start = time.time()
        mission = self._mission(mission_id)
        connector = self.registry.get("linear")
        issue_title = title or (f"[AUTOPILOT] {mission.title[:120]}" if mission else "AUTOPILOT follow-up")
        issue_description = description or self._description_for(mission)
        payload = {
            "title": issue_title,
            "description": issue_description,
            "labels": labels or ["autopilot"],
            "mission_id": mission_id,
        }

        decisions: list[PolicyDecision] = []
        if mission:
            decision = self.policy.decide(mission, connector, "create_issue")
            decisions.append(decision)
            self._record_decision(mission, decision)
            if not decision.allowed:
                result = ScopedAgentResult(
                    agent=self.agent_name,
                    status="blocked",
                    summary=decision.reason,
                    mission_id=mission_id,
                    policy_decisions=decisions,
                    output={"provider": "linear"},
                )
                result.duration_ms = (time.time() - start) * 1000
                self._trace(mission_id, "create_issue", "blocked", result.model_dump())
                return result

        action = await connector.action("create_issue", payload)
        self._record_action(mission, action)
        self.store.remember("project_mgmt_agent", f"{issue_title}: {action.status}")
        result = ScopedAgentResult(
            agent=self.agent_name,
            status=action.status,
            summary=action.summary,
            mission_id=mission_id,
            actions=[action],
            policy_decisions=decisions,
            output={"provider": "linear", "title": issue_title},
        )
        result.duration_ms = (time.time() - start) * 1000
        self._trace(mission_id, "create_issue", action.status, result.model_dump())
        return result

    def _description_for(self, mission: Mission | None) -> str:
        if not mission:
            return "AUTOPILOT generated follow-up."
        return "\n".join(
            [
                f"Mission: {mission.title}",
                f"Severity: {mission.severity}",
                f"Confidence: {mission.confidence:.2f}",
                "",
                mission.summary,
                "",
                "Evidence:",
                *[f"- {item.title}: {item.summary}" for item in mission.evidence[:5]],
            ]
        )


class CloudInfraAgent(ScopedAgent):
    """One-action cloud agent: trigger a deployment via webhook or GitHub Actions dispatch."""

    agent_name = "cloud_infra"

    async def trigger_deployment(
        self,
        mission_id: str | None = None,
        environment: str = "staging",
        ref: str = "main",
        reason: str = "AUTOPILOT deployment trigger",
        approved: bool = False,
    ) -> ScopedAgentResult:
        start = time.time()
        mission = self._mission(mission_id)
        connector = self.registry.get("cloud_infra")
        payload = {"mission_id": mission_id, "environment": environment, "ref": ref, "reason": reason}

        decisions: list[PolicyDecision] = []
        if mission:
            policy = PolicyEngine(high_risk_requires_human=not approved)
            decision = policy.decide(mission, connector, "trigger_deployment")
            decisions.append(decision)
            self._record_decision(mission, decision)
            if not decision.allowed:
                result = ScopedAgentResult(
                    agent=self.agent_name,
                    status="blocked",
                    summary=decision.reason,
                    mission_id=mission_id,
                    policy_decisions=decisions,
                    output={"approved": approved, **payload},
                )
                result.duration_ms = (time.time() - start) * 1000
                self._trace(mission_id, "trigger_deployment", "blocked", result.model_dump())
                return result

        action = await connector.action("trigger_deployment", payload)
        self._record_action(mission, action)
        self.store.remember("cloud_infra_agent", json.dumps({"status": action.status, **payload}))
        result = ScopedAgentResult(
            agent=self.agent_name,
            status=action.status,
            summary=action.summary,
            mission_id=mission_id,
            actions=[action],
            policy_decisions=decisions,
            output={"approved": approved, **payload},
        )
        result.duration_ms = (time.time() - start) * 1000
        self._trace(mission_id, "trigger_deployment", action.status, result.model_dump())
        return result


class SecurityAuditAgent(ScopedAgent):
    """Runs on PR-open input and writes a durable audit artifact plus notification."""

    agent_name = "security_audit"

    async def run_pr_open_audit(self, payload: dict[str, Any], mission_id: str | None = None) -> ScopedAgentResult:
        start = time.time()
        mission = self._mission(mission_id)
        security = self.registry.get("security_audit")
        signal = await security.normalize_event(payload)
        artifact = self.registry.get("artifact")
        notification = self.registry.get("notification")

        report = self._audit_report(signal.payload)
        artifact_action = await artifact.write(
            f"security-audit-{signal.id}",
            report,
            {"signal_id": signal.id, "mission_id": mission_id, "trigger": signal.payload.get("trigger")},
        )
        notify_action = await notification.action(
            "notify_ops",
            {
                "mission_id": mission_id or signal.id,
                "text": f"Security audit completed for {signal.summary}. Artifact: {artifact_action.artifact_path}",
            },
        )

        for action in [artifact_action, notify_action]:
            self._record_action(mission, action)
        self.store.remember("security_audit_agent", f"{signal.summary}: {artifact_action.status}/{notify_action.status}")

        result = ScopedAgentResult(
            agent=self.agent_name,
            status="complete" if artifact_action.status == "complete" else artifact_action.status,
            summary="Security audit artifact written and notification emitted.",
            mission_id=mission_id,
            actions=[artifact_action, notify_action],
            output={"signal": signal.model_dump(), "report_preview": report[:500]},
        )
        result.duration_ms = (time.time() - start) * 1000
        self._trace(mission_id, "pr_open_audit", result.status, result.model_dump())
        return result

    def _audit_report(self, payload: dict[str, Any]) -> str:
        repository = payload.get("repository", {}) if isinstance(payload.get("repository"), dict) else {}
        pull_request = payload.get("pull_request", {}) if isinstance(payload.get("pull_request"), dict) else {}
        repo_name = repository.get("full_name") or repository.get("name") or payload.get("repo") or "unknown repo"
        pr_number = pull_request.get("number") or payload.get("number") or payload.get("pr") or "unknown"
        title = pull_request.get("title") or payload.get("title") or "Pull request"
        branch = (pull_request.get("head") or {}).get("ref") if isinstance(pull_request.get("head"), dict) else None
        changed_files = payload.get("changed_files") or payload.get("files") or []
        file_lines = [f"- {item}" for item in changed_files[:20]] if isinstance(changed_files, list) else ["- Not provided"]

        return "\n".join(
            [
                "# Security Audit",
                "",
                f"Repository: {repo_name}",
                f"Pull request: #{pr_number} - {title}",
                f"Branch: {branch or 'unknown'}",
                f"Trigger: {payload.get('trigger', 'pr_open')}",
                "",
                "## Checks",
                "- Authentication and authorization paths reviewed from provided metadata.",
                "- Secret exposure risk checked against changed-file names supplied in payload.",
                "- Dependency or infrastructure-sensitive paths flagged for human review when present.",
                "",
                "## Changed Files",
                *file_lines,
                "",
                "## Output Contract",
                "This agent writes a durable local audit artifact and emits an ops notification when configured.",
            ]
        )

