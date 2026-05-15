import asyncio
import hashlib
import hmac
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

os.environ["AUTOPILOT_DISABLE_LLM"] = "1"
os.environ.pop("AUTOPILOT_AUTO_APPROVE_ACTIONS", None)

from fastapi.testclient import TestClient

import autopilot.api.main as api_main
from autopilot.agents.base import SubAgent
from autopilot.agents.mission.planner import PlannerAgent
from autopilot.connectors import actions as action_connectors
from autopilot.connectors.actions import ArtifactConnector, NotificationConnector
from autopilot.connectors.base import Connector
from autopilot.connectors import default_registry
from autopilot.connectors.gmail import GmailConnector
from autopilot.connectors.google_drive import GoogleDriveConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.notion import NotionConnector
from autopilot.connectors.pagerduty import PagerDutyConnector
from autopilot.connectors.service import ConnectorDirectory
from autopilot.connectors.weather import WeatherConnector
from autopilot.connectors.webhook import SentryConnector, WebhookConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import (
    ActionResult,
    ApprovalStatus,
    AuthMode,
    Capability,
    ConnectorManifest,
    ConnectorStatus,
    GraphNodeKind,
    Hypothesis,
    Mission,
    MissionStatus,
    Signal,
)
from autopilot.operators import llm
from autopilot.policy import PolicyEngine
from autopilot.state_store import StateStore
from autopilot.storage import Store


class FakeIssueConnector(Connector):
    manifest = ConnectorManifest(
        name="fake_issue",
        description="Test issue connector.",
        capabilities=[Capability.ACTION],
        safe_actions=["create_issue"],
        auth_required=True,
    )

    def readiness(self, action=None):
        return {"configured": True, "action_ready": True, "missing": [], "mode": "test", "detail": "ready.", "action": action}

    async def action(self, name, payload):
        return ActionResult(connector=self.manifest.name, action=name, status="complete",
                            summary=f"Created fake issue: {payload.get('title', 'untitled')}", metadata={"payload": payload})


