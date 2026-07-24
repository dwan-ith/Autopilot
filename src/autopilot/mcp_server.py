from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.streamable_http import TransportSecuritySettings
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel
from starlette.responses import PlainTextResponse

from autopilot.agents.base import Tool
from autopilot.connectors import default_registry
from autopilot.connectors.base import ConnectorRegistry
from autopilot.kernel import RuntimeKernel
from autopilot.models import ApprovalStatus, Mission, Signal
from autopilot.storage import Store

logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
log = logging.getLogger("autopilot.mcp_server")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _input_schema(tool: Tool) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, description in tool.parameters.items():
        text = str(description)
        properties[name] = {"type": "string", "description": text}
        if "optional" not in text.lower():
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass
class ExposedTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True


class AutopilotMCPServer:
    """Official-SDK MCP facade over connector reads and mission control."""

    def __init__(
        self,
        registry: ConnectorRegistry | None = None,
        store: Store | None = None,
        runtime: RuntimeKernel | None = None,
    ) -> None:
        # The stdio child gets a native-only registry. The in-process HTTP server
        # may receive the live registry, including negotiated downstream MCPs.
        self.registry = registry or default_registry(include_mcp=False)
        self.store = store
        self.runtime = runtime
        self.server = Server(
            "autopilot",
            version="0.3.0",
            instructions=(
                "Inspect connected operational context and submit signals to AUTOPILOT. "
                "External side effects remain policy-gated in AUTOPILOT's approval queue."
            ),
        )
        self.tools: dict[str, ExposedTool] = {}
        self._register_connector_tools()
        if self.store is not None and self.runtime is not None:
            self._register_control_tools()
        self._install_protocol_handlers()

    def _register_connector_tools(self) -> None:
        for connector in self.registry._connectors.values():
            # Avoid recursively exposing AUTOPILOT's own stdio proof server.
            if connector.manifest.name == "mcp_autopilot_native":
                continue
            if not hasattr(connector, "as_tools"):
                continue
            for tool in connector.as_tools():
                if tool.name in self.tools:
                    continue

                async def execute(arguments: dict[str, Any], item: Tool = tool) -> Any:
                    result = await item.execute(**arguments)
                    if not result.success:
                        raise RuntimeError(result.error or "Tool execution failed")
                    return result.output

                self.tools[tool.name] = ExposedTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=_input_schema(tool),
                    handler=execute,
                )

    def _register_control_tools(self) -> None:
        assert self.store is not None
        assert self.runtime is not None

        async def status(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "missions": self.store.count_missions(),
                "traces": self.store.count_traces(),
                "pending_approvals": len(
                    self.store.list_action_approvals(ApprovalStatus.PENDING)
                ),
                "connector_count": len(self.registry._connectors),
            }

        async def list_missions(arguments: dict[str, Any]) -> list[dict[str, Any]]:
            limit = max(1, min(int(arguments.get("limit", 20)), 100))
            return self.store.list_missions(limit=limit)

        async def get_mission(arguments: dict[str, Any]) -> Mission:
            mission = self.store.get_mission(str(arguments["mission_id"]))
            if mission is None:
                raise ValueError("Mission not found")
            return mission

        async def list_approvals(_: dict[str, Any]) -> list[dict[str, Any]]:
            return self.store.list_action_approvals(ApprovalStatus.PENDING)

        async def submit_signal(arguments: dict[str, Any]) -> Mission:
            raw_entities = arguments.get("entities", [])
            if isinstance(raw_entities, str):
                entities = [item.strip() for item in raw_entities.split(",") if item.strip()]
            elif isinstance(raw_entities, list):
                entities = [str(item) for item in raw_entities]
            else:
                raise ValueError("entities must be an array or comma-separated string")
            signal = Signal(
                source=str(arguments.get("source") or "mcp"),
                type=str(arguments.get("type") or "operational_signal"),
                summary=str(arguments["summary"]),
                entities=entities,
                urgency=str(arguments.get("urgency") or "medium"),
                payload=arguments.get("payload") if isinstance(arguments.get("payload"), dict) else {},
                idempotency_key=str(arguments["idempotency_key"]) if arguments.get("idempotency_key") else None,
            )
            return await self.runtime.ingest(signal)

        async def wait_for_mission(arguments: dict[str, Any]) -> Mission:
            mission_id = str(arguments["mission_id"])
            timeout_seconds = max(
                1.0,
                min(float(arguments.get("timeout_seconds", 20)), 25.0),
            )
            try:
                mission = await asyncio.wait_for(
                    self.runtime.wait_for(mission_id),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                mission = self.store.get_mission(mission_id)
            if mission is None:
                raise ValueError("Mission not found")
            return mission

        self._add_control_tool(
            "autopilot_status",
            "Read AUTOPILOT runtime counts and approval state.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            status,
        )
        self._add_control_tool(
            "autopilot_list_missions",
            "List recent AUTOPILOT missions.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
                "additionalProperties": False,
            },
            list_missions,
        )
        self._add_control_tool(
            "autopilot_get_mission",
            "Read one mission, including evidence, graph, actions, and approvals.",
            {
                "type": "object",
                "properties": {"mission_id": {"type": "string"}},
                "required": ["mission_id"],
                "additionalProperties": False,
            },
            get_mission,
        )
        self._add_control_tool(
            "autopilot_list_approvals",
            "List pending policy-gated actions. Execution remains in the AUTOPILOT dashboard.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            list_approvals,
        )
        self._add_control_tool(
            "autopilot_submit_signal",
            "Submit a real operational signal and start or correlate an AUTOPILOT mission.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1},
                    "source": {"type": "string", "default": "mcp"},
                    "type": {"type": "string", "default": "operational_signal"},
                    "entities": {"type": "array", "items": {"type": "string"}, "default": []},
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "default": "medium",
                    },
                    "payload": {"type": "object", "default": {}},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
            submit_signal,
            read_only=False,
            idempotent=False,
        )
        self._add_control_tool(
            "autopilot_wait_for_mission",
            "Wait briefly for a mission, then return its current state for safe polling.",
            {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string"},
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 25,
                        "default": 20,
                    },
                },
                "required": ["mission_id"],
                "additionalProperties": False,
            },
            wait_for_mission,
        )

    def _add_control_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
        *,
        read_only: bool = True,
        destructive: bool = False,
        idempotent: bool = True,
    ) -> None:
        self.tools[name] = ExposedTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
        )

    def _install_protocol_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[types.Tool]:
            # Downstream MCP tools are discovered during application startup,
            # after this facade is constructed.
            self._register_connector_tools()
            return [
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=tool.read_only,
                        destructiveHint=tool.destructive,
                        idempotentHint=tool.idempotent,
                        openWorldHint=True,
                    ),
                )
                for tool in sorted(self.tools.values(), key=lambda item: item.name)
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            tool = self.tools.get(name)
            if tool is None:
                return types.CallToolResult(
                    isError=True,
                    content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                )
            try:
                if self.store is not None:
                    self.store.trace(
                        None,
                        "mcp.external.tool.started",
                        "started",
                        {
                            "tool": name,
                            "read_only": tool.read_only,
                            "argument_keys": sorted((arguments or {}).keys()),
                        },
                    )
                value = _json_value(await tool.handler(arguments or {}))
            except Exception as exc:
                if self.store is not None:
                    self.store.trace(
                        None,
                        "mcp.external.tool.failed",
                        "failed",
                        {"tool": name, "error": str(exc)[:500]},
                    )
                return types.CallToolResult(
                    isError=True,
                    content=[types.TextContent(type="text", text=str(exc))],
                )
            if self.store is not None:
                self.store.trace(
                    None,
                    "mcp.external.tool.complete",
                    "complete",
                    {"tool": name, "read_only": tool.read_only},
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=True, default=str))],
                structuredContent={"result": value},
                isError=False,
            )

    def initialization_options(self) -> InitializationOptions:
        return self.server.create_initialization_options(
            notification_options=NotificationOptions(tools_changed=False),
            experimental_capabilities={},
        )

    async def serve(self) -> None:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.initialization_options(),
            )


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class AutopilotMCPHTTP:
    """Authenticated Streamable HTTP transport for remote MCP clients."""

    def __init__(self, facade: AutopilotMCPServer) -> None:
        self.facade = facade
        self.manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_csv_env(
                "AUTOPILOT_MCP_ALLOWED_HOSTS",
                "localhost:*,127.0.0.1:*,testserver",
            ),
            allowed_origins=_csv_env(
                "AUTOPILOT_MCP_ALLOWED_ORIGINS",
                "http://localhost:*,http://127.0.0.1:*",
            ),
        )
        manager = StreamableHTTPSessionManager(
            self.facade.server,
            json_response=True,
            stateless=True,
            security_settings=security,
        )
        self.manager = manager
        try:
            async with manager.run():
                yield
        finally:
            self.manager = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await PlainTextResponse("HTTP transport required", status_code=400)(scope, receive, send)
            return
        expected = (
            os.getenv("AUTOPILOT_MCP_API_KEY", "").strip()
            or os.getenv("AUTOPILOT_API_KEY", "").strip()
        )
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        supplied = headers.get("x-autopilot-key", "") or headers.get("x-api-key", "")
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if expected and (not supplied or not secrets.compare_digest(supplied, expected)):
            await PlainTextResponse("MCP API key required", status_code=401)(scope, receive, send)
            return
        host = headers.get("host", "").split(":", 1)[0].lower()
        if not expected and host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            await PlainTextResponse(
                "Set AUTOPILOT_MCP_API_KEY before exposing MCP remotely",
                status_code=503,
            )(scope, receive, send)
            return
        if self.manager is None:
            await PlainTextResponse("MCP transport is starting", status_code=503)(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)


async def serve() -> None:
    registry = default_registry(include_mcp=False)
    store = Store()
    runtime = RuntimeKernel(store, registry)
    await AutopilotMCPServer(registry=registry, store=store, runtime=runtime).serve()


def run() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
