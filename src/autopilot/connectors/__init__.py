from autopilot.connectors.actions import ArtifactConnector, NotificationConnector
from autopilot.connectors.base import ConnectorRegistry
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.webhook import WebhookConnector


def default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(WebhookConnector())
    registry.register(KnowledgeConnector())
    registry.register(ArtifactConnector())
    registry.register(NotificationConnector())
    return registry
