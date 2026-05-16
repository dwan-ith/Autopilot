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
        changed_files: list[str] = payload.get("changed_files") or payload.get("files") or []
        if not isinstance(changed_files, list):
            changed_files = []

        # ── Real pattern-based analysis ─────────────────────────────────────

        # Risk patterns: category → (patterns, severity, description)
        RISK_PATTERNS = {
            "SECRET_EXPOSURE": (
                [".env", "secret", "credential", "api_key", "apikey", "password", "passwd",
                 "token", "private_key", "id_rsa", "id_ed25519", ".pem", ".p12", ".pfx",
                 "keystore", "vault", "htpasswd"],
                "CRITICAL",
                "File may contain or expose secrets, credentials, or private keys.",
            ),
            "AUTH_AND_ACCESS": (
                ["auth", "login", "oauth", "jwt", "session", "cookie", "permission",
                 "acl", "rbac", "middleware/auth", "guard", "policy", "access_control"],
                "HIGH",
                "Authentication or authorization logic changed — requires security review.",
            ),
            "INFRASTRUCTURE": (
                ["dockerfile", "docker-compose", ".terraform", "helm/", "k8s/",
                 "kubernetes", "nginx.conf", "apache", "ingress", "network",
                 "firewall", "security_group", "iam", "aws_", "gcp_", "azure_"],
                "HIGH",
                "Infrastructure or cloud configuration changed — check for open ports or role escalation.",
            ),
            "DEPENDENCY": (
                ["package.json", "package-lock.json", "yarn.lock", "requirements.txt",
                 "pyproject.toml", "poetry.lock", "go.sum", "go.mod", "gemfile",
                 "gemfile.lock", "cargo.toml", "cargo.lock", "build.gradle", "pom.xml"],
                "MEDIUM",
                "Dependency manifest changed — check for known-vulnerable or unexpected packages.",
            ),
            "DATA_MIGRATION": (
                ["migration", "migrate", "schema", "alembic", "flyway", "liquibase",
                 "seed", "fixtures", ".sql"],
                "MEDIUM",
                "Database schema or migration changed — check for data loss or backwards-incompatibility.",
            ),
            "CI_CD_PIPELINE": (
                [".github/workflows", ".gitlab-ci", "jenkinsfile", ".circleci",
                 "bitbucket-pipelines", ".travis.yml", "cloudbuild"],
                "MEDIUM",
                "CI/CD pipeline configuration changed — verify no unintended secret exposure or elevated permissions.",
            ),
            "ENCRYPTION_CRYPTO": (
                ["crypto", "encrypt", "decrypt", "hash", "hmac", "tls", "ssl",
                 "certificate", "cipher", "signing", "pgp"],
                "HIGH",
                "Cryptographic code changed — verify algorithm correctness and key management.",
            ),
        }

        flagged: dict[str, list[str]] = {}  # category → matched files
        all_findings: list[str] = []

        for filepath in changed_files:
            fp_lower = str(filepath).lower()
            for category, (patterns, severity, _) in RISK_PATTERNS.items():
                if any(p in fp_lower for p in patterns):
                    flagged.setdefault(category, []).append(str(filepath))

        # Build findings section
        if flagged:
            for category, files in sorted(flagged.items(), key=lambda x: RISK_PATTERNS[x[0]][1]):
                _, severity, desc = RISK_PATTERNS[category]
                all_findings.append(f"### [{severity}] {category.replace('_', ' ').title()}")
                all_findings.append(f"  {desc}")
                for f in files[:10]:
                    all_findings.append(f"  - `{f}`")
                all_findings.append("")
        else:
            all_findings.append("No high-risk file patterns detected in the changed file list.")

        # Overall risk level
        if "SECRET_EXPOSURE" in flagged or "ENCRYPTION_CRYPTO" in flagged:
            overall = "CRITICAL — Immediate review required before merge."
        elif any(c in flagged for c in ["AUTH_AND_ACCESS", "INFRASTRUCTURE"]):
            overall = "HIGH — Security team review strongly recommended."
        elif flagged:
            overall = "MEDIUM — Standard security review applies."
        else:
            overall = "LOW — No high-risk patterns detected; proceed with normal review."

        file_lines = [f"- `{item}`" for item in changed_files[:25]] if changed_files else ["- Not provided"]

        lines = [
            "# AUTOPILOT Security Audit",
            "",
            f"**Repository:** {repo_name}",
            f"**Pull Request:** #{pr_number} — {title}",
            f"**Branch:** {branch or 'unknown'}",
            f"**Trigger:** {payload.get('trigger', 'pr_open')}",
            f"**Overall Risk:** {overall}",
            "",
            "## Risk Findings",
            "",
        ]
        lines.extend(all_findings)
        lines += [
            "## Changed Files",
            "",
            *file_lines,
            "",
            "## Output Contract",
            "This agent writes a durable local audit artifact and emits an ops notification when configured.",
            "Findings are based on static file-path pattern analysis of the payload's changed_files list.",
        ]
        return "\n".join(lines)

