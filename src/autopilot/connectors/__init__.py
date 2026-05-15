from autopilot.connectors.actions import ArtifactConnector, LinearConnector, NotificationConnector
from autopilot.connectors.base import ConnectorRegistry
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector


def default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(WebhookConnector())
    registry.register(SentryConnector())
    registry.register(KnowledgeConnector())
    registry.register(ArtifactConnector())
    registry.register(NotificationConnector())
    registry.register(LinearConnector())
    return registry
