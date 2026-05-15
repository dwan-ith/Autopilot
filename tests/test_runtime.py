import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["AUTOPILOT_DISABLE_LLM"] = "1"

from autopilot.connectors import default_registry
from autopilot.kernel import RuntimeKernel
from autopilot.models import MissionStatus, Signal
from autopilot.storage import Store


class RuntimeKernelTest(unittest.TestCase):
    def test_correlates_runs_replans_and_publishes_actions(self):
        async def scenario():
            tmp_dir = Path.cwd() / ".tmp"
            tmp_dir.mkdir(exist_ok=True)
            store = Store(tmp_dir / f"autopilot-{uuid4().hex}.db")
            runtime = RuntimeKernel(store, default_registry())

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

            steps = store.list_steps(mission.id)
            self.assertTrue(any(step["name"] == "Verification Gate" for step in steps))
            self.assertTrue(any(step["name"] == "Action Publisher" for step in steps))
            self.assertEqual(len(completed.evidence), len({(ev.source, ev.title, ev.summary[:120]) for ev in completed.evidence}))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
