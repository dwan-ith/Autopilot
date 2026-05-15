from autopilot.connectors.actions import ArtifactConnector, NotificationConnector
from autopilot.connectors.base import ConnectorRegistry
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.webhook import WebhookConnector
from autopilot.connectors.maas import MAASConnectorRegistry
from autopilot.agents.github_agent import GitHubAgent
from autopilot.agents.project_mgmt_agent import ProjectMgmtAgent
from autopilot.agents.communication_agent import CommunicationAgent
from autopilot.agents.cloud_infra_agent import CloudInfraAgent
from autopilot.agents.docs_agent import DocsAgent
from autopilot.agents.analytics_agent import AnalyticsAgent
from autopilot.agents.security_audit_agent import SecurityAuditAgent
from autopilot.storage import Store


def default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(WebhookConnector())
    registry.register(KnowledgeConnector())
    registry.register(ArtifactConnector())
    registry.register(NotificationConnector())
    return registry


def default_maas_registry(store: Store | None = None) -> MAASConnectorRegistry:
    maas = MAASConnectorRegistry()
    # instantiate agents; analytics needs store
    maas.register(GitHubAgent())
    maas.register(ProjectMgmtAgent())
    maas.register(CommunicationAgent())
    maas.register(CloudInfraAgent())
    maas.register(DocsAgent())
    maas.register(AnalyticsAgent(store or Store()))
    maas.register(SecurityAuditAgent())
    return maas
