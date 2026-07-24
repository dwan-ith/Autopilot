from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from autopilot.connectors.base import Connector
from autopilot.models import (
    ActionResult,
    ActionRisk,
    AuthMode,
    Capability,
    ConnectorManifest,
    ConnectorToolSpec,
    Evidence,
)

log = logging.getLogger("autopilot.mcp")

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CONFIG_DIAGNOSTICS: list[dict[str, str]] = []


class MCPError(RuntimeError):
    """Base error for MCP configuration, transport, and tool failures."""


class MCPToolError(MCPError):
    """Raised when an MCP server marks a tool result as an error."""


@dataclass(frozen=True)
class MCPServerConfig:
    id: str
    display_name: str
    enabled: bool
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    cwd: str | None = None
    timeout_seconds: float = 20.0
    allowed_tools: tuple[str, ...] = ()
    allowed_write_tools: tuple[str, ...] = ()
    description: str = ""


@dataclass
class _Request:
    operation: str
    future: asyncio.Future[Any]
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "server"


def _expand(value: str, missing: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "PYTHON":
            return sys.executable
        if name == "AUTOPILOT_ROOT":
            return str(Path(__file__).resolve().parents[3])
        if name == "AUTOPILOT_SRC":
            return str(Path(__file__).resolve().parents[2])
        resolved = os.getenv(name)
        if resolved is None:
            missing.add(name)
            return ""
        return resolved

    return _ENV_REF.sub(replace, value)


def _parse_server_config(name: str, raw: Any) -> MCPServerConfig:
    if not isinstance(raw, dict):
        raise MCPError("server configuration must be an object")

    missing: set[str] = set()
    transport = str(raw.get("transport") or ("streamable_http" if raw.get("url") else "stdio")).lower()
    if transport in {"http", "streamable-http"}:
        transport = "streamable_http"
    if transport not in {"stdio", "streamable_http"}:
        raise MCPError("transport must be 'stdio' or 'streamable_http'")

    command = raw.get("command")
    url = raw.get("url")
    if transport == "stdio" and not isinstance(command, str):
        raise MCPError("stdio transport requires a string 'command'")
    if transport == "streamable_http" and not isinstance(url, str):
        raise MCPError("streamable_http transport requires a string 'url'")

    args = raw.get("args", [])
    env = raw.get("env", {})
    headers = raw.get("headers", {})
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise MCPError("'args' must be a list of strings")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise MCPError("'env' must be an object containing string values")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise MCPError("'headers' must be an object containing string values")

    resolved_command = _expand(command, missing) if isinstance(command, str) else None
    resolved_url = _expand(url, missing) if isinstance(url, str) else None
    resolved_args = tuple(_expand(arg, missing) for arg in args)
    resolved_env = {key: _expand(value, missing) for key, value in env.items()}
    resolved_headers = {key: _expand(value, missing) for key, value in headers.items()}
    resolved_cwd = _expand(str(raw["cwd"]), missing) if raw.get("cwd") else None
    enabled = bool(raw.get("enabled", False))
    if enabled and missing:
        raise MCPError(f"missing environment variables: {', '.join(sorted(missing))}")

    return MCPServerConfig(
        id=f"mcp_{_slug(name)}",
        display_name=str(raw.get("displayName") or name),
        enabled=enabled,
        transport=transport,
        command=resolved_command,
        args=resolved_args,
        url=resolved_url,
        env=resolved_env,
        headers=resolved_headers,
        cwd=resolved_cwd,
        timeout_seconds=max(1.0, min(float(raw.get("timeoutSeconds", 20)), 120.0)),
        allowed_tools=tuple(str(item) for item in raw.get("allowedTools", [])),
        allowed_write_tools=tuple(str(item) for item in raw.get("allowedWriteTools", [])),
        description=str(raw.get("description") or ""),
    )


def load_mcp_server_configs(path: Path | str | None = None) -> list[MCPServerConfig]:
    """Load validated MCP server settings without executing any command."""
    config_path = Path(path or os.getenv("AUTOPILOT_MCP_CONFIG", "") or Path(__file__).resolve().parents[3] / "data" / "mcp_servers.json")
    _CONFIG_DIAGNOSTICS.clear()
    if not config_path.exists():
        return []

    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _CONFIG_DIAGNOSTICS.append({"server": "*", "error": f"Cannot read {config_path}: {exc}"})
        return []

    servers = document.get("mcpServers", {}) if isinstance(document, dict) else {}
    if not isinstance(servers, dict):
        _CONFIG_DIAGNOSTICS.append({"server": "*", "error": "'mcpServers' must be an object"})
        return []

    result: list[MCPServerConfig] = []
    for name, raw in servers.items():
        try:
            result.append(_parse_server_config(str(name), raw))
        except (MCPError, TypeError, ValueError) as exc:
            _CONFIG_DIAGNOSTICS.append({"server": str(name), "error": str(exc)})
    return result


def mcp_config_diagnostics() -> list[dict[str, str]]:
    return list(_CONFIG_DIAGNOSTICS)


def _safe_endpoint(config: MCPServerConfig) -> str:
    if config.transport == "stdio":
        return Path(config.command or "").name or "missing command"
    parts = urlsplit(config.url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _tool_capability(tool: types.Tool) -> Capability:
    annotations = tool.annotations
    return Capability.SEARCH if annotations and annotations.readOnlyHint is True else Capability.ACTION


def _tool_spec(tool: types.Tool) -> ConnectorToolSpec:
    properties = tool.inputSchema.get("properties", {}) if isinstance(tool.inputSchema, dict) else {}
    input_schema: dict[str, str] = {}
    if isinstance(properties, dict):
        for key, value in properties.items():
            schema = value if isinstance(value, dict) else {}
            input_schema[str(key)] = str(schema.get("description") or schema.get("type") or "value")
    capability = _tool_capability(tool)
    annotations = tool.annotations
    destructive = bool(annotations and annotations.destructiveHint)
    return ConnectorToolSpec(
        name=tool.name,
        description=tool.description or f"MCP tool {tool.name}",
        capability=capability,
        input_schema=input_schema,
        output="MCP CallToolResult",
        risk=ActionRisk.HIGH if destructive else (ActionRisk.LOW if capability == Capability.SEARCH else ActionRisk.MEDIUM),
        requires_confirmation=capability == Capability.ACTION,
        mcp_tool=True,
        read_only_hint=capability == Capability.SEARCH,
        destructive_hint=destructive,
    )


def _result_value(result: types.CallToolResult) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent
    blocks: list[Any] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            try:
                blocks.append(json.loads(block.text))
            except json.JSONDecodeError:
                blocks.append(block.text)
        else:
            blocks.append(block.model_dump(mode="json", by_alias=True, exclude_none=True))
    if len(blocks) == 1:
        return blocks[0]
    return blocks


class MCPConnector(Connector):
    """Official-SDK MCP client adapted into the Autopilot connector contract."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.manifest = ConnectorManifest(
            name=config.id,
            description=config.description or f"MCP server '{config.display_name}'.",
            category="External (MCP)",
            auth_mode=AuthMode.MCP,
            capabilities=[],
            scopes=["mcp.tools"],
            objects=["mcp_tools"],
            tools=[],
            auth_required=False,
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[_Request] | None = None
        self._ready: asyncio.Event | None = None
        self._start_lock: asyncio.Lock | None = None
        self._connected = False
        self._last_error: str | None = None
        self._server_name: str | None = None
        self._server_version: str | None = None
        self._tools: dict[str, types.Tool] = {}

    def readiness(self, action: str | None = None) -> dict[str, Any]:
        configured = self.config.enabled
        return {
            "configured": configured,
            "action_ready": self._connected,
            "missing": [] if configured else ["enabled"],
            "mode": "mcp_live" if self._connected else ("mcp_configured" if configured else "disabled"),
            "detail": (
                f"Negotiated MCP session with {len(self._tools)} tool(s)."
                if self._connected
                else (self._last_error or "MCP server is configured but has not completed a handshake.")
                if configured
                else "MCP server is present in configuration but disabled."
            ),
            "action": action,
            "integration_live": self._connected,
            "transport": self.config.transport,
            "endpoint": _safe_endpoint(self.config),
            "server_name": self._server_name,
            "server_version": self._server_version,
            "tool_count": len(self._tools),
            "last_error": self._last_error,
        }

    @asynccontextmanager
    async def _transport(self) -> AsyncIterator[tuple[Any, Any]]:
        if self.config.transport == "stdio":
            params = StdioServerParameters(
                command=self.config.command or "",
                args=list(self.config.args),
                env=self.config.env or None,
                cwd=self.config.cwd,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                yield read_stream, write_stream
            return

        async with streamablehttp_client(
            self.config.url or "",
            headers=self.config.headers or None,
            timeout=self.config.timeout_seconds,
            sse_read_timeout=max(self.config.timeout_seconds, 60),
        ) as (read_stream, write_stream, _):
            yield read_stream, write_stream

    async def connect(self, force: bool = False) -> dict[str, Any]:
        if not self.config.enabled:
            raise MCPError(f"MCP server '{self.config.display_name}' is disabled")
        if self._connected and not force:
            return self.readiness()
        if force:
            await self.close()

        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._connected:
                return self.readiness()
            if self._worker_task is None or self._worker_task.done():
                self._queue = asyncio.Queue()
                self._ready = asyncio.Event()
                self._last_error = None
                self._worker_task = asyncio.create_task(self._worker(), name=f"{self.config.id}-mcp")

        assert self._ready is not None
        await asyncio.wait_for(self._ready.wait(), timeout=self.config.timeout_seconds)
        if not self._connected:
            raise MCPError(self._last_error or "MCP handshake failed")
        return self.readiness()

    async def _discover_tools(self, session: ClientSession) -> None:
        discovered: dict[str, types.Tool] = {}
        cursor: str | None = None
        while True:
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                if self.config.allowed_tools and tool.name not in self.config.allowed_tools:
                    continue
                discovered[tool.name] = tool
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                break
        self._tools = discovered
        specs = [_tool_spec(tool) for tool in discovered.values()]
        self.manifest.tools = specs
        capabilities = {spec.capability for spec in specs}
        self.manifest.capabilities = sorted(capabilities, key=lambda item: item.value)
        self.manifest.safe_actions = [
            name
            for name in self.config.allowed_write_tools
            if name in discovered and _tool_capability(discovered[name]) == Capability.ACTION
        ]

    async def _worker(self) -> None:
        assert self._queue is not None
        assert self._ready is not None
        try:
            async with self._transport() as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self._server_name = initialized.serverInfo.name
                    self._server_version = initialized.serverInfo.version
                    await self._discover_tools(session)
                    self._connected = True
                    self._ready.set()
                    log.info(
                        "MCP server %s negotiated as %s %s with %d tools",
                        self.config.id,
                        self._server_name,
                        self._server_version,
                        len(self._tools),
                    )

                    while True:
                        request = await self._queue.get()
                        if request.operation == "close":
                            if not request.future.done():
                                request.future.set_result(None)
                            break
                        try:
                            if request.operation == "refresh":
                                await self._discover_tools(session)
                                value: Any = self.readiness()
                            elif request.operation == "call" and request.tool_name:
                                value = await session.call_tool(request.tool_name, request.arguments or {})
                            else:
                                raise MCPError(f"Unsupported MCP worker operation: {request.operation}")
                            if not request.future.done():
                                request.future.set_result(value)
                        except Exception as exc:
                            if not request.future.done():
                                request.future.set_exception(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("MCP server %s failed: %s", self.config.id, self._last_error)
        finally:
            self._connected = False
            self._ready.set()
            while self._queue and not self._queue.empty():
                request = self._queue.get_nowait()
                if not request.future.done():
                    request.future.set_exception(MCPError(self._last_error or "MCP session closed"))

    async def _request(self, operation: str, tool_name: str | None = None, arguments: dict[str, Any] | None = None) -> Any:
        await self.connect()
        assert self._queue is not None
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Request(operation, future, tool_name, arguments))
        try:
            return await asyncio.wait_for(future, timeout=self.config.timeout_seconds)
        except TimeoutError as exc:
            raise MCPError(f"MCP operation timed out after {self.config.timeout_seconds:g}s") from exc

    async def refresh(self) -> dict[str, Any]:
        return await self._request("refresh")

    async def invoke_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        await self.connect()
        tool = self._tools.get(name)
        if tool is None:
            raise MCPError(f"Unknown MCP tool '{name}'")
        result = await self._request("call", name, arguments or {})
        if result.isError:
            raise MCPToolError(str(_result_value(result)))
        return _result_value(result)

    def as_tools(self) -> list[Any]:
        """Expose only tools explicitly annotated read-only to autonomous agents."""
        from autopilot.agents.base import Tool

        result: list[Tool] = []
        for remote in self._tools.values():
            if _tool_capability(remote) != Capability.SEARCH:
                continue
            properties = remote.inputSchema.get("properties", {}) if isinstance(remote.inputSchema, dict) else {}
            parameters = {
                str(key): str((value if isinstance(value, dict) else {}).get("description") or "value")
                for key, value in properties.items()
            }

            async def proxy(_name: str = remote.name, **kwargs: Any) -> Any:
                return await self.invoke_tool(_name, kwargs)

            result.append(
                Tool(
                    name=f"{self.config.id}_{_slug(remote.name)}",
                    description=remote.description or f"Read-only MCP tool {remote.name}",
                    parameters=parameters,
                    fn=proxy,
                )
            )
        return result

    def _query_tool(self) -> tuple[types.Tool, str] | None:
        for tool in self._tools.values():
            if _tool_capability(tool) != Capability.SEARCH:
                continue
            properties = tool.inputSchema.get("properties", {}) if isinstance(tool.inputSchema, dict) else {}
            if not isinstance(properties, dict):
                continue
            for key in ("query", "search", "q", "url", "ref", "path"):
                if key in properties:
                    return tool, key
            if len(properties) == 1:
                return tool, str(next(iter(properties)))
        return None

    async def search(self, query: str) -> list[Evidence]:
        await self.connect()
        selection = self._query_tool()
        if selection is None:
            raise MCPError("This MCP server has no read-only tool accepting a query-like input")
        tool, parameter = selection
        value = await self.invoke_tool(tool.name, {parameter: query})
        summary = json.dumps(value, default=str, ensure_ascii=True)
        return [
            Evidence(
                source=self.manifest.name,
                title=f"{self.config.display_name}: {tool.name}",
                summary=summary[:4000],
                confidence=0.75,
                metadata={"mcp_tool": tool.name, "result": value},
            )
        ]

    async def read(self, ref: str) -> dict[str, Any]:
        evidence = await self.search(ref)
        return evidence[0].metadata

    async def action(self, name: str, payload: dict[str, Any]) -> ActionResult:
        if name not in self.config.allowed_write_tools:
            raise MCPError(
                f"MCP write tool '{name}' is not allowed; add it to allowedWriteTools after reviewing its side effects"
            )
        value = await self.invoke_tool(name, payload)
        return ActionResult(
            connector=self.manifest.name,
            action=name,
            status="complete",
            summary=f"MCP tool '{name}' completed.",
            metadata={"result": value},
        )

    async def close(self) -> None:
        task = self._worker_task
        if task is None:
            return
        if not task.done() and self._queue is not None:
            future = asyncio.get_running_loop().create_future()
            await self._queue.put(_Request("close", future))
            with suppress(Exception):
                await asyncio.wait_for(future, timeout=min(self.config.timeout_seconds, 5))
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=min(self.config.timeout_seconds, 10))
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._worker_task = None


def load_mcp_connectors(path: Path | str | None = None) -> list[MCPConnector]:
    return [MCPConnector(config) for config in load_mcp_server_configs(path)]
