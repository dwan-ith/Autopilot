"""Sub-agent architecture for AUTOPILOT.

Hierarchy
---------
PersistentAgent   — lives for the lifetime of the RuntimeKernel.
                    Shares state across missions (memory, policy state, etc.)

SubAgent          — spawned per-mission, per-task.
                    Runs a bounded reason → tool_call → observe loop.

Orchestrator      — runs multiple SubAgents in parallel with concurrency control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from autopilot.operators.llm import parse_json, reason

log = logging.getLogger("autopilot.agents")

# Lazy reference to the Omium TraceSink — set by the RuntimeKernel on startup.
# Kept as module-level to avoid circular imports between agents and tracing.
_omium_sink: Any = None


def set_omium_sink(sink: Any) -> None:
    """Register the Omium TraceSink for agent-step traces. Called from RuntimeKernel."""
    global _omium_sink
    _omium_sink = sink


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Result of a single tool invocation."""
    tool_name: str
    input: dict[str, Any]
    output: Any
    success: bool
    duration_ms: float
    error: str | None = None


@dataclass
class AgentStep:
    """One step in an agent's reasoning loop."""
    step_number: int
    thought: str
    raw_llm_response: str | None = None  # full LLM output text for reasoning trace UI
    tool_call: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: ToolResult | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Final output from a sub-agent run."""
    agent_id: str
    agent_role: str
    task: str
    answer: dict[str, Any]
    steps: list[AgentStep]
    total_duration_ms: float
    tool_calls_made: int
    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class Tool:
    """A capability exposed to a sub-agent."""

    def __init__(self, name: str, description: str, parameters: dict[str, str], fn):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn  # async callable

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    async def execute(self, **kwargs) -> ToolResult:
        start = time.time()
        try:
            result = await self.fn(**kwargs)
            return ToolResult(
                tool_name=self.name, input=kwargs, output=result,
                success=True, duration_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name, input=kwargs, output=None,
                success=False, duration_ms=(time.time() - start) * 1000,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Shared system prompt template
# ---------------------------------------------------------------------------

_AGENT_SYSTEM_PROMPT = """\
You are a specialized sub-agent in AUTOPILOT, an autonomous operator runtime.

Role: {role}
Task: {task}

Role-specific instructions:
{role_instructions}

Available tools:
{tool_descriptions}

{tool_rule}

Respond with EXACTLY one JSON object:

To call a tool (include your reasoning in 'thought'):
{{"action": "tool", "thought": "I should search X because Y", "tool": "tool_name", "input": {{"param": "value"}}}}

To return your final answer:
{{"action": "answer", "thought": "Based on the evidence I found...", "result": {{...your structured result...}}}}

ALWAYS include the 'thought' field explaining your reasoning before the action.
Do NOT include any text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# SubAgent  (mission-scoped, bounded reasoning loop)
# ---------------------------------------------------------------------------

