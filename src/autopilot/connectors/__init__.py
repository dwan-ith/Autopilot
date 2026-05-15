from autopilot.connectors.actions import ArtifactConnector, CloudInfraConnector, LinearConnector, NotificationConnector
from autopilot.connectors.base import ConnectorRegistry
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.security import SecurityAuditConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector


def default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(WebhookConnector())
    registry.register(SentryConnector())
    registry.register(GitHubConnector())
    registry.register(KnowledgeConnector())
    registry.register(ArtifactConnector())
    registry.register(NotificationConnector())
    registry.register(LinearConnector())
    registry.register(CloudInfraConnector())
    registry.register(SecurityAuditConnector())
    return registry
