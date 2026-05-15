import sys

content = open('src/autopilot/api/main.py', 'r').read()

import1 = """from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.actions import ArtifactConnector, LinearConnector, NotificationConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import ActionResult, ApprovalStatus, AuthMode, GraphNodeKind, MissionGraphNode, Signal, StepStatus, WebhookSignalRequest, new_id, utc_now
from autopilot.operators.llm import active_provider_name
from autopilot.storage import ARTIFACT_DIR, ROOT, Store

CONNECTOR_CLASSES = {
    "github": GitHubConnector,
    "web_search": KnowledgeConnector,
    "local_artifacts": ArtifactConnector,
    "linear": LinearConnector,
    "slack": NotificationConnector,
    "sentry": SentryConnector,
    "webhook": WebhookConnector,
}"""

content = content.replace("""from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.kernel import RuntimeKernel
from autopilot.models import ActionResult, ApprovalStatus, AuthMode, GraphNodeKind, MissionGraphNode, Signal, StepStatus, WebhookSignalRequest, new_id, utc_now
from autopilot.operators.llm import active_provider_name
from autopilot.storage import ARTIFACT_DIR, ROOT, Store""", import1)


sync1 = """store = Store()
registry = default_registry()
directory = ConnectorDirectory(store)

for cid, cls in CONNECTOR_CLASSES.items():
    manifest = cls().manifest
    if manifest.auth_required:
        if cid not in [c.connector_id for c in store.list_connector_connections()]:
            registry.unregister(manifest.name)

runtime = RuntimeKernel(store, registry)"""

content = content.replace("""store = Store()
registry = default_registry()
directory = ConnectorDirectory(store)
runtime = RuntimeKernel(store, registry)""", sync1)


connect1 = """        connection = directory.connect(
            connector_id,
            auth_mode,
            credentials_ref=(payload or {}).get("credentials_ref"),
            metadata=(payload or {}).get("metadata") or {},
        )
        if connector_id in CONNECTOR_CLASSES:
            registry.register(CONNECTOR_CLASSES[connector_id]())
        store.trace(None, "connector.connected", "complete", connection.model_dump())"""

content = content.replace("""        connection = directory.connect(
            connector_id,
            auth_mode,
            credentials_ref=(payload or {}).get("credentials_ref"),
            metadata=(payload or {}).get("metadata") or {},
        )
        store.trace(None, "connector.connected", "complete", connection.model_dump())""", connect1)

disconnect1 = """    try:
        connection = directory.disconnect(connector_id)
        if connector_id in CONNECTOR_CLASSES:
            manifest_name = CONNECTOR_CLASSES[connector_id]().manifest.name
            registry.unregister(manifest_name)
        store.trace(None, "connector.disconnected", "complete", connection.model_dump())"""

content = content.replace("""    try:
        connection = directory.disconnect(connector_id)
        store.trace(None, "connector.disconnected", "complete", connection.model_dump())""", disconnect1)

open('src/autopilot/api/main.py', 'w').write(content)
print('Done!')
