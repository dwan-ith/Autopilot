"""AUTOPILOT RuntimeKernel — 9-agent autonomous operator runtime.

Persistent agents live here for the lifetime of the kernel:
  - Correlator   classifies and correlates incoming signals
  - Verifier     scores evidence quality and confidence
  - Governor     enforces action policy (wraps PolicyEngine)
  - Memory       stores/recalls cross-mission patterns

The Orchestrator role is played by the RuntimeKernel itself:
  it owns the mission graph, schedules tasks, handles the replan loop,
  and coordinates the mission subagents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from autopilot.agents.persistent.memory import MemoryAgent
from autopilot.connectors.base import ConnectorRegistry
from autopilot.models import (
    Capability,
    GraphNodeKind,
    Mission,
    MissionGraphNode,
    MissionStatus,
    OperatorStep,
    Signal,
    StepStatus,
    utc_now,
)
from autopilot.operators.core import OperatorSuite
from autopilot.storage import Store
from autopilot.tracing import TraceSink

log = logging.getLogger("autopilot.runtime")


class RuntimeKernel:
    """Orchestrates the full 9-agent mission lifecycle."""

    def __init__(
        self,
        store: Store,
        registry: ConnectorRegistry,
        correlation_window_seconds: float = 0.45,
    ):
        self.store = store
        self.registry = registry
        self.correlation_window_seconds = correlation_window_seconds
        self.tracer = TraceSink(store)

        # Wire Omium sink into the SubAgent module so every tool call emits a trace
        from autopilot.agents.base import set_omium_sink
        set_omium_sink(self.tracer)

        # Persistent Memory agent (needs Store)
        self.memory = MemoryAgent(store)

        # OperatorSuite holds the other persistent agents + wires subagents
        self.operators = OperatorSuite(registry, memory_agent=self.memory)

        self._tasks: dict[str, asyncio.Task] = {}
        self._mission_locks: dict[str, asyncio.Lock] = {}

    # ── Signal ingestion ────────────────────────────────────────────────────

    async def ingest(self, signal: Signal) -> Mission:
        signal.idempotency_key = signal.idempotency_key or self._signal_key(signal)
        duplicate = self.store.mission_for_signal_key(signal.idempotency_key)
        if duplicate:
            self.tracer.emit(
                duplicate.id, "signal.duplicate", "complete",
                {"signal": signal.model_dump(), "idempotency_key": signal.idempotency_key},
            )
            return duplicate

        # Correlate against active missions using the Correlator agent
        active = self.store.active_missions()
        corr_info = await self.operators.correlator.correlate(signal, active)
        corr_id = corr_info.get("correlated_mission_id") if isinstance(corr_info, dict) else None

        # Also do a quick entity-based check as safety net
        if not corr_id:
            corr_id = self._correlate_by_entity(signal, active)

        if corr_id:
            mission = self.store.get_mission(corr_id)
            if mission:
                mission.signals.append(signal)
                pl = signal.payload.get("pipeline")
                if isinstance(pl, dict):
                    mission.pipeline.update(pl)
                mission.graph.append(MissionGraphNode(
                    kind=GraphNodeKind.SIGNAL,
                    title=f"Correlated signal: {signal.type}",
                    status=StepStatus.COMPLETE,
                    summary=signal.summary,
                    ref_id=signal.id,
                    completed_at=utc_now(),
                    metadata={"source": signal.source, "entities": signal.entities},
                ))
                if mission.status != MissionStatus.WAITING:
                    mission.status = MissionStatus.RUNNING
                mission.summary = f"Correlated new {signal.type} signal into existing mission."
                self.store.update_mission(mission)
                self.store.save_signal(signal, mission.id)
                self.tracer.emit(mission.id, "signal.correlated", "complete", {"signal": signal.model_dump()})
                self.schedule(mission.id)
                return mission

        # New mission
        pl_seed = signal.payload.get("pipeline")
        pipe: dict[str, Any] = dict(pl_seed) if isinstance(pl_seed, dict) else {}
        mission = Mission(
            title=self._title_for(signal),
            status=MissionStatus.QUEUED,
            severity=corr_info.get("severity", signal.urgency) if isinstance(corr_info, dict) else signal.urgency,
            summary=corr_info.get("impact_summary", signal.summary) if isinstance(corr_info, dict) else signal.summary,
            signals=[signal],
            pipeline=pipe,
            graph=[MissionGraphNode(
                kind=GraphNodeKind.SIGNAL,
                title=f"Initial signal: {signal.type}",
                status=StepStatus.COMPLETE,
                summary=signal.summary,
                ref_id=signal.id,
                completed_at=utc_now(),
                metadata={"source": signal.source, "entities": signal.entities},
            )],
        )
        self.store.create_mission(mission)
        self.store.save_signal(signal, mission.id)
        self.tracer.emit(mission.id, "mission.created", "complete", {"signal": signal.model_dump()})
        self.schedule(mission.id)
        return mission

    # ── Scheduling ──────────────────────────────────────────────────────────

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

    def resume_pipeline_delivery(self, mission_id: str, continuation_secret: str) -> bool:
        """Continue a mission paused after analysis (defer_delivery). Returns False on auth/state mismatch."""
        mission = self.store.get_mission(mission_id)
        if not mission or mission.status != MissionStatus.WAITING:
            return False
        if not mission.pipeline.get("defer_delivery"):
            return False
        if not mission.pipeline.get("delivery_pending"):
            return False
        if mission.pipeline.get("continuation_secret") != continuation_secret:
            return False
        mission.status = MissionStatus.RUNNING
        self.store.update_mission(mission)
        self.tracer.emit(mission_id, "pipeline.resume", "started", {"mission_id": mission_id})
        self.schedule(mission_id)
        return True

    def cancel(self, mission_id: str, reason: str = "Canceled by operator request") -> bool:
        mission = self.store.get_mission(mission_id)
        if not mission:
            return False
        mission.status = MissionStatus.CANCELED
        mission.summary = reason
        mission.completed_at = utc_now()
        mission.graph.append(MissionGraphNode(
            kind=GraphNodeKind.VALIDATION,
            title="Mission canceled",
            status=StepStatus.CANCELED,
            summary=reason,
            completed_at=utc_now(),
        ))
        self.store.update_mission(mission)
        task = self._tasks.get(mission_id)
        if task and not task.done():
            task.cancel()
        self.tracer.emit(mission_id, "mission.canceled", "canceled", {"reason": reason})
        return True

    # ── Mission execution  (Orchestrator role) ──────────────────────────────

    async def run_mission(self, mission_id: str) -> None:
        lock = self._mission_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            timeout = max(30, int(os.getenv("AUTOPILOT_MISSION_TIMEOUT_SECONDS", "300")))
            try:
                await asyncio.wait_for(self._run_mission_locked(mission_id), timeout=timeout)
            except asyncio.TimeoutError:
                mission = self.store.get_mission(mission_id)
                if mission and mission.status not in {MissionStatus.COMPLETE, MissionStatus.CANCELED}:
                    mission.status = MissionStatus.FAILED
                    mission.summary = f"Mission timed out after {timeout}s; partial work was preserved."
                    mission.completed_at = utc_now()
                    mission.graph.append(MissionGraphNode(
                        kind=GraphNodeKind.VALIDATION,
                        title="Mission timed out",
                        status=StepStatus.FAILED,
                        summary=mission.summary,
                        completed_at=utc_now(),
                    ))
                    self.store.update_mission(mission)
                self.tracer.emit(mission_id, "mission.timeout", "failed", {"timeout_seconds": timeout})

    async def _run_mission_locked(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if not mission or mission.status == MissionStatus.CANCELED:
            return

        # Delivery-only continuation (async / webhook-gated pipeline)
        if (
            mission.pipeline.get("defer_delivery")
            and mission.pipeline.get("analysis_complete")
            and mission.pipeline.get("delivery_pending")
        ):
            if mission.status == MissionStatus.WAITING:
                return
            if mission.status == MissionStatus.RUNNING:
                try:
                    await self._complete_delivery_phases(mission_id)
                except asyncio.CancelledError:
                    self.tracer.emit(mission_id, "mission.canceled", "canceled", {"reason": "Runtime task canceled"})
                except Exception as exc:
                    log.exception("Mission %s delivery failed: %s", mission_id, exc)
                    m2 = self.store.get_mission(mission_id) or mission
                    m2.status = MissionStatus.FAILED
                    m2.summary = f"Delivery failed: {exc}"
                    self.store.update_mission(m2)
                    self.tracer.emit(mission_id, "mission.failed", "failed", {"error": str(exc), "phase": "delivery"})
                return

        try:
            mission.status = MissionStatus.RUNNING
            self.store.update_mission(mission)
            self.tracer.emit(mission.id, "runtime.dispatch", "started", {"mission": mission.title})

            # Correlation window — allow more signals to arrive
            await asyncio.sleep(self.correlation_window_seconds)
            mission = self.store.get_mission(mission_id) or mission

            # ── STEP 1: Memory recall ─────────────────────────────────────
            async with self.step(mission, "Memory Recall", "surface relevant prior patterns") as step:
                memory_ctx = await self.memory.recall_for(mission)
                mission.memory_notes = memory_ctx.get("relevant_patterns", [])
                step.output_summary = memory_ctx.get("notes", "No prior context.")
                self.store.update_mission(mission)

            # ── STEP 2: Correlator / Signal Evaluation ────────────────────
            async with self.step(mission, "Correlator", "classify severity, entities, investigation focus") as step:
                active = self.store.active_missions()
                mission = await self.operators.evaluate_signal(mission, active)
                step.output_summary = mission.summary
                self.store.update_mission(mission)

            # ── STEP 3: Planner ───────────────────────────────────────────
            async with self.step(mission, "Planner", "generate competing hypotheses with search focus") as step:
                mission = await self.operators.plan_mission(mission)
                step.output_summary = f"Generated {len(mission.hypotheses)} hypotheses."
                step.metadata = {"hypotheses": [h.model_dump() for h in mission.hypotheses]}
                self._append_hypothesis_nodes(mission)
                self.store.update_mission(mission)

            # ── STEP 4: Investigator (parallel subagents) ─────────────────
            async with self.step(mission, "Investigator Swarm", "parallel evidence-gathering across hypothesis branches") as step:
                mission = await self.operators.investigate(mission)
                step.output_summary = f"Gathered {len(mission.evidence)} evidence items across {len(mission.agent_runs)} branches."
                step.metadata = {
                    "evidence_count": len(mission.evidence),
                    "agent_runs": len(mission.agent_runs),
                    "connectors_searched": [c.manifest.name for c in self.registry.by_capability(Capability.SEARCH)],
                }
                self._append_branch_nodes(mission)
                self.store.update_mission(mission)

            # Re-correlate if more signals arrived during investigation
            mission = self.store.get_mission(mission_id) or mission
            if len(mission.signals) > 1 and mission.replans < 1:
                async with self.step(mission, "Planner (Replan)", "expand graph for correlated signals") as step:
                    mission = await self.operators.replan(
                        mission,
                        "New correlated signals arrived during investigation — expand scope.",
                    )
                    step.output_summary = f"Replan #{mission.replans}: added {len([h for h in mission.hypotheses if h.confidence == 0.3])} branches."
                    self._append_hypothesis_nodes(mission)
                    self.store.update_mission(mission)

                async with self.step(mission, "Investigator Swarm (Follow-up)", "investigate expanded branches") as step:
                    mission = await self.operators.investigate(mission)
                    step.output_summary = f"Follow-up: total evidence={len(mission.evidence)}."
                    self.store.update_mission(mission)

            # ── STEP 5: Verifier ──────────────────────────────────────────
            async with self.step(mission, "Verifier", "score evidence quality and confidence") as step:
                mission, needs_replan = await self.operators.verify(mission)
                step.output_summary = f"Confidence={mission.confidence:.2f} needs_replan={needs_replan}."
                step.metadata = {"confidence": mission.confidence, "needs_replan": needs_replan}
                self.store.update_mission(mission)

            if needs_replan:
                async with self.step(mission, "Planner (Adaptive Replan)", "spawn follow-up branch on low confidence") as step:
                    mission = await self.operators.replan(mission)
                    step.output_summary = f"Replan #{mission.replans}: added follow-up branch."
                    self._append_hypothesis_nodes(mission)
                    self.store.update_mission(mission)

                async with self.step(mission, "Investigator Swarm (Replan)", "investigate after replan") as step:
                    mission = await self.operators.investigate(mission)
                    mission, _ = await self.operators.verify(mission)
                    step.output_summary = f"Post-replan confidence={mission.confidence:.2f}."
                    step.metadata = {"confidence": mission.confidence, "evidence_count": len(mission.evidence)}
                    self.store.update_mission(mission)

            # ── STEP 6: Reflection (cross-branch synthesis) ───────────────
            async with self.step(mission, "Reflection", "synthesize evidence, gaps, readiness for delivery") as step:
                mission = await self.operators.reflect_mission(mission)
                ref = mission.pipeline.get("reflection") or {}
                step.output_summary = str(ref.get("reflection", ""))[:500]
                step.metadata = {"reflection": ref}
                self.store.update_mission(mission)

            mission = self.store.get_mission(mission_id) or mission
            if mission.pipeline.get("defer_delivery"):
                mission.pipeline.setdefault("continuation_secret", secrets.token_urlsafe(18))
                mission.pipeline["analysis_complete"] = True
                mission.pipeline["delivery_pending"] = True
                mission.pipeline["checkpoint"] = "await_operator_continue"
                mission.status = MissionStatus.WAITING
                mission.summary = f"{mission.summary} | Paused — POST /pipeline/continue to finalize delivery."
                self.store.update_mission(mission)
                self.tracer.emit(
                    mission.id,
                    "pipeline.awaiting_continue",
                    "waiting",
                    {"checkpoint": mission.pipeline["checkpoint"], "mission_id": mission.id},
                )
                return

            await self._complete_delivery_phases(mission_id)

        except asyncio.CancelledError:
            self.tracer.emit(mission_id, "mission.canceled", "canceled", {"reason": "Runtime task canceled"})
            return
        except Exception as exc:
            log.exception("Mission %s failed: %s", mission_id, exc)
            mission = self.store.get_mission(mission_id) or mission
            mission.status = MissionStatus.FAILED
            mission.summary = f"Runtime failed: {exc}"
            self.store.update_mission(mission)
            self.tracer.emit(mission_id, "mission.failed", "failed", {"error": str(exc)})

    async def _complete_delivery_phases(self, mission_id: str) -> None:
        """Executor, policy-gated actions, validation, mission completion."""
        mission = self.store.get_mission(mission_id)
        if not mission:
            return
        validation: dict = {}
        async with self.step(mission, "Executor", "synthesize brief and execute approved actions") as step:
            brief = await self.operators.synthesize(mission)
            step.metadata = {"brief_preview": brief[:400]}

        mission = self.store.get_mission(mission_id) or mission
        async with self.step(mission, "Action Publisher", "governor-gated side effects and notifications") as step:
            actions, validation = await self.operators.publish_actions(mission, brief)
            mission.actions.extend(actions)

            pending = sum(1 for a in mission.approvals if a.status.value == "pending")
            step.output_summary = (
                f"Executed {len(actions)} action(s); pending_approvals={pending}; "
                f"resolution={validation.get('resolution_status', 'unknown')}."
            )
            step.metadata = {
                "actions": [a.model_dump() for a in actions],
                "approvals": [ap.model_dump() for ap in mission.approvals],
                "policy_decisions": [d.model_dump() for d in mission.policy_decisions],
                "validation": validation,
            }
            self._append_action_nodes(mission)

        mission = self.store.get_mission(mission_id) or mission
        mission.status = MissionStatus.COMPLETE
        mission.completed_at = utc_now()
        mission.pipeline.pop("delivery_pending", None)
        mission.pipeline.pop("checkpoint", None)
        mission.pipeline.pop("continuation_secret", None)
        self.store.update_mission(mission)

        resolution = validation.get("resolution_status", "unknown") if validation else "unknown"
        self.memory.remember(mission, resolution)

        self.tracer.emit(
            mission.id, "mission.complete", "complete",
            {"confidence": mission.confidence, "actions": len(mission.actions), "resolution": resolution},
        )

    # ── Step context manager ─────────────────────────────────────────────────

    @asynccontextmanager
    async def step(self, mission: Mission, name: str, role: str) -> AsyncIterator[OperatorStep]:
        step = OperatorStep(mission_id=mission.id, name=name, role=role, input_summary=mission.summary)
        is_replan = "Replan" in name or "Replanner" in name
        graph_node = MissionGraphNode(
            kind=GraphNodeKind.REPLAN if is_replan else GraphNodeKind.OPERATOR,
            title=name,
            ref_id=step.id,
            summary=role,
        )
        mission.graph.append(graph_node)
        self.store.update_mission(mission)
        self.store.add_step(step)
        self.tracer.emit(
            mission.id,
            f"operator.{name.lower().replace(' ', '_')}.start",
            "started",
            {"role": role},
            step.id,
        )
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
            self.tracer.emit(
                mission.id,
                f"operator.{name.lower().replace(' ', '_')}.complete",
                "complete",
                step.model_dump(),
                step.id,
            )
        except Exception as exc:
            step.status = StepStatus.FAILED
            step.completed_at = utc_now()
            step.output_summary = str(exc)
            graph_node.status = StepStatus.FAILED
            graph_node.completed_at = step.completed_at
            graph_node.summary = str(exc)
            self.store.update_mission(mission)
            self.store.add_step(step)
            self.tracer.emit(
                mission.id,
                f"operator.{name.lower().replace(' ', '_')}.failed",
                "failed",
                {"error": str(exc)},
                step.id,
            )
            raise

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _correlate_by_entity(self, signal: Signal, active: list[Mission]) -> str | None:
        signal_entities = {e.lower() for e in signal.entities}
        if not signal_entities:
            return None
        for mission in active:
            mission_entities = {e.lower() for s in mission.signals for e in s.entities}
            if signal_entities & mission_entities:
                return mission.id
        return None

    def _title_for(self, signal: Signal) -> str:
        subject = signal.entities[0] if signal.entities else signal.type.replace("_", " ")
        return f"{subject}: {signal.summary[:80]}"

    def _signal_key(self, signal: Signal) -> str:
        payload = {
            "source": signal.source,
            "type": signal.type,
            "summary": signal.summary.strip().lower(),
            "entities": sorted(e.strip().lower() for e in signal.entities),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
        return f"{signal.source}:{signal.type}:{digest}"

    def _append_hypothesis_nodes(self, mission: Mission) -> None:
        existing = {n.ref_id for n in mission.graph if n.kind == GraphNodeKind.HYPOTHESIS}
        parents = [n.id for n in mission.graph if n.kind == GraphNodeKind.SIGNAL]
        for h in mission.hypotheses:
            if h.id in existing:
                continue
            mission.graph.append(MissionGraphNode(
                kind=GraphNodeKind.HYPOTHESIS,
                title=h.title,
                status=StepStatus.COMPLETE,
                parent_ids=parents,
                branch_id=h.id,
                ref_id=h.id,
                summary=h.rationale,
                completed_at=utc_now(),
                metadata={"confidence": h.confidence},
            ))

    def _append_branch_nodes(self, mission: Mission) -> None:
        existing = {n.ref_id for n in mission.graph if n.kind == GraphNodeKind.BRANCH}
        for h in mission.hypotheses:
            ref = f"branch:{h.id}:{len(h.evidence_ids)}"
            if ref in existing:
                continue
            mission.graph.append(MissionGraphNode(
                kind=GraphNodeKind.BRANCH,
                title=f"Evidence branch: {h.title}",
                status=StepStatus.COMPLETE,
                parent_ids=[n.id for n in mission.graph if n.ref_id == h.id],
                branch_id=h.id,
                ref_id=ref,
                summary=f"Collected {len(h.evidence_ids)} evidence item(s).",
                completed_at=utc_now(),
                metadata={"evidence_ids": h.evidence_ids, "final_confidence": h.confidence},
            ))

    def _append_action_nodes(self, mission: Mission) -> None:
        existing = {n.ref_id for n in mission.graph if n.kind in {GraphNodeKind.ACTION, GraphNodeKind.POLICY}}
        for d in mission.policy_decisions:
            if d.id not in existing:
                mission.graph.append(MissionGraphNode(
                    kind=GraphNodeKind.POLICY,
                    title=f"Policy: {d.connector}.{d.action}",
                    status=StepStatus.COMPLETE,
                    ref_id=d.id,
                    summary=d.reason,
                    completed_at=utc_now(),
                    metadata=d.model_dump(),
                ))
        for a in mission.actions:
            if a.id not in existing:
                status = (
                    StepStatus.COMPLETE if a.status in {"complete", "skipped"}
                    else StepStatus.STARTED if a.status == "pending_approval"
                    else StepStatus.FAILED
                )
                mission.graph.append(MissionGraphNode(
                    kind=GraphNodeKind.ACTION,
                    title=f"Action: {a.connector}.{a.action}",
                    status=status,
                    ref_id=a.id,
                    summary=a.summary,
                    completed_at=utc_now(),
                    metadata=a.model_dump(),
                ))
