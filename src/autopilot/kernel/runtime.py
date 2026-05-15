from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import Mission, MissionStatus, OperatorStep, Signal, StepStatus, utc_now
from autopilot.operators import OperatorSuite
from autopilot.storage import Store
from autopilot.tracing import TraceSink


class RuntimeKernel:
    def __init__(self, store: Store, registry: ConnectorRegistry):
        self.store = store
        self.registry = registry
        self.operators = OperatorSuite(registry)
        self.tracer = TraceSink(store)
        self._tasks: dict[str, asyncio.Task] = {}

    async def ingest(self, signal: Signal) -> Mission:
        self.store.save_signal(signal)
        mission = self._correlate(signal)
        if mission:
            mission.signals.append(signal)
            mission.status = MissionStatus.RUNNING
            mission.summary = f"Correlated new {signal.type} signal into existing mission."
            self.store.update_mission(mission)
            self.tracer.emit(mission.id, "signal.correlated", "complete", {"signal": signal.model_dump(), "mission": mission.id})
        else:
            mission = Mission(
                title=self._title_for(signal),
                status=MissionStatus.QUEUED,
                severity=signal.urgency,
                summary=signal.summary,
                signals=[signal],
            )
            self.store.create_mission(mission)
            self.tracer.emit(mission.id, "mission.created", "complete", {"signal": signal.model_dump()})

        self.schedule(mission.id)
        return mission

    def schedule(self, mission_id: str) -> None:
        task = self._tasks.get(mission_id)
        if task and not task.done():
            return
        self._tasks[mission_id] = asyncio.create_task(self.run_mission(mission_id))

    def resume_active(self) -> None:
        for mission in self.store.active_missions():
            self.schedule(mission.id)

    async def run_mission(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if not mission:
            return
        try:
            mission.status = MissionStatus.RUNNING
            self.store.update_mission(mission)
            self.tracer.emit(mission.id, "runtime.dispatch", "started", {"mission": mission.title})

            async with self.step(mission, "Signal Evaluator", "classify severity, entities, and operational importance") as step:
                mission = await self.operators.evaluate_signal(mission)
                step.output_summary = mission.summary
                self.store.update_mission(mission)

            async with self.step(mission, "Mission Planner", "spawn hypotheses and scoped investigation branches") as step:
                mission = await self.operators.plan_mission(mission)
                step.output_summary = f"Spawned {len(mission.hypotheses)} hypotheses."
                step.metadata = {"hypotheses": [hyp.model_dump() for hyp in mission.hypotheses]}
                self.store.update_mission(mission)

            async with self.step(mission, "Dynamic Subagents", "investigate hypotheses through capability-routed connectors") as step:
                mission = await self.operators.investigate(mission)
                step.output_summary = f"Collected {len(mission.evidence)} evidence item(s)."
                step.metadata = {"evidence": [ev.model_dump() for ev in mission.evidence]}
                self.store.update_mission(mission)

            mission = self.store.get_mission(mission_id) or mission
            mission = await self.operators.evaluate_signal(mission)
            self.store.update_mission(mission)
            async with self.step(mission, "Verification Gate", "score confidence and decide whether to replan") as step:
                mission, needs_replan = await self.operators.verify(mission)
                step.output_summary = f"Confidence={mission.confidence:.2f}; needs_replan={needs_replan}."
                step.metadata = {"confidence": mission.confidence, "needs_replan": needs_replan}
                self.store.update_mission(mission)

            if needs_replan:
                async with self.step(mission, "Adaptive Replanner", "spawn follow-up branch because confidence is below threshold") as step:
                    mission = await self.operators.replan(mission)
                    step.output_summary = f"Replan #{mission.replans}: added follow-up evidence branch."
                    self.store.update_mission(mission)

                async with self.step(mission, "Follow-up Subagent", "run targeted investigation from revised mission graph") as step:
                    mission = await self.operators.investigate(mission)
                    mission, _ = await self.operators.verify(mission)
                    step.output_summary = f"After follow-up, confidence={mission.confidence:.2f}."
                    step.metadata = {"confidence": mission.confidence, "evidence_count": len(mission.evidence)}
                    self.store.update_mission(mission)

            async with self.step(mission, "Synthesis Operator", "produce verified operational action packet") as step:
                brief = await self.operators.synthesize(mission)
                step.output_summary = "Generated mission brief."
                step.metadata = {"brief_preview": brief[:500]}

            async with self.step(mission, "Action Publisher", "execute bounded writes and notifications") as step:
                mission.actions.extend(await self.operators.publish_actions(mission, brief))
                step.output_summary = f"Executed {len(mission.actions)} bounded action(s)."
                step.metadata = {"actions": [action.model_dump() for action in mission.actions]}
                mission.status = MissionStatus.COMPLETE
                mission.completed_at = utc_now()
                self.store.update_mission(mission)
                self.store.remember("mission_resolution", f"{mission.title}: confidence {mission.confidence:.2f}; actions {len(mission.actions)}")

            self.tracer.emit(mission.id, "mission.complete", "complete", {"confidence": mission.confidence, "actions": len(mission.actions)})
        except Exception as exc:
            mission = self.store.get_mission(mission_id) or mission
            mission.status = MissionStatus.FAILED
            mission.summary = f"Runtime failed: {exc}"
            self.store.update_mission(mission)
            self.tracer.emit(mission_id, "mission.failed", "failed", {"error": str(exc)})

    @asynccontextmanager
    async def step(self, mission: Mission, name: str, role: str) -> AsyncIterator[OperatorStep]:
        step = OperatorStep(mission_id=mission.id, name=name, role=role, input_summary=mission.summary)
        self.store.add_step(step)
        self.tracer.emit(mission.id, f"operator.{name.lower().replace(' ', '_')}.start", "started", {"role": role}, step.id)
        try:
            yield step
            step.status = StepStatus.COMPLETE
            step.completed_at = utc_now()
            self.store.add_step(step)
            self.tracer.emit(mission.id, f"operator.{name.lower().replace(' ', '_')}.complete", "complete", step.model_dump(), step.id)
        except Exception as exc:
            step.status = StepStatus.FAILED
            step.completed_at = utc_now()
            step.output_summary = str(exc)
            self.store.add_step(step)
            self.tracer.emit(mission.id, f"operator.{name.lower().replace(' ', '_')}.failed", "failed", {"error": str(exc)}, step.id)
            raise

    def _correlate(self, signal: Signal) -> Mission | None:
        signal_entities = {entity.lower() for entity in signal.entities}
        if not signal_entities:
            return None
        for mission in self.store.active_missions():
            mission_entities = {entity.lower() for entity in self.operators.entities(mission)}
            if signal_entities & mission_entities:
                return mission
        return None

    def _title_for(self, signal: Signal) -> str:
        subject = signal.entities[0] if signal.entities else signal.type.replace("_", " ")
        return f"{subject}: {signal.summary[:80]}"
