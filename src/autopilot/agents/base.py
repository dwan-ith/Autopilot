"""Sub-agent architecture for AUTOPILOT.

A SubAgent is an autonomous reasoning unit that:
  1. Receives a task from the orchestrator
  2. Has access to a set of tools (connectors)
  3. Runs a reason → tool → observe loop until it has an answer
  4. Reports structured results back to the orchestrator

This is not a wrapper around a single LLM call. Each agent runs
its own multi-step reasoning loop with real tool invocations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from autopilot.operators.llm import parse_json, reason

log = logging.getLogger("autopilot.agents")


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
    """One step in the agent's reasoning loop."""
    step_number: int
    thought: str
    tool_call: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: ToolResult | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Final output from a sub-agent."""
    agent_id: str
    agent_role: str
    task: str
    answer: dict[str, Any]
    steps: list[AgentStep]
    total_duration_ms: float
    tool_calls_made: int
    success: bool
    error: str | None = None


class Tool:
    """A capability exposed to a sub-agent."""

    def __init__(self, name: str, description: str, parameters: dict[str, str], fn):
        self.name = name
        self.description = description
        self.parameters = parameters  # {param_name: description}
        self.fn = fn  # async callable

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, **kwargs) -> ToolResult:
        start = time.time()
        try:
            result = await self.fn(**kwargs)
            return ToolResult(
                tool_name=self.name,
                input=kwargs,
                output=result,
                success=True,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                input=kwargs,
                output=None,
                success=False,
                duration_ms=(time.time() - start) * 1000,
                error=str(exc),
            )


AGENT_SYSTEM_PROMPT = """\
You are a specialized sub-agent in AUTOPILOT, an autonomous operator runtime.

Your role: {role}
Your task: {task}

You have access to these tools:
{tool_descriptions}

IMPORTANT RULES:
- You MUST use tools to gather real data. Do NOT make up information.
- After each tool call, reason about the result before deciding next steps.
- When you have enough information, return your final answer.

Respond with EXACTLY one JSON object in one of these formats:

To call a tool:
{{"action": "tool", "tool": "tool_name", "input": {{"param": "value"}}}}

To return your final answer:
{{"action": "answer", "result": {{...your structured result...}}}}

Do NOT include any text outside the JSON object.
"""


class SubAgent:
    """An autonomous reasoning agent with tool-use capabilities."""

    def __init__(
        self,
        role: str,
        tools: list[Tool],
        max_steps: int = 8,
        temperature: float = 0.3,
    ):
        self.id = f"agent_{uuid4().hex[:8]}"
        self.role = role
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.temperature = temperature

    async def run(self, task: str) -> AgentResult:
        """Execute the agent's reasoning loop."""
        start = time.time()
        steps: list[AgentStep] = []
        tool_calls = 0

        tool_descriptions = "\n".join(
            f"  - {tool.name}: {tool.description} (params: {json.dumps(tool.parameters)})"
            for tool in self.tools.values()
        )

        system = AGENT_SYSTEM_PROMPT.format(
            role=self.role,
            task=task,
            tool_descriptions=tool_descriptions,
        )

        conversation_context = f"Task: {task}"

        for step_num in range(1, self.max_steps + 1):
            raw = await reason(
                system,
                conversation_context,
                json_mode=True,
                temperature=self.temperature,
            )

            if raw is None:
                # No LLM available — return heuristic result
                return AgentResult(
                    agent_id=self.id,
                    agent_role=self.role,
                    task=task,
                    answer={"fallback": True, "message": "No LLM provider; using heuristic mode."},
                    steps=steps,
                    total_duration_ms=(time.time() - start) * 1000,
                    tool_calls_made=tool_calls,
                    success=True,
                )

            parsed = parse_json(raw)
            if not isinstance(parsed, dict):
                log.warning("Agent %s step %d: unparseable response", self.id, step_num)
                steps.append(AgentStep(step_number=step_num, thought=raw or ""))
                conversation_context += f"\n\nYour last response was not valid JSON. Respond with EXACTLY one JSON object."
                continue

            action = parsed.get("action", "")

            if action == "answer":
                result = parsed.get("result", parsed)
                steps.append(AgentStep(step_number=step_num, thought=f"Final answer: {json.dumps(result)[:200]}"))
                return AgentResult(
                    agent_id=self.id,
                    agent_role=self.role,
                    task=task,
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
                    step = AgentStep(
                        step_number=step_num,
                        thought=f"Attempted unknown tool: {tool_name}",
                        tool_call=tool_name,
                        tool_input=tool_input,
                    )
                    steps.append(step)
                    conversation_context += f"\n\nTool '{tool_name}' does not exist. Available tools: {list(self.tools.keys())}"
                    continue

                tool = self.tools[tool_name]
                tool_result = await tool.execute(**tool_input)
                tool_calls += 1

                step = AgentStep(
                    step_number=step_num,
                    thought=f"Called {tool_name}",
                    tool_call=tool_name,
                    tool_input=tool_input,
                    tool_result=tool_result,
                )
                steps.append(step)

                log.info(
                    "Agent %s [%s] step %d: %s → %s",
                    self.id, self.role, step_num, tool_name,
                    "success" if tool_result.success else f"error: {tool_result.error}",
                )

                # Feed tool result back into the conversation
                result_str = json.dumps(tool_result.output, default=str)[:2000] if tool_result.success else f"ERROR: {tool_result.error}"
                conversation_context += f"\n\nTool '{tool_name}' returned:\n{result_str}\n\nReason about this result and decide your next step."
                continue

            # Unknown action
            steps.append(AgentStep(step_number=step_num, thought=f"Unknown action: {action}"))
            conversation_context += "\n\nRespond with action 'tool' or 'answer' only."

        # Max steps reached
        return AgentResult(
            agent_id=self.id,
            agent_role=self.role,
            task=task,
            answer={"incomplete": True, "steps_exhausted": True},
            steps=steps,
            total_duration_ms=(time.time() - start) * 1000,
            tool_calls_made=tool_calls,
            success=False,
            error="Max reasoning steps reached without final answer.",
        )


class Orchestrator:
    """Decomposes a mission into sub-agent tasks and runs them in parallel."""

    def __init__(self, tools: list[Tool], max_parallel: int = 5):
        self.tools = tools
        self.max_parallel = max_parallel

    async def run_agents(self, tasks: list[tuple[str, str]]) -> list[AgentResult]:
        """Run multiple (role, task) pairs as parallel sub-agents.

        Args:
            tasks: List of (role, task_description) tuples.

        Returns:
            List of AgentResults in the same order.
        """
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_one(role: str, task: str) -> AgentResult:
            async with semaphore:
                agent = SubAgent(role=role, tools=self.tools)
                log.info("Orchestrator: spawning sub-agent [%s] for: %.80s", role, task)
                return await agent.run(task)

        results = await asyncio.gather(
            *(run_one(role, task) for role, task in tasks),
            return_exceptions=True,
        )

        # Convert exceptions to failed AgentResults
        final: list[AgentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                role, task = tasks[i]
                final.append(AgentResult(
                    agent_id=f"agent_failed_{i}",
                    agent_role=role,
                    task=task,
                    answer={},
                    steps=[],
                    total_duration_ms=0,
                    tool_calls_made=0,
                    success=False,
                    error=str(result),
                ))
            else:
                final.append(result)
        return final
