from __future__ import annotations

from datetime import timezone
from typing import Any

from autopilot.connectors.catalog import CATALOG, catalog_by_id
from autopilot.connectors.oauth import is_authorized
from autopilot.models import AuthMode, ConnectorCatalogItem, ConnectorConnection, ConnectorStatus, utc_now
from autopilot.storage import Store


def _is_demo_connection(connection: ConnectorConnection | None) -> bool:
    if not connection or connection.status != ConnectorStatus.CONNECTED:
        return False
    meta = connection.metadata or {}
    return bool(meta.get("demo_connection")) or connection.auth_mode == AuthMode.DEMO


def _live_connected_directory(
    item: ConnectorCatalogItem,
    connection: ConnectorConnection | None,
    oauth_token_present: bool,
) -> bool:
    """Whether the persisted directory row (and OAuth token store) indicates non-demo credentials."""
    if _is_demo_connection(connection):
        return False
    if connection and connection.status == ConnectorStatus.CONNECTED:
        if item.auth_mode == AuthMode.OAUTH:
            return oauth_token_present
        if item.auth_mode == AuthMode.WEBHOOK:
            return bool(connection.credentials_ref)
        if item.auth_mode == AuthMode.API_KEY:
            return bool(connection.credentials_ref)
        if item.auth_mode == AuthMode.NONE:
            return True
        if item.auth_mode == AuthMode.MCP:
            return bool(connection.credentials_ref)
    if item.auth_mode == AuthMode.OAUTH and item.implemented and oauth_token_present:
        return True
    return False


class ConnectorDirectory:
    """User-facing connector directory and connection state manager."""

    def __init__(self, store: Store):
        self.store = store
        self.catalog = catalog_by_id()

    def list(self, user_id: str = "default_user") -> list[dict]:
        connections = {connection.connector_id: connection for connection in self.store.list_connector_connections(user_id)}
        return [
            self._view(item, connections.get(item.id))
            for item in sorted(CATALOG, key=lambda c: ("zzz", "zzz") if c.id == "slack" else (c.category, c.name))
        ]

    def connect(
        self,
        connector_id: str,
        user_id: str = "default_user",
        auth_mode: AuthMode | None = None,
        credentials_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConnectorConnection:
        item = self._require(connector_id)
        mode = auth_mode or (AuthMode.DEMO if item.demo_available else item.auth_mode)
        config = self._safe_metadata(metadata or {})
        connection = ConnectorConnection(
            connector_id=item.id,
            user_id=user_id,
            status=ConnectorStatus.CONNECTED,
            auth_mode=mode,
            granted_scopes=item.scopes,
            connected_at=utc_now(),
            credentials_ref=credentials_ref,
            metadata={
                "display_name": item.name,
                "category": item.category,
                "demo_connection": mode == AuthMode.DEMO,
                "capabilities": [cap.value for cap in item.capabilities],
                "safe_actions": item.safe_actions,
                "implemented": item.implemented,
                "implemented_actions": item.implemented_actions,
                "configured": bool(credentials_ref) or mode in {AuthMode.DEMO, AuthMode.NONE},
                **config,
            },
        )
        self.store.save_connector_connection(connection)
        return connection

    def disconnect(self, connector_id: str, user_id: str = "default_user") -> ConnectorConnection:
        item = self._require(connector_id)
        connection = ConnectorConnection(
            connector_id=item.id,
            user_id=user_id,
            status=ConnectorStatus.DISCONNECTED,
            auth_mode=item.auth_mode,
            granted_scopes=[],
            connected_at=None,
            metadata={"display_name": item.name, "category": item.category},
        )
        self.store.save_connector_connection(connection)
        return connection

    def _require(self, connector_id: str) -> ConnectorCatalogItem:
        try:
            return self.catalog[connector_id]
        except KeyError as exc:
            raise KeyError(f"Unknown connector '{connector_id}'") from exc

    def _view(self, item: ConnectorCatalogItem, connection: ConnectorConnection | None) -> dict:
        oauth_token_present = False
        if item.implemented and item.auth_mode == AuthMode.OAUTH:
            oauth_token_present = is_authorized(item.id, self.store.path)

        return {
            **item.model_dump(),
            "status": connection.status.value if connection else ConnectorStatus.AVAILABLE.value,
            "connected_at": connection.connected_at.astimezone(timezone.utc).isoformat()
            if connection and connection.connected_at
            else None,
            "granted_scopes": connection.granted_scopes if connection else [],
            "connection_auth_mode": connection.auth_mode.value if connection else None,
            "credentials_ref": connection.credentials_ref if connection else None,
            "connection_metadata": connection.metadata if connection else {},
            "oauth_token_present": oauth_token_present,
            "live_connected": _live_connected_directory(item, connection, oauth_token_present),
            "is_demo_connection": _is_demo_connection(connection),
        }

    def _safe_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in metadata.items():
            lower = key.lower()
            if any(secret in lower for secret in ["token", "secret", "password", "api_key", "webhook_url"]):
                redacted[key] = "***redacted***"
            else:
                redacted[key] = value
        return redacted
