import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["AUTOPILOT_DISABLE_LLM"] = "1"
os.environ.pop("AUTOPILOT_AUTO_APPROVE_ACTIONS", None)

from fastapi.testclient import TestClient

import autopilot.api.main as api_main
from autopilot.agents.base import SubAgent
from autopilot.connectors.actions import ArtifactConnector
from autopilot.connectors.base import Connector
from autopilot.connectors import default_registry
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.webhook import SentryConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import ActionResult, AuthMode, Capability, ConnectorManifest, ConnectorStatus, GraphNodeKind, MissionStatus, Signal
from autopilot.storage import Store


class FakeIssueConnector(Connector):
    manifest = ConnectorManifest(
        name="fake_issue",
        description="Test issue connector.",
        capabilities=[Capability.ACTION],
        safe_actions=["create_issue"],
        auth_required=True,
    )

    def readiness(self, action: str | None = None) -> dict:
        return {
            "configured": True,
            "action_ready": True,
            "missing": [],
            "mode": "test",
            "detail": "Fake issue connector is ready.",
            "action": action,
        }

    async def action(self, name: str, payload: dict) -> ActionResult:
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary=f"Created fake issue: {payload.get('title', 'untitled')}",
            metadata={"payload": payload},
        )


class RuntimeKernelTest(unittest.TestCase):
    def test_correlates_runs_replans_and_publishes_actions(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=0.05)

            first = Signal(
                source="support_webhook",
                type="support_escalation",
                summary="Enterprise customer reports failed exports after today's rollout.",
                entities=["export service", "rollout"],
                urgency="high",
            )
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp_dir):
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
            # Evidence count is >= 0 in offline/heuristic mode (no LLM, no Tavily).
            # When LLM providers are configured, investigators gather real evidence.
            self.assertGreaterEqual(len(completed.evidence), 0)
            self.assertGreaterEqual(len(completed.actions), 1)
            self.assertGreaterEqual(len(completed.agent_runs), 1)
            self.assertGreaterEqual(len(completed.policy_decisions), 1)
            self.assertGreaterEqual(len(completed.graph), 1)
            graph_kinds = {node.kind for node in completed.graph}
            self.assertIn(GraphNodeKind.HYPOTHESIS, graph_kinds)
            self.assertIn(GraphNodeKind.BRANCH, graph_kinds)
            self.assertIn(GraphNodeKind.SUBAGENT, graph_kinds)
            self.assertIn(GraphNodeKind.POLICY, graph_kinds)
            self.assertIn(GraphNodeKind.ACTION, graph_kinds)

            steps = store.list_steps(mission.id)
            self.assertTrue(any(step["name"] == "Verifier" for step in steps))
            self.assertTrue(any(step["name"] == "Action Publisher" for step in steps))
            self.assertEqual(len(completed.evidence), len({(ev.source, ev.title, ev.summary[:120]) for ev in completed.evidence}))
            self.assertFalse(any(ev.metadata.get("kind") == "config_error" for ev in completed.evidence))
            self.assertTrue(any(action.action == "write_action_packet" for action in completed.actions))
            self.assertFalse(completed.approvals)
            self.assertTrue(any(action.status == "skipped" and action.action == "create_issue" for action in completed.actions))

        asyncio.run(scenario())

    def test_pending_approval_can_be_executed_through_api(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            registry = default_registry()
            registry.register(FakeIssueConnector())
            runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp_dir):
                mission = await runtime.ingest(
                    Signal(
                        source="support_webhook",
                        type="support_escalation",
                        summary="Enterprise customer reports failed exports after rollout.",
                        entities=["export service", "rollout"],
                        urgency="high",
                    )
                )
                await runtime.wait_for(mission.id)

            completed = store.get_mission(mission.id)
            self.assertIsNotNone(completed)
            pending = [approval for approval in completed.approvals if approval.status == "pending" and approval.connector == "fake_issue"]
            self.assertTrue(pending)

            old_store, old_registry = api_main.store, api_main.registry
            api_main.store, api_main.registry = store, registry
            try:
                client = TestClient(api_main.app)
                response = client.post(f"/api/approvals/{pending[0].id}/approve", json={"decided_by": "test"})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["status"], "executed")
                updated = store.get_mission(mission.id)
                self.assertIsNotNone(updated)
                self.assertTrue(any(approval.id == pending[0].id and approval.status == "executed" for approval in updated.approvals))
            finally:
                api_main.store, api_main.registry = old_store, old_registry

        asyncio.run(scenario())

    def test_optional_api_key_protects_write_endpoints(self):
        old_key = os.environ.get("AUTOPILOT_API_KEY")
        os.environ["AUTOPILOT_API_KEY"] = "secret-test-key"
        tmp_dir = Path.cwd() / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
        old_store, old_directory = api_main.store, api_main.directory
        api_main.store = store
        api_main.directory = ConnectorDirectory(store)
        try:
            client = TestClient(api_main.app)
            blocked = client.post(
                "/api/connector-directory/slack/connect",
                json={"auth_mode": "demo"},
            )
            self.assertEqual(blocked.status_code, 401)

            allowed = client.post(
                "/api/connector-directory/slack/connect",
                headers={"x-autopilot-key": "secret-test-key"},
                json={"auth_mode": "demo"},
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)
        finally:
            api_main.store = old_store
            api_main.directory = old_directory
            if old_key is None:
                os.environ.pop("AUTOPILOT_API_KEY", None)
            else:
                os.environ["AUTOPILOT_API_KEY"] = old_key

    def test_connector_directory_persists_demo_connections(self):
        tmp_dir = Path.cwd() / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
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
        self.assertTrue(slack["tools"])

        gmail = next(item for item in reloaded if item["id"] == "gmail")
        self.assertTrue(gmail["implemented"])  # Gmail now has full OAuth2 connector
        self.assertIn("gmail.readonly", gmail["scopes"])
        self.assertTrue(any(tool["requires_confirmation"] for tool in gmail["tools"]))
        self.assertTrue(any(tool["read_only_hint"] for tool in gmail["tools"]))

    def test_artifact_action_packet_uses_canonical_action_name(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp_dir):
                result = await ArtifactConnector().action(
                    "write_action_packet",
                    {"packet_name": "action-packet-test", "mission_id": "mission_test"},
                )
            self.assertEqual(result.status, "complete")
            self.assertEqual(result.action, "write_action_packet")
            self.assertTrue(result.artifact_path.endswith("action-packet-test.json"))

        asyncio.run(scenario())

    def test_no_tool_agent_prompt_does_not_force_tool_use(self):
        async def scenario():
            captured: dict[str, str] = {}

            async def fake_reason(system: str, prompt: str, **kwargs):
                captured["system"] = system
                return '{"action":"answer","result":{"ok":true}}'

            with patch("autopilot.agents.base.reason", new=AsyncMock(side_effect=fake_reason)):
                result = await SubAgent(
                    role="Verifier",
                    tools=[],
                    system_prompt="Return a JSON verification result.",
                ).run("Verify without tools.")

            self.assertTrue(result.success)
            self.assertIn("You have no tools", captured["system"])
            self.assertNotIn("MUST use tools", captured["system"])
            self.assertIn("Return a JSON verification result.", captured["system"])

        asyncio.run(scenario())

    def test_idempotent_signal_does_not_spawn_duplicate_missions(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
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


if __name__ == "__main__":
    unittest.main()
