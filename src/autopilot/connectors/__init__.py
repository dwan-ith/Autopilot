from autopilot.connectors.actions import ArtifactConnector, CloudInfraConnector, NotificationConnector
from autopilot.connectors.base import ConnectorRegistry
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.gmail import GmailConnector
from autopilot.connectors.google_drive import GoogleDriveConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.linear import LinearConnector  # canonical full GraphQL implementation
from autopilot.connectors.notion import NotionConnector
from autopilot.connectors.pagerduty import PagerDutyConnector
from autopilot.connectors.security import SecurityAuditConnector
from autopilot.connectors.tavily import TavilyConnector
from autopilot.connectors.weather import WeatherConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector


def default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(WebhookConnector())
    registry.register(SentryConnector())
    registry.register(PagerDutyConnector())
    registry.register(GitHubConnector())
    registry.register(KnowledgeConnector())
    registry.register(ArtifactConnector())
    registry.register(NotificationConnector())
    registry.register(LinearConnector())
    registry.register(CloudInfraConnector())
    registry.register(SecurityAuditConnector())
    registry.register(NotionConnector())
    registry.register(TavilyConnector())
    registry.register(WeatherConnector())
    registry.register(GmailConnector())
    registry.register(GoogleDriveConnector())
    return registry