class SubAgent:
    """Bounded JSON-speaking operator with real tool-use loop.

    Each SubAgent has a *role* which pins it to a specific LLM key slot
    (see operators/llm.py), so parallel agents never share a rate-limit bucket.
    """

    def __init__(
        self,
        role: str,
        tools: list[Tool],
        max_steps: int = 8,
        temperature: float = 0.3,
        system_prompt: str = "",
    ):
        self.id = f"agent_{uuid4().hex[:8]}"
        self.role = role
        self.tools = {tool.name: tool for tool in tools}
        env_steps = os.getenv("AUTOPILOT_SUBAGENT_MAX_STEPS", "").strip()
        self.max_steps = max(1, int(env_steps)) if env_steps else max_steps
        self.temperature = temperature
        self.system_prompt = system_prompt.strip()

    async def run(self, task: str) -> AgentResult:
        """Execute the reason → tool_call → observe loop."""
        start = time.time()
        steps: list[AgentStep] = []
        tool_calls = 0

        tool_descriptions = "\n".join(
            f"  - {t.name}: {t.description} (params: {json.dumps(t.parameters)})"
            for t in self.tools.values()
        ) or "  - none (analysis-only role)"

        tool_rule = (
            "Use read/search tools to gather real data. Do not fabricate tool results."
            if self.tools
            else "You have no tools. Analyze the provided context and be explicit about uncertainty."
        )

        system = _AGENT_SYSTEM_PROMPT.format(
            role=self.role,
            task=task,
            role_instructions=self.system_prompt or "Follow the task contract exactly.",
            tool_descriptions=tool_descriptions,
            tool_rule=tool_rule,
        )

        ctx = f"Task: {task}"

        for step_num in range(1, self.max_steps + 1):
            raw = await reason(
                system, ctx,
                role=self.role,
                json_mode=True,
                temperature=self.temperature,
            )

            if raw is None:
                fallback_steps, fallback_answer, fallback_calls = await self._heuristic_fallback(task, steps)
                return AgentResult(
                    agent_id=self.id, agent_role=self.role, task=task,
                    answer=fallback_answer, steps=fallback_steps,
                    total_duration_ms=(time.time() - start) * 1000,
                    tool_calls_made=tool_calls + fallback_calls,
                    success=True,
                )

            parsed = parse_json(raw)
            if not isinstance(parsed, dict):
                steps.append(AgentStep(
                    step_number=step_num,
                    thought=f"[Invalid JSON on step {step_num}] {(raw or '')[:400]}",
                    raw_llm_response=raw,
                ))
                ctx += "\n\nYour last response was not valid JSON. Respond with EXACTLY one JSON object."
                continue

            action = parsed.get("action", "")

            if action == "answer":
                result = parsed.get("result", parsed)
                steps.append(AgentStep(
                    step_number=step_num,
                    thought=f"Final answer: {json.dumps(result)[:300]}",
                    raw_llm_response=raw,
                ))
                return AgentResult(
                    agent_id=self.id, agent_role=self.role, task=task,
                    answer=result if isinstance(result, dict) else {"result": result},
                    steps=steps,
                    total_duration_ms=(time.time() - start) * 1000,
                    tool_calls_made=tool_calls,
                    success=True,
                )

            if action == "tool":
                tool_name = parsed.get("tool", "")
                tool_input = parsed.get("input", {})

                if tool_name not in self.tools:
                    steps.append(AgentStep(
                        step_number=step_num,
                        thought=f"Attempted unknown tool '{tool_name}'. Available: {list(self.tools.keys())}",
                        raw_llm_response=raw,
                    ))
                    ctx += f"\n\nTool '{tool_name}' does not exist. Available: {list(self.tools.keys())}"
                    continue

                tool = self.tools[tool_name]
                tool_result = await tool.execute(**tool_input)
                tool_calls += 1

                # Emit structured Omium trace for every tool invocation
                if _omium_sink is not None:
                    try:
                        _omium_sink.emit_agent_step(
                            mission_id=getattr(self, "_mission_id", None),
                            agent_id=self.id,
                            agent_role=self.role,
                            step_number=step_num,
                            tool_name=tool_name,
                            status="complete" if tool_result.success else "failed",
                            duration_ms=tool_result.duration_ms,
                            result_summary=str(tool_result.output)[:200] if tool_result.success else tool_result.error,
                        )
                    except Exception:
                        pass  # Tracing must never crash the agent

                # Extract any reasoning text the LLM provided before the tool call
                # LLMs often include a 'thought' or 'reasoning' key alongside 'action'
                llm_reasoning = (
                    parsed.get("thought")
                    or parsed.get("reasoning")
                    or parsed.get("rationale")
                    or f"Calling {tool_name} with {json.dumps(tool_input)[:200]}"
                )

                step = AgentStep(
                    step_number=step_num,
                    thought=str(llm_reasoning)[:600],
                    raw_llm_response=raw,
                    tool_call=tool_name,
                    tool_input=tool_input,
                    tool_result=tool_result,
                )
                steps.append(step)

                log.info(
                    "Agent %s [%s] step %d: %s → %s",
                    self.id, self.role, step_num, tool_name,
                    "ok" if tool_result.success else f"err: {tool_result.error}",
                )

                result_str = (
                    json.dumps(tool_result.output, default=str)[:2000]
                    if tool_result.success
                    else f"ERROR: {tool_result.error}"
                )
                ctx += f"\n\nTool '{tool_name}' returned:\n{result_str}\n\nReason about this and decide next step."
                continue

            steps.append(AgentStep(
                step_number=step_num,
                thought=f"Unknown action '{action}' returned. Must be 'tool' or 'answer'.",
                raw_llm_response=raw,
            ))
            ctx += "\n\nRespond with action 'tool' or 'answer' only."

        return AgentResult(
            agent_id=self.id, agent_role=self.role, task=task,
            answer={"incomplete": True, "steps_exhausted": True},
            steps=steps,
            total_duration_ms=(time.time() - start) * 1000,
            tool_calls_made=tool_calls,
            success=False,
            error="Max reasoning steps reached without final answer.",
        )

    async def _heuristic_fallback(
        self, task: str, steps: list[AgentStep],
    ) -> tuple[list[AgentStep], dict[str, Any], int]:
        search_tools = [
            t for name, t in self.tools.items()
            if "search" in name and not any(b in name for b in ["create", "post", "notify", "send"])
        ]
        if not search_tools:
            return steps, {"fallback": True, "message": "No LLM provider; no read tools available."}, 0

        gathered: list[Any] = []
        calls = 0
        for tool in search_tools[:3]:
            query = self._query_from_task(task)
            tr = await tool.execute(query=query)
            calls += 1
            steps.append(AgentStep(
                step_number=len(steps) + 1,
                thought=f"Heuristic fallback called {tool.name}",
                tool_call=tool.name,
                tool_input={"query": query},
                tool_result=tr,
            ))
            if tr.success and isinstance(tr.output, list):
                gathered.extend(tr.output)

        return steps, {
            "fallback": True,
            "evidence_gathered": [],
            "assessment": f"Heuristic mode: searched {calls} tool(s) — no LLM provider active.",
            "confidence": 0.55 if gathered else 0.3,
        }, calls

    def _query_from_task(self, task: str) -> str:
        lines = [line.strip() for line in task.splitlines() if line.strip()]
        key_lines = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.lower().startswith(("hypothesis:", "key entities:", "mission context:")) and ":" in line
        ]
        return " ".join(key_lines)[:240] or task[:240]