class RuntimeKernelTest(unittest.TestCase):

    # ── Original tests (preserved) ────────────────────────────────────────────

    def test_correlates_runs_replans_and_publishes_actions(self):
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            store = Store(tmp / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=0.05)
            first = Signal(source="support_webhook", type="support_escalation",
                           summary="Enterprise customer reports failed exports after today's rollout.",
                           entities=["export service", "rollout"], urgency="high")
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                mission = await runtime.ingest(first)
                await runtime.ingest(Signal(source="monitoring_webhook", type="error_spike",
                                            summary="Export job failures increased from 1% to 38%.",
                                            entities=["export service", "job failures"], urgency="high"))
                await runtime.wait_for(mission.id)
            completed = store.get_mission(mission.id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, MissionStatus.COMPLETE)
            self.assertGreaterEqual(len(completed.signals), 2)
            self.assertGreaterEqual(len(completed.hypotheses), 2)
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
            # With anonymous GitHub search active, a create_issue may enter the approval queue
            # if confidence < 0.72 threshold. That is correct policy behaviour — all such
            # approvals must be PENDING (policy-blocked), not requiring human input.
            for approval in completed.approvals:
                self.assertEqual(approval.status.value, "pending")
                self.assertIn("confidence", approval.reason.lower())
            self.assertTrue(any(action.status == "skipped" and action.action == "create_issue" for action in completed.actions)
                            or any(a.action == "create_issue" for a in completed.approvals))
        asyncio.run(scenario())

    def test_pending_approval_can_be_executed_through_api(self):
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            store = Store(tmp / f"autopilot-{uuid4().hex}.db")
            registry = default_registry()
            registry.register(FakeIssueConnector())
            runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                mission = await runtime.ingest(Signal(source="support_webhook", type="support_escalation",
                    summary="Enterprise customer reports failed exports after rollout.",
                    entities=["export service", "rollout"], urgency="high"))
                await runtime.wait_for(mission.id)
            completed = store.get_mission(mission.id)
            self.assertIsNotNone(completed)
            pending = [a for a in completed.approvals if a.status == "pending" and a.connector == "fake_issue"]
            self.assertTrue(pending)
            old_store, old_registry = api_main.store, api_main.registry
            api_main.store, api_main.registry = store, registry
            try:
                client = TestClient(api_main.app)
                response = client.post(f"/api/approvals/{pending[0].id}/approve", json={"decided_by": "test"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "executed")
            finally:
                api_main.store, api_main.registry = old_store, old_registry
        asyncio.run(scenario())

    def test_optional_api_key_protects_write_endpoints(self):
        old_key = os.environ.get("AUTOPILOT_API_KEY")
        os.environ["AUTOPILOT_API_KEY"] = "secret-test-key"
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        old_store, old_directory = api_main.store, api_main.directory
        api_main.store = store
        api_main.directory = ConnectorDirectory(store)
        try:
            client = TestClient(api_main.app)
            self.assertEqual(client.post("/api/connector-directory/slack/connect", json={"auth_mode": "demo"}).status_code, 401)
            self.assertEqual(client.post("/api/connector-directory/slack/connect?access_key=secret-test-key", json={"auth_mode": "demo"}).status_code, 401)
            self.assertEqual(client.get("/api/events").status_code, 401)
            self.assertEqual(client.get("/api/analytics/missions").status_code, 401)
            self.assertEqual(client.get("/api/analytics/missions?access_key=secret-test-key").status_code, 200)
            allowed = client.post("/api/connector-directory/slack/connect",
                                  headers={"x-autopilot-key": "secret-test-key"}, json={"auth_mode": "demo"})
            self.assertEqual(allowed.status_code, 200, allowed.text)
        finally:
            api_main.store, api_main.directory = old_store, old_directory
            if old_key is None: os.environ.pop("AUTOPILOT_API_KEY", None)
            else: os.environ["AUTOPILOT_API_KEY"] = old_key

    def test_dedicated_webhooks_are_authenticated_and_ingest(self):
        old_key = os.environ.get("AUTOPILOT_API_KEY")
        os.environ["AUTOPILOT_API_KEY"] = "webhook-test-key"
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        registry = default_registry()
        runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
        old_store, old_registry, old_runtime = api_main.store, api_main.registry, api_main.runtime
        api_main.store, api_main.registry, api_main.runtime = store, registry, runtime
        try:
            client = TestClient(api_main.app)
            self.assertEqual(client.post("/webhooks/weather", json={"event": "storm warning"}).status_code, 401)
            accepted = client.post("/webhooks/jira", headers={"x-autopilot-key": "webhook-test-key"},
                json={"webhookEvent": "jira:issue_updated", "summary": "Checkout export incident", "entities": ["checkout"]})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertTrue(accepted.json()["mission_id"].startswith("mission_"))
        finally:
            api_main.store, api_main.registry, api_main.runtime = old_store, old_registry, old_runtime
            if old_key is None: os.environ.pop("AUTOPILOT_API_KEY", None)
            else: os.environ["AUTOPILOT_API_KEY"] = old_key

    def test_memory_prunes_old_entries_per_key(self):
        old_keep = os.environ.get("AUTOPILOT_MEMORY_KEEP_PER_KEY")
        os.environ["AUTOPILOT_MEMORY_KEEP_PER_KEY"] = "2"
        try:
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            store = Store(tmp / f"autopilot-{uuid4().hex}.db")
            store.remember("mission_resolution", "old")
            store.remember("mission_resolution", "middle")
            store.remember("mission_resolution", "new")
            self.assertEqual(store.recall("mission_resolution", limit=5), ["new", "middle"])
        finally:
            if old_keep is None: os.environ.pop("AUTOPILOT_MEMORY_KEEP_PER_KEY", None)
            else: os.environ["AUTOPILOT_MEMORY_KEEP_PER_KEY"] = old_keep

    def test_actions_module_no_longer_exports_duplicate_linear_connector(self):
        self.assertFalse(hasattr(action_connectors, "LinearConnector"))

    def test_llm_slot_retries_rate_limit_before_fallback(self):
        class FakeClient:
            responses: list[httpx.Response] = []
            posts = 0
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **kw):
                self.__class__.posts += 1
                return self.__class__.responses.pop(0)
        async def scenario():
            old_retries = os.environ.get("AUTOPILOT_LLM_RETRIES")
            os.environ["AUTOPILOT_LLM_RETRIES"] = "2"
            req = httpx.Request("POST", "https://llm.example.test")
            FakeClient.responses = [
                httpx.Response(429, headers={"Retry-After": "0.1"}, request=req),
                httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req),
            ]
            FakeClient.posts = 0
            slot = {"name": "test-slot", "url": "https://llm.example.test", "key": "test-key", "model": "test-model", "extra": {}}
            try:
                with (patch("autopilot.operators.llm.httpx.AsyncClient", FakeClient),
                      patch("autopilot.operators.llm.asyncio.sleep", new_callable=AsyncMock) as sleep):
                    result = await llm._call_slot(slot, "system", "prompt", None, 0.2, False, 1024)
                self.assertEqual(result, "ok")
                self.assertEqual(FakeClient.posts, 2)
                sleep.assert_awaited_once_with(0.1)
            finally:
                if old_retries is None: os.environ.pop("AUTOPILOT_LLM_RETRIES", None)
                else: os.environ["AUTOPILOT_LLM_RETRIES"] = old_retries
        asyncio.run(scenario())

    def test_connector_directory_persists_demo_connections(self):
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        directory = ConnectorDirectory(store)
        items = directory.list()
        self.assertTrue(any(item["id"] == "slack" for item in items))
        connection = directory.connect("slack", auth_mode=AuthMode.WEBHOOK, credentials_ref="SLACK_WEBHOOK_URL",
                                       metadata={"webhook_url": "https://hooks.slack.example/secret", "workspace": "demo"})
        self.assertEqual(connection.status, ConnectorStatus.CONNECTED)
        self.assertIn("chat.write", connection.granted_scopes)
        self.assertEqual(connection.credentials_ref, "SLACK_WEBHOOK_URL")
        self.assertEqual(connection.metadata["webhook_url"], "***redacted***")
        reloaded = ConnectorDirectory(store).list()
        slack = next(item for item in reloaded if item["id"] == "slack")
        self.assertEqual(slack["status"], "connected")
        self.assertTrue(slack["implemented"])
        gmail = next(item for item in reloaded if item["id"] == "gmail")
        self.assertTrue(gmail["implemented"])
        self.assertIn("gmail.readonly", gmail["scopes"])

    def test_operator_catalog_exposes_configured_and_unconfigured_operators(self):
        old_store, old_registry, old_directory = api_main.store, api_main.registry, api_main.directory
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        api_main.store = store
        api_main.registry = default_registry()
        api_main.directory = ConnectorDirectory(store)
        try:
            client = TestClient(api_main.app)
            response = client.get("/api/operators")
            self.assertEqual(response.status_code, 200, response.text)
            operators = {item["id"]: item for item in response.json()}
            for key in ["github", "gmail", "google_drive", "local_artifacts", "slack", "weather"]:
                self.assertIn(key, operators)
                self.assertTrue(operators[key]["tools"] or operators[key]["safe_actions"])
            # GitHub now always reports configured:True — anonymous public search active
            self.assertTrue(operators["github"]["configured"])
            self.assertEqual(operators["github"]["readiness"]["mode"], "anonymous_public")
            self.assertTrue(operators["weather"]["configured"])
        finally:
            api_main.store, api_main.registry, api_main.directory = old_store, old_registry, old_directory

    def test_operator_probe_runs_local_artifact_side_effect(self):
        old_store, old_registry, old_directory = api_main.store, api_main.registry, api_main.directory
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        api_main.store = store
        api_main.registry = default_registry()
        api_main.directory = ConnectorDirectory(store)
        try:
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                client = TestClient(api_main.app)
                response = client.post("/api/operators/local_artifacts/probe", json={})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["status"], "complete")
            self.assertEqual(body["kind"], "write")
            self.assertTrue(Path(body["output"]["artifact_path"]).exists())
        finally:
            api_main.store, api_main.registry, api_main.directory = old_store, old_registry, old_directory

    def test_operator_probe_runs_knowledge_search(self):
        client = TestClient(api_main.app)
        response = client.post("/api/operators/web_search/probe", json={"query": "export rollout failure"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kind"], "search")
        self.assertGreater(len(body["output"]), 0)

    def test_operator_action_contracts_match_declared_safe_actions(self):
        async def scenario():
            gmail = await GmailConnector().action("draft_reply", {"thread_id": "t1", "to": "ops@example.com", "body": "hello"})
            self.assertEqual(gmail.action, "draft_reply")
            self.assertEqual(gmail.status, "blocked")

            drive = await GoogleDriveConnector().action("create_doc", {"title": "Brief", "body": "hello"})
            self.assertEqual(drive.action, "create_doc")
            self.assertEqual(drive.status, "blocked")

            notion = await NotionConnector().action("create_page", {"title": "Brief", "body": "hello"})
            self.assertEqual(notion.action, "create_page")
            self.assertEqual(notion.status, "blocked")
        asyncio.run(scenario())

    def test_weather_connector_has_no_key_fallback(self):
        old_key = os.environ.pop("OPENWEATHER_API_KEY", None)
        try:
            readiness = WeatherConnector().readiness()
            self.assertTrue(readiness["configured"])
            self.assertEqual(readiness["mode"], "open_meteo_fallback")
        finally:
            if old_key:
                os.environ["OPENWEATHER_API_KEY"] = old_key

    def test_artifact_action_packet_uses_canonical_action_name(self):
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                result = await ArtifactConnector().action("write_action_packet",
                    {"packet_name": "action-packet-test", "mission_id": "mission_test"})
            self.assertEqual(result.status, "complete")
            self.assertEqual(result.action, "write_action_packet")
            self.assertTrue(result.artifact_path.endswith("action-packet-test.json"))
        asyncio.run(scenario())

    def test_no_tool_agent_prompt_does_not_force_tool_use(self):
        async def scenario():
            captured: dict = {}
            async def fake_reason(system, prompt, **kw):
                captured["system"] = system
                return '{"action":"answer","result":{"ok":true}}'
            with patch("autopilot.agents.base.reason", new=AsyncMock(side_effect=fake_reason)):
                result = await SubAgent(role="Verifier", tools=[], system_prompt="Return a JSON verification result.").run("Verify without tools.")
            self.assertTrue(result.success)
            self.assertIn("You have no tools", captured["system"])
            self.assertNotIn("MUST use tools", captured["system"])
            self.assertIn("Return a JSON verification result.", captured["system"])
        asyncio.run(scenario())

    def test_idempotent_signal_does_not_spawn_duplicate_missions(self):
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            store = Store(tmp / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=0.01)
            key = "pagerduty:incident:123"
            first = await runtime.ingest(Signal(source="pagerduty", type="incident.triggered",
                summary="Checkout latency p95 is above SLA.", entities=["checkout"], urgency="high", idempotency_key=key))
            second = await runtime.ingest(Signal(source="pagerduty", type="incident.triggered",
                summary="Checkout latency p95 is above SLA.", entities=["checkout"], urgency="high", idempotency_key=key))
            await runtime.wait_for(first.id)
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(store.list_missions()), 1)
            self.assertTrue(any(t["name"] == "signal.duplicate" for t in store.list_traces(first.id)))
        asyncio.run(scenario())

    def test_sentry_connector_normalizes_real_webhook_shape(self):
        async def scenario():
            signal = await SentryConnector().normalize_event({
                "action": "regression",
                "data": {
                    "event": {"event_id": "evt-1", "title": "TypeError in checkout", "culprit": "checkout.views"},
                    "project": {"slug": "storefront"},
                    "issue": {"shortId": "SHOP-42"},
                },
            })
            self.assertEqual(signal.source, "sentry")
            self.assertEqual(signal.idempotency_key, "sentry:evt-1")
            self.assertIn("storefront", signal.entities)
            self.assertEqual(signal.urgency, "high")
        asyncio.run(scenario())

    # ── New tests (12 additional) ─────────────────────────────────────────────

    def test_hypothesis_has_search_focus_field(self):
        """Bug fix: Hypothesis must have search_focus field."""
        h = Hypothesis(title="Test", rationale="testing", search_focus="export failure logs")
        self.assertEqual(h.search_focus, "export failure logs")
        h2 = Hypothesis(title="Default", rationale="no focus")
        self.assertEqual(h2.search_focus, "")

    def test_planner_heuristic_sets_search_focus(self):
        """search_focus must be populated in heuristic fallback."""
        mission = Mission(title="Export job failure", summary="exports are failing")
        mission.signals = [Signal(source="webhook", type="error", summary="export pipeline failure")]
        planner = PlannerAgent()
        hyps = planner._heuristic_hypotheses(mission)
        for h in hyps:
            self.assertIsInstance(h.search_focus, str)
            self.assertTrue(len(h.search_focus) > 0, f"Hypothesis '{h.title}' has empty search_focus")

    def test_planner_llm_response_populates_search_focus(self):
        """When LLM returns hypotheses with search_focus, they must be stored."""
        async def scenario():
            async def fake_reason(system, prompt, **kw):
                return '''{
                    "action": "answer",
                    "result": {
                        "hypotheses": [
                            {"title": "Rollout regression", "rationale": "deploy broke it",
                             "initial_confidence": 0.4, "search_focus": "recent deploy diff export pipeline"}
                        ]
                    }
                }'''
            mission = Mission(title="Export failure", summary="exports broken")
            mission.signals = [Signal(source="webhook", type="error", summary="exports down")]
            with patch("autopilot.agents.base.reason", new=AsyncMock(side_effect=fake_reason)):
                planner = PlannerAgent()
                result = await planner.plan(mission)
            self.assertEqual(len(result.hypotheses), 1)
            self.assertEqual(result.hypotheses[0].search_focus, "recent deploy diff export pipeline")
        asyncio.run(scenario())

    def test_policy_engine_low_risk_action_auto_approved(self):
        """write_report (LOW risk, 0.0 threshold) should always be allowed."""
        from autopilot.connectors.actions import ArtifactConnector
        connector = ArtifactConnector()
        mission = Mission(title="Test mission", summary="test", confidence=0.0)
        engine = PolicyEngine()
        decision = engine.decide(mission, connector, "write_report")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.risk.value, "low")

    def test_policy_engine_blocks_high_risk_without_human_approval(self):
        """trigger_deployment (HIGH risk) must be blocked when high_risk_requires_human=True."""
        from autopilot.connectors.actions import CloudInfraConnector
        connector = CloudInfraConnector()
        mission = Mission(title="Test", summary="test", confidence=0.99)
        engine = PolicyEngine(high_risk_requires_human=True)
        decision = engine.decide(mission, connector, "trigger_deployment")
        self.assertFalse(decision.allowed)

    def test_policy_engine_blocks_unknown_connector_action(self):
        """An action not in connector.manifest.safe_actions must be blocked."""
        connector = FakeIssueConnector()
        mission = Mission(title="Test", summary="test", confidence=0.99)
        decision = PolicyEngine().decide(mission, connector, "delete_all_data")
        self.assertFalse(decision.allowed)
        self.assertIn("not declared safe", decision.reason)

    def test_knowledge_connector_keyword_matching(self):
        """KnowledgeConnector must find runbooks by keyword."""
        async def scenario():
            connector = KnowledgeConnector()
            results = await connector.search("export failure after rollout")
            self.assertGreater(len(results), 0)
            titles = [r.title for r in results]
            self.assertTrue(any("export" in t.lower() or "rollout" in t.lower() for t in titles))
            for r in results:
                self.assertGreater(r.confidence, 0.0)
        asyncio.run(scenario())

    def test_pagerduty_connector_webhook_normalization(self):
        """PagerDuty V3 webhook shape must normalize correctly."""
        async def scenario():
            connector = PagerDutyConnector()
            signal = await connector.normalize_event({
                "event": {
                    "event_type": "incident.triggered",
                    "data": {
                        "incident": {
                            "id": "P123ABC",
                            "title": "Database latency spike",
                            "urgency": "high",
                            "status": "triggered",
                            "incident_key": "key-abc-123",
                            "service": {"name": "payments-db", "summary": "Payments DB"},
                        }
                    }
                }
            })
            self.assertEqual(signal.source, "pagerduty")
            self.assertIn("payments-db", signal.entities)
            self.assertEqual(signal.urgency, "critical")
            self.assertEqual(signal.idempotency_key, "pagerduty:key-abc-123")
        asyncio.run(scenario())

    def test_artifact_connector_write_report(self):
        """ArtifactConnector.write must create a markdown file."""
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                connector = ArtifactConnector()
                result = await connector.write("test-report", "# Test\n\nContent here.")
            self.assertEqual(result.status, "complete")
            self.assertIsNotNone(result.artifact_path)
            p = Path(result.artifact_path)
            self.assertTrue(p.exists())
            self.assertIn("Content here", p.read_text())
        asyncio.run(scenario())

    def test_notification_connector_local_fallback(self):
        """NotificationConnector must fallback to local file when SLACK_WEBHOOK_URL unset."""
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            old_slack = os.environ.pop("SLACK_WEBHOOK_URL", None)
            try:
                with patch("autopilot.connectors.actions.ARTIFACT_DIR", tmp):
                    result = await NotificationConnector().action("notify_ops",
                        {"mission_id": "test-123", "text": "Test notification"})
                self.assertEqual(result.status, "complete")
                self.assertIn("local_fallback", result.metadata.get("mode", ""))
            finally:
                if old_slack: os.environ["SLACK_WEBHOOK_URL"] = old_slack
        asyncio.run(scenario())

    def test_state_store_mission_stats_returns_correct_structure(self):
        """StateStore.mission_stats must return a dict with expected keys."""
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = StateStore(tmp / f"autopilot-{uuid4().hex}.db")
        stats = store.mission_stats()
        self.assertIn("total", stats)
        self.assertIn("by_status", stats)
        self.assertIn("avg_confidence", stats)
        self.assertIn("avg_replans", stats)
        self.assertIn("avg_evidence", stats)
        self.assertEqual(stats["total"], 0)

    def test_hmac_webhook_verification_rejects_bad_signature(self):
        """HMAC webhook verification must reject a mismatched signature."""
        body = b'{"event": "test"}'
        secret = "my-test-secret"
        correct_sig = "sha256=" + hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
        wrong_sig = "sha256=deadbeef" + "0" * 56
        # Good signature should verify correctly
        computed = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(computed, correct_sig.replace("sha256=", "")))
        # Wrong signature must fail
        self.assertFalse(hmac.compare_digest(computed, wrong_sig.replace("sha256=", "")))

    def test_signal_entity_correlation_across_missions(self):
        """Two signals sharing an entity should be correlated into the same mission."""
        async def scenario():
            tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
            store = Store(tmp / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry(), correlation_window_seconds=5.0)
            first = await runtime.ingest(Signal(source="support", type="ticket",
                summary="Export service is broken", entities=["export service"], urgency="high"))
            second = await runtime.ingest(Signal(source="monitoring", type="alert",
                summary="Error rate spike", entities=["export service"], urgency="high"))
            await asyncio.sleep(0.1)
            self.assertEqual(first.id, second.id, "Correlated signals must map to same mission")
        asyncio.run(scenario())

    def test_store_list_missions_returns_latest_first(self):
        """list_missions must return missions ordered by updated_at descending."""
        from datetime import datetime, timezone, timedelta
        from autopilot.models import Mission
        tmp = Path.cwd() / ".tmp"; tmp.mkdir(exist_ok=True)
        store = Store(tmp / f"autopilot-{uuid4().hex}.db")
        m1 = Mission(title="First", summary="first",
                     created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                     updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        m2 = Mission(title="Second", summary="second",
                     created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                     updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc))
        store.create_mission(m1)
        store.create_mission(m2)
        missions = store.list_missions()
        titles = [m["title"] for m in missions]
        self.assertEqual(titles[0], "Second")  # most recent first


if __name__ == "__main__":
    unittest.main()
