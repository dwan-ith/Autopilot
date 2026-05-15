from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import GraphNodeKind, Mission, MissionGraphNode, MissionStatus, OperatorStep, Signal, StepStatus, utc_now
from autopilot.operators import OperatorSuite
from autopilot.storage import Store
from autopilot.tracing import TraceSink
from autopilot.orchestrator import OrchestratorAgent
from autopilot.connectors.maas import MAASConnectorRegistry
from autopilot.models import AgentTask, ActionRecord
import logging

logger = logging.getLogger(__name__)


class RuntimeKernel:
<<<<<<< HEAD
    def __init__(self, store: Store, registry: ConnectorRegistry, maas_registry: MAASConnectorRegistry | None = None):
=======
    def __init__(self, store: Store, registry: ConnectorRegistry, correlation_window_seconds: float = 0.45):
>>>>>>> 7e86d18fb019511de1ac9377bbddc4d936f91751
        self.store = store
        self.registry = registry
        self.operators = OperatorSuite(registry)
        self.tracer = TraceSink(store)
        self._tasks: dict[str, asyncio.Task] = {}
<<<<<<< HEAD
        self.agent_registry = maas_registry
        self._agent_queue: asyncio.Queue[AgentTask] = asyncio.Queue()
        self.orchestrator: OrchestratorAgent | None = None
        if maas_registry is not None:
            self.orchestrator = OrchestratorAgent(maas_registry, store, self._agent_queue)
        self._agent_worker_task: asyncio.Task | None = None
=======
        self._mission_locks: dict[str, asyncio.Lock] = {}
        self.correlation_window_seconds = correlation_window_seconds
