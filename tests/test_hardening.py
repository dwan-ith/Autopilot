"""Regression tests for the hardening pass.

Each test pins a specific defect fixed during the security/rigor audit:
governor escalation-only LLM governance, deployment self-approval removal,
approval TOCTOU double-execution, terminal-state stickiness, signal dedup
races, fail-closed webhooks, OAuth state integrity, SSRF guards, honest
connector evidence, JSON salvage parsing, validator whitelisting, trace
retention, SSE change detection, and the WAITING-mission continuation route.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["AUTOPILOT_DISABLE_LLM"] = "1"
os.environ.pop("AUTOPILOT_AUTO_APPROVE_ACTIONS", None)

import httpx

from fastapi.testclient import TestClient

import autopilot.api.main as api_main
from autopilot.agents.mission.validator import _sanitize_resolution
from autopilot.agents.persistent.governor import GovernorAgent
from autopilot.agents.specialized import CloudInfraAgent
from autopilot.connectors import default_registry
from autopilot.connectors._ssrf import UnsafeDestinationError, assert_public_http_url
from autopilot.connectors.actions import NotificationConnector
from autopilot.connectors.base import Connector
from autopilot.connectors.github_connector import GitHubConnector
from autopilot.connectors.knowledge import KnowledgeConnector
from autopilot.connectors.oauth import build_oauth_state, verify_oauth_state
from autopilot.connectors.weather import WeatherConnector
from autopilot.kernel import RuntimeKernel
from autopilot.models import (
    ActionApproval,
    ActionResult,
    ApprovalStatus,
    Capability,
    ConnectorManifest,
    Mission,
    MissionStatus,
    Signal,
)
from autopilot.operators.llm import parse_json
from autopilot.policy import PolicyEngine
from autopilot.storage import Store


def _tmp_store() -> Store:
    tmp = Path.cwd() / ".tmp"
    tmp.mkdir(exist_ok=True)
    return Store(tmp / f"autopilot-{uuid4().hex}.db")


class FakeDeployConnector(Connector):
    manifest = ConnectorManifest(
        name="fake_deploy",
        description="Test deployment connector.",
        capabilities=[Capability.ACTION],
        safe_actions=["trigger_deployment"],
        auth_required=False,
    )

    async def action(self, name, payload):
        return ActionResult(connector=self.manifest.name, action=name, status="complete",
                            summary=f"Deployed {payload.get('environment', '?')}")


class SlowActionConnector(Connector):
    """Records every execution so double-execution attempts are observable."""

    manifest = ConnectorManifest(
        name="slow_fake",
        description="Test connector with observable side effects.",
        capabilities=[Capability.ACTION],
        safe_actions=["do_thing"],
        auth_required=False,
    )
    executions = 0

    async def action(self, name, payload):
        SlowActionConnector.executions += 1
        await asyncio.sleep(0.05)
        return ActionResult(connector=self.manifest.name, action=name, status="complete",
                            summary="side effect happened")


class MediumRiskConnector(Connector):
    """MEDIUM-risk action surface for governor borderline tests."""

    manifest = ConnectorManifest(
        name="medium_fake",
        description="Test connector with a confidence-gated action.",
        capabilities=[Capability.ACTION],
        safe_actions=["create_issue"],
        auth_required=False,
    )


class GovernorEscalationTests(unittest.TestCase):

    def test_llm_allow_verdict_cannot_unlock_blocked_action(self):
        """A borderline-blocked action stays blocked even when the review LLM says allow."""
        governor = GovernorAgent()
        mission = Mission(title="borderline", confidence=0.80)
        connector = MediumRiskConnector()
        with patch.object(GovernorAgent, "_llm_review", new=AsyncMock(return_value={"decision": "allow", "reason": "seems fine"})):
            decision = asyncio.run(governor.decide_async(mission, connector, "create_issue"))
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_validation)
        self.assertIn("human approval", decision.reason)

    def test_llm_not_consulted_far_below_threshold(self):
        governor = GovernorAgent()
        mission = Mission(title="low", confidence=0.30)
        connector = MediumRiskConnector()
        mock_review = AsyncMock(return_value={"decision": "allow"})
        with patch.object(GovernorAgent, "_llm_review", new=mock_review):
            decision = asyncio.run(governor.decide_async(mission, connector, "create_issue"))
        self.assertFalse(decision.allowed)
        self.assertEqual(mock_review.await_count, 0)

    def test_sync_decide_is_pure_deterministic(self):
        governor = GovernorAgent()
        mission = Mission(title="sync", confidence=0.80)
        connector = MediumRiskConnector()
        decision = governor.decide(mission, connector, "create_issue")
        self.assertFalse(decision.allowed)


class DeploymentGateTests(unittest.TestCase):

    def test_high_risk_deployment_queues_approval_and_never_self_executes(self):
        async def scenario():
            store = _tmp_store()
            registry = default_registry()
            runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
            mission = await runtime.ingest(Signal(
                source="support_webhook", type="support_escalation",
                summary="Enterprise customer reports failed exports after rollout.",
                entities=["export"], urgency="high"))
            await runtime.wait_for(mission.id)

            agent = CloudInfraAgent(store=store, registry=registry)
            result = await agent.trigger_deployment(mission_id=mission.id, environment="staging")
            self.assertEqual(result.status, "pending_approval", result.summary)

            reloaded = store.get_mission(mission.id)
            queued = [a for a in reloaded.approvals if a.action == "trigger_deployment"]
            self.assertTrue(queued, "deployment must leave a traceable approval record")
            self.assertEqual(queued[0].status, ApprovalStatus.PENDING)
            # No deployment side effect ran autonomously.
            self.assertFalse(any(a.action == "trigger_deployment" and a.status == "complete"
                                 for a in reloaded.actions))
        asyncio.run(scenario())

    def test_deployment_without_mission_scope_is_refused(self):
        async def scenario():
            store = _tmp_store()
            agent = CloudInfraAgent(store=store, registry=default_registry())
            result = await agent.trigger_deployment(mission_id=None)
            self.assertEqual(result.status, "blocked")
            self.assertIn("mission_id", result.summary)
        asyncio.run(scenario())


class ApprovalAtomicityTests(unittest.TestCase):

    def test_concurrent_approvals_execute_side_effect_once(self):
        old_key = os.environ.pop("AUTOPILOT_API_KEY", None)
        old_store, old_registry = api_main.store, api_main.registry
        try:
            store = _tmp_store()
            registry = default_registry(include_mcp=False)
            connector = SlowActionConnector()
            registry.register(connector)
            SlowActionConnector.executions = 0

            mission = Mission(title="approval race")
            mission.approvals.append(ActionApproval(
                mission_id=mission.id, connector="slow_fake", action="do_thing",
                risk="medium", reason="queued by policy",
            ))
            store.create_mission(mission)
            api_main.store, api_main.registry = store, registry

            async def fire():
                transport = httpx.ASGITransport(app=api_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    return await asyncio.gather(
                        client.post(f"/api/approvals/{mission.approvals[0].id}/approve", json={"decided_by": "op1"}),
                        client.post(f"/api/approvals/{mission.approvals[0].id}/approve", json={"decided_by": "op2"}),
                    )

            r1, r2 = asyncio.run(fire())
            statuses = sorted([r1.status_code, r2.status_code])
            self.assertEqual(statuses, [200, 409], f"expected exactly one execution, got {r1.status_code}/{r2.status_code}")
            self.assertEqual(r1.json()["status"] if r1.status_code == 200 else r2.json()["status"], "executed")
            self.assertEqual(SlowActionConnector.executions, 1, "side effect must run exactly once")
            reloaded = store.get_mission(mission.id)
            self.assertEqual(reloaded.approvals[0].status, ApprovalStatus.EXECUTED)
        finally:
            api_main.store, api_main.registry = old_store, old_registry
            if old_key is not None:
                os.environ["AUTOPILOT_API_KEY"] = old_key


class StorageIntegrityTests(unittest.TestCase):

    def test_terminal_status_is_sticky(self):
        store = _tmp_store()
        mission = Mission(title="done", status=MissionStatus.COMPLETE)
        from autopilot.models import utc_now
        mission.completed_at = utc_now()
        store.create_mission(mission)

        stale = store.get_mission(mission.id)
        stale.status = MissionStatus.RUNNING
        stale.completed_at = None
        store.update_mission(stale)

        reloaded = store.get_mission(mission.id)
        self.assertEqual(reloaded.status, MissionStatus.COMPLETE)
        self.assertIsNotNone(reloaded.completed_at)

    def test_duplicate_signal_key_ingests_to_one_mission(self):
        async def scenario():
            store = _tmp_store()
            runtime = RuntimeKernel(store, default_registry(include_mcp=False), correlation_window_seconds=0.05)
            s1 = Signal(source="webhook", type="event", summary="dup event", idempotency_key="race-key-1")
            s2 = Signal(source="webhook", type="event", summary="dup event retry", idempotency_key="race-key-1")
            m1, m2 = await asyncio.gather(runtime.ingest(s1), runtime.ingest(s2))
            self.assertEqual(m1.id, m2.id, "same idempotency key must correlate to one mission")
        asyncio.run(scenario())

    def test_stream_version_changes_on_write(self):
        store = _tmp_store()
        v1 = store.stream_version()
        store.create_mission(Mission(title="version probe"))
        self.assertNotEqual(v1, store.stream_version())

    def test_prune_stale_traces_removes_only_old_rows(self):
        from contextlib import closing

        store = _tmp_store()
        store.trace(None, "old_event", "complete", {})
        store.trace(None, "fresh_event", "complete", {})
        with closing(store.connect()) as conn, conn:
            conn.execute(
                "update trace_events set created_at = datetime('now', '-30 days') where name='old_event'"
            )
        pruned = store.prune_stale_traces(days=7)
        self.assertEqual(pruned, 1)
        names = {row.get("name") for row in store.list_traces(limit=100)}
        self.assertNotIn("old_event", names)
        self.assertIn("fresh_event", names)


class WebhookAuthTests(unittest.TestCase):

    def _swap_runtime(self, store=None):
        old = (api_main.store, api_main.registry, api_main.runtime)
        store = store or _tmp_store()
        registry = default_registry(include_mcp=False)
        api_main.store = store
        api_main.registry = registry
        api_main.runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
        return old

    def _restore(self, old, key_env):
        api_main.store, api_main.registry, api_main.runtime = old
        for name, value in key_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_webhook_fails_closed_without_secret_or_key(self):
        saved = {n: os.environ.get(n) for n in ("AUTOPILOT_API_KEY", "AUTOPILOT_WEBHOOK_SECRET")}
        for n in saved:
            os.environ.pop(n, None)
        old = self._swap_runtime()
        try:
            client = TestClient(api_main.app)
            response = client.post("/webhooks/jira", json={"webhookEvent": "jira:issue_updated"})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn("refused", response.json()["detail"].lower())
        finally:
            self._restore(old, saved)

    def test_signed_webhook_accepted_and_unsigned_rejected_when_secret_set(self):
        secret = "whsec-test-123"
        saved = {n: os.environ.get(n) for n in ("AUTOPILOT_API_KEY", "AUTOPILOT_WEBHOOK_SECRET")}
        os.environ["AUTOPILOT_WEBHOOK_SECRET"] = secret
        os.environ.pop("AUTOPILOT_API_KEY", None)
        old = self._swap_runtime()
        try:
            client = TestClient(api_main.app)
            body = json.dumps({"webhookEvent": "jira:issue_updated", "summary": "signed incident"}).encode()
            bad_sig = "sha256=" + "0" * 64
            rejected = client.post("/webhooks/jira", content=body,
                                   headers={"Content-Type": "application/json", "X-Autopilot-Signature": bad_sig})
            self.assertEqual(rejected.status_code, 401)

            digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
            accepted = client.post("/webhooks/jira", content=body,
                                   headers={"Content-Type": "application/json", "X-Autopilot-Signature": f"sha256={digest}"})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertTrue(accepted.json()["mission_id"].startswith("mission_"))
        finally:
            self._restore(old, saved)


class OAuthStateTests(unittest.TestCase):

    def test_roundtrip_and_tamper_rejection(self):
        token = build_oauth_state("google", "gmail")
        self.assertEqual(verify_oauth_state(token, "google"), "gmail")

        with self.assertRaises(ValueError, msg="tampered token must fail"):
            verify_oauth_state(token[:-4] + "AAAA", "google")
        with self.assertRaises(ValueError, msg="provider mismatch must fail"):
            verify_oauth_state(token, "github")
        with self.assertRaises(ValueError):
            verify_oauth_state(None, "google")
        with self.assertRaises(ValueError):
            verify_oauth_state("not-a-token", "github")


class SsrfGuardTests(unittest.TestCase):

    def test_blocks_protected_literal_targets_and_schemes(self):
        for bad in [
            "http://127.0.0.1:8080/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost/run",
            "http://[::1]:9000/",
            "ftp://public.example.com/file",
            "file:///etc/passwd",
        ]:
            with self.assertRaises(UnsafeDestinationError, msg=bad):
                assert_public_http_url(bad)

    def test_blocks_hostname_resolving_to_private_address(self):
        fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with self.assertRaises(UnsafeDestinationError):
                assert_public_http_url("http://internal.corp.example/hook")

    def test_allows_public_host(self):
        public_ip = str(ipaddress.ip_address("93.184.216.34"))
        fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, 443))]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            host = assert_public_http_url("https://api.example.com/webhook")
        self.assertEqual(host, "api.example.com")

    def test_notification_connector_blocks_internal_callback(self):
        result = asyncio.run(NotificationConnector().action(
            "webhook_callback", {"callback_url": "http://127.0.0.1:9/x", "text": "hi"}))
        self.assertEqual(result.status, "blocked")
        self.assertIn("SSRF guard", result.summary)


class HonestEvidenceTests(unittest.TestCase):

    def test_weather_missing_location_errors_instead_of_defaulting_to_london(self):
        data = asyncio.run(WeatherConnector().read(""))
        self.assertIn("error", data)
        self.assertNotIn("London", json.dumps(data))

    def test_github_search_failure_is_tool_error_not_empty(self):
        async def boom(self, client, query, headers):
            raise httpx.ConnectError("network down")

        with patch.object(GitHubConnector, "_search_issues", new=boom):
            evidence = asyncio.run(GitHubConnector().search("anything"))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].metadata.get("kind"), "tool_error")
        self.assertEqual(evidence[0].confidence, 0.0)

    def test_knowledge_runbooks_are_confidence_capped_and_tagged(self):
        old_key = os.environ.pop("TAVILY_API_KEY", None)
        try:
            evidence = asyncio.run(KnowledgeConnector().search(
                "export failure after rollout deployment schema"))
            runbooks = [e for e in evidence if e.metadata.get("kind") == "local_runbook"]
            self.assertTrue(runbooks)
            for item in runbooks:
                self.assertLessEqual(item.confidence, 0.72)
                self.assertEqual(item.metadata.get("provenance"), "curated_static_runbook")
        finally:
            if old_key is not None:
                os.environ["TAVILY_API_KEY"] = old_key


class ParsingAndValidationTests(unittest.TestCase):

    def test_safe_int_handles_dashed_and_word_inputs(self):
        """'p-3' used to raise UnboundLocalError inside the digit-extraction genexp."""
        from autopilot.agents.validation import safe_int
        self.assertEqual(safe_int("p-3", 99, lo=1, hi=999), 3)
        self.assertEqual(safe_int("priority 5", 1), 5)
        self.assertEqual(safe_int("high", 7), 20)  # severity words rank ×10
        self.assertEqual(safe_int(None, 4), 4)
        self.assertEqual(safe_int("no digits", 8), 8)

    def test_parse_json_salvages_first_complete_value(self):
        self.assertEqual(parse_json('noise {"a": 1} tail {"b": 2}'), {"a": 1})
        self.assertEqual(parse_json("[1, 2] trailing"), [1, 2])
        self.assertIsNone(parse_json("no json here"))

    def test_validator_resolution_whitelist(self):
        self.assertIsNone(_sanitize_resolution({"resolution_status": "totally_fixed"}))
        self.assertIsNone(_sanitize_resolution("resolved"))
        sanitized = _sanitize_resolution({
            "resolution_status": "Partially-Resolved",
            "all_actions_succeeded": "yes",
            "follow_up_actions": ["check logs", "notify owner"],
        })
        self.assertEqual(sanitized["resolution_status"], "partially_resolved")
        self.assertTrue(sanitized["follow_up_needed"])
        self.assertEqual(len(sanitized["follow_up_actions"]), 2)


class ContinuationRouteTests(unittest.TestCase):

    def _make_waiting_mission(self, store, continuation_value):
        mission = Mission(title="paused delivery", status=MissionStatus.WAITING, confidence=0.9)
        mission.pipeline.update({
            "defer_delivery": True,
            "delivery_pending": True,
            "continuation_secret": continuation_value,
        })
        store.create_mission(mission)
        return mission

    def test_continue_requires_correct_secret_then_resumes(self):
        old_key = os.environ.pop("AUTOPILOT_API_KEY", None)
        old = (api_main.store, api_main.runtime)
        try:
            store = _tmp_store()
            registry = default_registry(include_mcp=False)
            api_main.store = store
            api_main.runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
            mission = self._make_waiting_mission(store, continuation_value="tok-abc")
            done = Mission(title="already finished", status=MissionStatus.COMPLETE)
            done.pipeline.update({"continuation_secret": "tok-done"})
            store.create_mission(done)

            client = TestClient(api_main.app)
            self.assertEqual(
                client.post(f"/api/missions/{mission.id}/continue", json={"continuation_secret": "nope"}).status_code,
                403,
            )
            self.assertEqual(client.post("/api/missions/does-not-exist/continue", json={"continuation_secret": "x"}).status_code, 404)
            self.assertEqual(
                client.post(f"/api/missions/{done.id}/continue", json={"continuation_secret": "tok-done"}).status_code,
                409,
            )

            resumed = client.post(f"/api/missions/{mission.id}/continue", json={"continuation_secret": "tok-abc"})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertTrue(resumed.json()["resumed"])
            reloaded = store.get_mission(mission.id)
            self.assertNotEqual(reloaded.status, MissionStatus.WAITING)
        finally:
            api_main.store, api_main.runtime = old
            if old_key is not None:
                os.environ["AUTOPILOT_API_KEY"] = old_key


class EnvGuardTests(unittest.TestCase):

    def test_production_exposed_bind_requires_api_key(self):
        saved = {n: os.environ.get(n) for n in (
            "AUTOPILOT_ENV", "AUTOPILOT_HOST", "AUTOPILOT_API_KEY", "AUTOPILOT_CREDENTIAL_ENCRYPTION_KEY")}
        try:
            os.environ["AUTOPILOT_ENV"] = "production"
            os.environ["AUTOPILOT_HOST"] = "0.0.0.0"  # noqa: S104 — the test subject IS the exposed-bind guard
            os.environ["AUTOPILOT_CREDENTIAL_ENCRYPTION_KEY"] = "unit-test-key"
            os.environ.pop("AUTOPILOT_API_KEY", None)
            from autopilot.env_check import validate_env
            with self.assertRaises(OSError):
                validate_env()

            os.environ["AUTOPILOT_API_KEY"] = "present-now"
            validate_env()  # must not raise
        finally:
            for n, v in saved.items():
                if v is None:
                    os.environ.pop(n, None)
                else:
                    os.environ[n] = v


if __name__ == "__main__":
    unittest.main()
