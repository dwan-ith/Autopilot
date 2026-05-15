import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["AUTOPILOT_DISABLE_LLM"] = "1"

from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.webhook import SentryConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import AuthMode, ConnectorStatus, GraphNodeKind, MissionStatus, Signal
from autopilot.state_store import StateStore


class RuntimeKernelTest(unittest.TestCase):
    def test_correlates_runs_replans_and_publishes_actions(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = StateStore(tmp_dir / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=0.05)

            first = Signal(
                source="support_webhook",
                type="support_escalation",
                summary="Enterprise customer reports failed exports after today's rollout.",
                entities=["export service", "rollout"],
                urgency="high",
            )
            mission = await runtime.ingest(first)

            second = Signal(
                source="monitoring_webhook",
                type="error_spike",
                summary="Export job failures increased from 1% to 38%.",
                entities=["export service", "job failures"],
                urgency="high",
            )
            await runtime.ingest(second)

            await runtime.wait_for(mission.id)
            completed = store.get_mission(mission.id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, MissionStatus.COMPLETE)
            self.assertGreaterEqual(len(completed.signals), 2)
            self.assertGreaterEqual(len(completed.hypotheses), 2)
            self.assertGreaterEqual(len(completed.evidence), 1)
            self.assertGreaterEqual(len(completed.actions), 1)
            self.assertGreaterEqual(len(completed.policy_decisions), 1)
            self.assertGreaterEqual(len(completed.graph), 1)
            graph_kinds = {node.kind for node in completed.graph}
            self.assertIn(GraphNodeKind.HYPOTHESIS, graph_kinds)
            self.assertIn(GraphNodeKind.BRANCH, graph_kinds)
            self.assertIn(GraphNodeKind.POLICY, graph_kinds)
            self.assertIn(GraphNodeKind.ACTION, graph_kinds)

            steps = store.list_steps(mission.id)
            self.assertTrue(any(step["name"] == "Verification Gate" for step in steps))
            self.assertTrue(any(step["name"] == "Action Publisher" for step in steps))
            self.assertEqual(len(completed.evidence), len({(ev.source, ev.title, ev.summary[:120]) for ev in completed.evidence}))

        asyncio.run(scenario())

    def test_connector_directory_persists_demo_connections(self):
        tmp_dir = Path.cwd() / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        store = StateStore(tmp_dir / f"autopilot-{uuid4().hex}.db")
        directory = ConnectorDirectory(store)

        items = directory.list()
        self.assertTrue(any(item["id"] == "slack" for item in items))

        connection = directory.connect(
            "slack",
            auth_mode=AuthMode.WEBHOOK,
            credentials_ref="SLACK_WEBHOOK_URL",
            metadata={"webhook_url": "https://hooks.slack.example/secret", "workspace": "demo"},
        )
        self.assertEqual(connection.status, ConnectorStatus.CONNECTED)
        self.assertIn("chat.write", connection.granted_scopes)
        self.assertEqual(connection.credentials_ref, "SLACK_WEBHOOK_URL")
        self.assertEqual(connection.metadata["webhook_url"], "***redacted***")

        reloaded = ConnectorDirectory(store).list()
        slack = next(item for item in reloaded if item["id"] == "slack")
        self.assertEqual(slack["status"], "connected")
        self.assertTrue(slack["implemented"])
        self.assertEqual(slack["credentials_ref"], "SLACK_WEBHOOK_URL")

    def test_idempotent_signal_does_not_spawn_duplicate_missions(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = StateStore(tmp_dir / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=0.01)

            signal = Signal(
                source="pagerduty",
                type="incident.triggered",
                summary="Checkout latency p95 is above SLA.",
                entities=["checkout"],
                urgency="high",
                idempotency_key="pagerduty:incident:123",
            )
            first = await runtime.ingest(signal)
            second = await runtime.ingest(
                Signal(
                    source="pagerduty",
                    type="incident.triggered",
                    summary="Checkout latency p95 is above SLA.",
                    entities=["checkout"],
                    urgency="high",
                    idempotency_key="pagerduty:incident:123",
                )
            )
            await runtime.wait_for(first.id)
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(store.list_missions()), 1)
            self.assertTrue(any(trace["name"] == "signal.duplicate" for trace in store.list_traces(first.id)))

        asyncio.run(scenario())

    def test_sentry_connector_normalizes_real_webhook_shape(self):
        async def scenario():
            signal = await SentryConnector().normalize_event(
                {
                    "action": "regression",
                    "data": {
                        "event": {"event_id": "evt-1", "title": "TypeError in checkout", "culprit": "checkout.views"},
                        "project": {"slug": "storefront"},
                        "issue": {"shortId": "SHOP-42"},
                    },
                }
            )
            self.assertEqual(signal.source, "sentry")
            self.assertEqual(signal.idempotency_key, "sentry:evt-1")
            self.assertIn("storefront", signal.entities)
            self.assertEqual(signal.urgency, "high")

        asyncio.run(scenario())

    def test_scoped_connectors_are_registered(self):
        registry = default_registry()
        manifests = {manifest.name: manifest for manifest in registry.manifests()}

        self.assertEqual(manifests["linear"].safe_actions, ["create_issue"])
        self.assertEqual(manifests["cloud_infra"].safe_actions, ["trigger_deployment"])
        self.assertIn("pull_request.opened", manifests["security_audit"].event_types)

    def test_security_audit_pr_open_contract(self):
        async def scenario():
            connector = default_registry().get("security_audit")
            signal = await connector.normalize_event(
                {
                    "action": "opened",
                    "repository": {"full_name": "demo/app"},
                    "pull_request": {
                        "number": 42,
                        "title": "Update auth flow",
                        "head": {"ref": "auth-update"},
                    },
                }
            )

            self.assertEqual(signal.type, "pull_request.opened")
            self.assertIn("demo/app", signal.entities)
            self.assertEqual(signal.payload["trigger"], "pr_open")
            self.assertIn("artifact", signal.payload["output_contract"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