# ---------------------------------------------------------------------------
# PersistentAgent  (lives in RuntimeKernel, stateful across missions)
# ---------------------------------------------------------------------------

class PersistentAgent:
    """Base class for agents that are instantiated once and called many times.

    Persistent agents maintain no per-call state — all context is passed in.
    They use SubAgent.run() internally but always return typed results.
    """

    role: str = "persistent"
    default_max_steps: int = 4

    def _make_subagent(self, tools: list[Tool] | None = None) -> SubAgent:
        return SubAgent(
            role=self.role,
            tools=tools or [],
            max_steps=self.default_max_steps,
            temperature=0.3,
            system_prompt=self._system_prompt(),
        )

    def _system_prompt(self) -> str:
        raise NotImplementedError

    async def _run(self, task: str, tools: list[Tool] | None = None) -> AgentResult:
        return await self._make_subagent(tools).run(task)


# ---------------------------------------------------------------------------
# Orchestrator  (parallel sub-agent coordinator)
# ---------------------------------------------------------------------------

class Orchestrator:
    """Runs bounded sub-agent tasks in parallel."""

    def __init__(self, tools: list[Tool], max_parallel: int = 6, system_prompt: str = ""):
        self.tools = tools
        self.max_parallel = max_parallel
        self.system_prompt = system_prompt

    async def run_agents(self, tasks: list[tuple[str, str]]) -> list[AgentResult]:
        """Run multiple (role, task) pairs as parallel sub-agents.

        Each role gets its own LLM key slot, so up to 6 agents run concurrently
        without rate-limit interference.
        """
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_one(role: str, task: str) -> AgentResult:
            async with semaphore:
                agent = SubAgent(role=role, tools=self.tools, system_prompt=self.system_prompt)
                log.info("Orchestrator: spawning [%s] for: %.80s", role, task)
                return await agent.run(task)

        results = await asyncio.gather(
            *(run_one(role, task) for role, task in tasks),
            return_exceptions=True,
        )

        final: list[AgentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                role, task = tasks[i]
                final.append(AgentResult(
                    agent_id=f"agent_failed_{i}", agent_role=role, task=task,
                    answer={}, steps=[], total_duration_ms=0, tool_calls_made=0,
                    success=False, error=str(result),
                ))
            else:
                final.append(result)
        return final
