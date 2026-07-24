from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from autopilot.connectors import default_registry
from autopilot.connectors.mcp import (
    MCPConnector,
    MCPError,
    load_mcp_server_configs,
    mcp_config_diagnostics,
)
from autopilot.kernel import RuntimeKernel
from autopilot.mcp_server import AutopilotMCPHTTP, AutopilotMCPServer
from autopilot.storage import Store

ROOT = Path(__file__).resolve().parents[1]


class MCPConfigTests(unittest.TestCase):
    def test_config_is_validated_without_executing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text(
                '{"mcpServers":{"broken":{"enabled":true,"transport":"stdio"}}}',
                encoding="utf-8",
            )
            self.assertEqual(load_mcp_server_configs(path), [])
            self.assertIn("requires a string 'command'", mcp_config_diagnostics()[0]["error"])

    def test_disabled_http_server_does_not_require_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text(
                """
                {
                  "mcpServers": {
                    "remote": {
                      "enabled": false,
                      "transport": "streamable_http",
                      "url": "https://example.invalid/mcp",
                      "headers": {"Authorization": "Bearer ${MISSING_TEST_TOKEN}"}
                    }
                  }
                }
                """,
                encoding="utf-8",
            )
            configs = load_mcp_server_configs(path)
            self.assertEqual(len(configs), 1)
            self.assertFalse(configs[0].enabled)


class MCPContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = load_mcp_server_configs(ROOT / "data" / "mcp_servers.json")[0]
        env = dict(config.env or {})
        env["PYTHONPATH"] = str(ROOT / "src")
        self.connector = MCPConnector(
            config.__class__(
                **{
                    **config.__dict__,
                    "command": sys.executable,
                    "cwd": str(ROOT),
                    "env": env,
                }
            )
        )

    async def asyncTearDown(self) -> None:
        await self.connector.close()

    async def test_real_sdk_handshake_discovery_and_tool_call(self) -> None:
        status = await self.connector.connect()
        self.assertEqual(status["mode"], "mcp_live")
        self.assertGreater(status["tool_count"], 0)
        self.assertEqual(status["server_name"], "autopilot")

        names = {tool.name for tool in self.connector.manifest.tools}
        self.assertIn("knowledge_search", names)
        result = await self.connector.invoke_tool(
            "knowledge_search",
            {"query": "export failure after rollout"},
        )
        self.assertIn("result", result)
        self.assertTrue(result["result"])

    async def test_only_read_only_annotated_tools_reach_agent_loop(self) -> None:
        await self.connector.connect()
        tools = self.connector.as_tools()
        self.assertTrue(tools)
        self.assertTrue(all(tool.name.startswith("mcp_autopilot_native_") for tool in tools))

    async def test_unknown_tool_fails_instead_of_returning_simulated_text(self) -> None:
        await self.connector.connect()
        with self.assertRaises(MCPError):
            await self.connector.invoke_tool("not_a_real_tool", {})


class MCPHTTPContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamable_http_auth_handshake_and_control_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "mcp-http.db")
            registry = default_registry(include_mcp=False)
            runtime = RuntimeKernel(store, registry, correlation_window_seconds=0.01)
            transport = AutopilotMCPHTTP(
                AutopilotMCPServer(registry=registry, store=store, runtime=runtime)
            )
            app = FastAPI()
            app.mount("/mcp", transport)

            def client_factory(headers=None, timeout=None, auth=None):
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                )

            previous = os.environ.get("AUTOPILOT_MCP_API_KEY")
            os.environ["AUTOPILOT_MCP_API_KEY"] = "mcp-contract-key"
            try:
                async with transport.run():
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as unauthenticated:
                        response = await unauthenticated.post("/mcp/", json={})
                        self.assertEqual(response.status_code, 401)

                    async with streamablehttp_client(
                        "http://testserver/mcp/",
                        headers={"Authorization": "Bearer mcp-contract-key"},
                        httpx_client_factory=client_factory,
                    ) as (read_stream, write_stream, _):
                        async with ClientSession(read_stream, write_stream) as session:
                            initialized = await session.initialize()
                            self.assertEqual(initialized.serverInfo.name, "autopilot")
                            tools = await session.list_tools()
                            by_name = {item.name: item for item in tools.tools}
                            self.assertIn("autopilot_status", by_name)
                            self.assertIn("autopilot_submit_signal", by_name)
                            self.assertFalse(
                                by_name["autopilot_submit_signal"].annotations.readOnlyHint
                            )
                            result = await session.call_tool("autopilot_status", {})
                            self.assertFalse(result.isError)
                            self.assertEqual(result.structuredContent["result"]["status"], "ok")
            finally:
                if previous is None:
                    os.environ.pop("AUTOPILOT_MCP_API_KEY", None)
                else:
                    os.environ["AUTOPILOT_MCP_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
