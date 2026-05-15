"""Connector catalog — only connectors that have real implementations.

Catalog entries with `implemented=True` have actual connector classes
that make real API calls. Entries with `implemented=False` are shown
as "available" in the UI but will be greyed out.

DO NOT add entries here that are pure theater. Every `implemented=True`
entry must correspond to a real Connector subclass.
"""

from __future__ import annotations

from autopilot.models import AuthMode, Capability, ConnectorCatalogItem


CATALOG: list[ConnectorCatalogItem] = [
    ConnectorCatalogItem(
        id="github",
        name="GitHub",
        category="Engineering",
        description=(
            "Receive webhooks from GitHub issues, PRs, and deployments. "
            "Search issues and PRs via GitHub REST API. Create follow-up issues and post comments."
        ),
        icon="GH",
        auth_mode=AuthMode.API_KEY,
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE, Capability.ACTION],
        event_types=["issues.opened", "pull_request.opened", "deployment.created", "workflow_run.completed", "push"],
        scopes=["repo", "issues:write"],
        safe_actions=["create_issue", "post_comment"],
        implemented_actions=["create_issue", "post_comment"],
        objects=["issues", "pull requests", "deployments", "commits"],
        implemented=True,
    ),
    ConnectorCatalogItem(
        id="sentry",
        name="Sentry",
        category="Observability",
        description=(
            "Ingest Sentry error and regression webhooks. Normalizes stack traces, "
            "projects, and culprits into structured operational signals."
        ),
        icon="SE",
        auth_mode=AuthMode.WEBHOOK,
        capabilities=[Capability.READ, Capability.SEARCH],
        event_types=["error.created", "issue.regression", "issue.created"],
        scopes=["events.read"],
        safe_actions=["mark_investigating"],
        objects=["errors", "issues", "stack traces"],
        implemented=True,
    ),
    ConnectorCatalogItem(
        id="web_search",
        name="Web Search",
        category="Research",
        description=(
            "Search the public web for real-time context and external validation "
            "via Tavily. Used by investigator sub-agents during evidence gathering."
        ),
        icon="WS",
        auth_mode=AuthMode.API_KEY,
        capabilities=[Capability.SEARCH, Capability.READ],
        scopes=["web.search"],
        safe_actions=[],
        objects=["web pages", "documentation", "public knowledge"],
        implemented=True,
    ),
    ConnectorCatalogItem(
        id="slack",
        name="Slack",
        category="Communication",
        description=(
            "Send bounded operational notifications to a Slack webhook "
            "when mission confidence passes policy threshold."
        ),
        icon="SL",
        auth_mode=AuthMode.WEBHOOK,
        capabilities=[Capability.NOTIFY, Capability.ACTION],
        event_types=["message.posted"],
        scopes=["chat.write"],
        safe_actions=["notify_ops"],
        implemented_actions=["notify_ops"],
        objects=["messages"],
        implemented=True,
    ),
    ConnectorCatalogItem(
        id="linear",
        name="Linear",
        category="Engineering",
        description=(
            "Create Linear issues for high-confidence missions when LINEAR_API_KEY "
            "and LINEAR_TEAM_ID are configured. Uses the Linear GraphQL API."
        ),
        icon="LN",
        auth_mode=AuthMode.API_KEY,
        capabilities=[Capability.WRITE, Capability.ACTION],
        scopes=["issues.write"],
        safe_actions=["create_issue"],
        implemented_actions=["create_issue"],
        objects=["issues"],
        implemented=True,
    ),
    ConnectorCatalogItem(
        id="local_artifacts",
        name="Artifacts",
        category="System",
        description=(
            "Write durable Markdown reports and JSON action packets to the local "
            "artifacts/ directory. Always active — no credentials required."
        ),
        icon="AR",
        auth_mode=AuthMode.NONE,
        capabilities=[Capability.WRITE, Capability.ACTION],
        scopes=[],
        safe_actions=["write_report", "write_action_packet"],
        implemented_actions=["write_report", "write_action_packet"],
        objects=["reports", "action packets"],
        implemented=True,
    ),
    # ── Coming soon (not implemented) ────────────────────────────────────
    ConnectorCatalogItem(
        id="pagerduty",
        name="PagerDuty",
        category="Observability",
        description="Receive PagerDuty incidents and post updates. (Not yet implemented — planned.)",
        icon="PD",
        auth_mode=AuthMode.WEBHOOK,
        capabilities=[Capability.READ, Capability.ACTION, Capability.NOTIFY],
        event_types=["incident.triggered", "incident.resolved"],
        scopes=["incidents.read"],
        safe_actions=["add_note"],
        objects=["incidents", "services"],
        implemented=False,
    ),
    ConnectorCatalogItem(
        id="notion",
        name="Notion",
        category="Knowledge",
        description="Read team knowledge bases and publish mission briefs. (Not yet implemented — planned.)",
        icon="NO",
        auth_mode=AuthMode.API_KEY,
        capabilities=[Capability.READ, Capability.SEARCH, Capability.WRITE],
        scopes=["pages.read", "pages.write"],
        safe_actions=["create_page"],
        objects=["pages", "databases"],
        implemented=False,
    ),
]


def catalog_by_id() -> dict[str, ConnectorCatalogItem]:
    return {item.id: item for item in CATALOG}