>>>>>>> 7e86d18fb019511de1ac9377bbddc4d936f91751

    async def ingest(self, signal: Signal) -> Mission:
        self.store.save_signal(signal)
        mission = self._correlate(signal)
        if mission:
            mission.signals.append(signal)
            mission.graph.append(
                MissionGraphNode(
                    kind=GraphNodeKind.SIGNAL,
                    title=f"Correlated signal: {signal.type}",
                    status=StepStatus.COMPLETE,
                    summary=signal.summary,
                    ref_id=signal.id,
                    completed_at=utc_now(),
                    metadata={"source": signal.source, "entities": signal.entities},
                )
            )
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
                graph=[
                    MissionGraphNode(
                        kind=GraphNodeKind.SIGNAL,
                        title=f"Initial signal: {signal.type}",
                        status=StepStatus.COMPLETE,
                        summary=signal.summary,
                        ref_id=signal.id,
                        completed_at=utc_now(),
                        metadata={"source": signal.source, "entities": signal.entities},
                    )
                ],
            )
            self.store.create_mission(mission)
            self.tracer.emit(mission.id, "mission.created", "complete", {"signal": signal.model_dump()})

        self.schedule(mission.id)
        # Route MAAS agent tasks if orchestrator present
        if self.orchestrator:
            tasks = self.orchestrator.route(signal, mission)
            await self.orchestrator.dispatch(tasks)
        return mission

    def start_agent_workers(self, num_workers: int = 1) -> None:
        if not self.orchestrator:
            return
        if self._agent_worker_task and not self._agent_worker_task.done():
            return
        self._agent_worker_task = asyncio.create_task(self._agent_worker())

    async def _agent_worker(self) -> None:
        if not self.orchestrator or not self.agent_registry:
            return
        while True:
            task: AgentTask = await self._agent_queue.get()
            try:
                # update status -> running
                task.status = "running"
                task.updated_at = utc_now()
                try:
                    self.store.save_agent_task(task)
                except Exception:
                    logger.exception("Failed to persist agent task running status")

                allowed = self.orchestrator.enforce_policy(task)
                if not allowed:
                    task.status = "failed"
                    task.updated_at = utc_now()
                    self.store.save_agent_task(task)
                    self.tracer.emit(task.mission_id, "agent.task.denied", "failed", {"task": task.model_dump()})
                    continue

                agent = self.agent_registry.get(task.agent_type)
                if not agent:
                    task.status = "failed"
                    task.updated_at = utc_now()
                    self.store.save_agent_task(task)
                    self.tracer.emit(task.mission_id, "agent.task.missing", "failed", {"task": task.model_dump()})
                    continue

                # invoke agent
                record: ActionRecord = await agent.handle(task)
                # attach mission_id into payload for querying
                try:
                    if isinstance(record.payload, dict):
                        record.payload.setdefault("mission_id", task.mission_id)
                except Exception:
                    pass
                # persist record
                try:
                    self.store.save_action_record(record)
                except Exception:
                    logger.exception("Failed to persist action record %s", record.id)

                task.status = "done"
                task.updated_at = utc_now()
                self.store.save_agent_task(task)
                self.tracer.emit(task.mission_id, "agent.task.complete", "complete", {"task": task.model_dump(), "record": record.model_dump()})
            except Exception as exc:
                logger.exception("Error running agent task: %s", exc)
                task.status = "failed"
                task.updated_at = utc_now()
                try:
                    self.store.save_agent_task(task)
                except Exception:
                    pass
            finally:
                self._agent_queue.task_done()

    def schedule(self, mission_id: str) -> None:
        task = self._tasks.get(mission_id)
        if task and not task.done():
            return
        self._tasks[mission_id] = asyncio.create_task(self.run_mission(mission_id))

    async def wait_for(self, mission_id: str) -> None:
        task = self._tasks.get(mission_id)
        if task:
            await task

    def resume_active(self) -> None:
        for mission in self.store.active_missions():
            self.schedule(mission.id)

    async def run_mission(self, mission_id: str) -> None:
        lock = self._mission_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            await self._run_mission_locked(mission_id)

    async def _run_mission_locked(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if not mission:
            return
        try:
            mission.status = MissionStatus.RUNNING
            self.store.update_mission(mission)
            self.tracer.emit(mission.id, "runtime.dispatch", "started", {"mission": mission.title})
            await asyncio.sleep(self.correlation_window_seconds)
            mission = self.store.get_mission(mission_id) or mission

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

            if len(mission.signals) > 1 and mission.replans < 1:
                async with self.step(mission, "Adaptive Replanner", "revise mission graph after correlated signals arrived mid-execution") as step:
                    mission = await self.operators.replan(
                        mission,
                        "New correlated signals arrived while the mission was running; spawn follow-up investigation over the expanded incident context.",
                    )
                    step.output_summary = f"Replan #{mission.replans}: expanded mission graph for {len(mission.signals)} correlated signals."
                    self.store.update_mission(mission)

                async with self.step(mission, "Follow-up Subagent", "investigate the revised mission graph before final verification") as step:
                    mission = await self.operators.investigate(mission)
                    step.output_summary = f"Follow-up collected additional evidence; total evidence={len(mission.evidence)}."
                    step.metadata = {"evidence_count": len(mission.evidence)}
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
                step.metadata = {
                    "actions": [action.model_dump() for action in mission.actions],
                    "policy_decisions": [decision.model_dump() for decision in mission.policy_decisions],
                }
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
        graph_node = MissionGraphNode(
            kind=GraphNodeKind.REPLAN if "Replanner" in name else GraphNodeKind.OPERATOR,
            title=name,
            ref_id=step.id,
            summary=role,
        )
        mission.graph.append(graph_node)
        self.store.update_mission(mission)
        self.store.add_step(step)
        self.tracer.emit(mission.id, f"operator.{name.lower().replace(' ', '_')}.start", "started", {"role": role}, step.id)
        try:
            yield step
            step.status = StepStatus.COMPLETE
            step.completed_at = utc_now()
            graph_node.status = StepStatus.COMPLETE
            graph_node.completed_at = step.completed_at
            graph_node.summary = step.output_summary or role
            graph_node.metadata = step.metadata
            self.store.update_mission(mission)
            self.store.add_step(step)
            self.tracer.emit(mission.id, f"operator.{name.lower().replace(' ', '_')}.complete", "complete", step.model_dump(), step.id)
        except Exception as exc:
            step.status = StepStatus.FAILED
            step.completed_at = utc_now()
            step.output_summary = str(exc)
            graph_node.status = StepStatus.FAILED
            graph_node.completed_at = step.completed_at
            graph_node.summary = str(exc)
            self.store.update_mission(mission)
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
