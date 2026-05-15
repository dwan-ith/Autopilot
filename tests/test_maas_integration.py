import asyncio
import unittest
from pathlib import Path
from uuid import uuid4

from autopilot.connectors import default_registry, default_maas_registry
from autopilot.kernel import RuntimeKernel
from autopilot.models import Signal, AgentType
from autopilot.storage import Store


class MAASIntegrationTest(unittest.TestCase):
    def test_github_push_routes_to_expected_agents(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            registry = default_registry()
            maas = default_maas_registry(store)
            runtime = RuntimeKernel(store, registry, maas)
            runtime.start_agent_workers()

            sig = Signal(
                source="github_webhook",
                type="push",
                summary="New push to main branch",
                entities=["repo"],
                urgency="medium",
                payload={"ref": "refs/heads/main"},
                source_platform="github",
            )
            mission = await runtime.ingest(sig)
            # wait for agent tasks to be processed
            await asyncio.sleep(0.5)
            tasks = store.get_agent_tasks(mission.id)
            types = {t.agent_type for t in tasks}
            self.assertIn(AgentType.GITHUB, types)
            self.assertIn(AgentType.PROJECT_MGMT, types)
            self.assertIn(AgentType.COMMUNICATION, types)

        asyncio.run(scenario())

    def test_cost_anomaly_routes_cloud_and_security(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            registry = default_registry()
            maas = default_maas_registry(store)
            runtime = RuntimeKernel(store, registry, maas)
            runtime.start_agent_workers()

            sig = Signal(
                source="billing_monitor",
                type="cost_anomaly",
                summary="Unexpected AWS spend spike",
                entities=["billing"],
                urgency="high",
                payload={"amount": 9999},
                source_platform="aws",
            )
            mission = await runtime.ingest(sig)
            await asyncio.sleep(0.5)
            tasks = store.get_agent_tasks(mission.id)
            types = {t.agent_type for t in tasks}
            self.assertIn(AgentType.CLOUD_INFRA, types)
            self.assertIn(AgentType.COMMUNICATION, types)
            self.assertIn(AgentType.SECURITY_AUDIT, types)

    def test_action_records_persisted_for_completed_tasks(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            registry = default_registry()
            maas = default_maas_registry(store)
            runtime = RuntimeKernel(store, registry, maas)
            runtime.start_agent_workers()

            sig = Signal(
                source="github_webhook",
                type="push",
                summary="New push to main branch",
                entities=["repo"],
                urgency="medium",
                payload={"ref": "refs/heads/main"},
                source_platform="github",
            )
            mission = await runtime.ingest(sig)
            await asyncio.sleep(0.5)
            records = store.get_action_records(mission.id)
            # action records for stubs should exist
            self.assertGreaterEqual(len(records), 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
