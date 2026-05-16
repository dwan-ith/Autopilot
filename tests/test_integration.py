"""Integration tests — require real LLM and connector access.

Gated by:
  AUTOPILOT_RUN_INTEGRATION=1    Run tests against real LLM providers
  GROQ_API_KEY                   At least one Groq key (required for integration)

These tests are NOT run in CI by default. They exist to verify E2E mission
completion outside of the unit test stubs that mock the LLM.

Run:
  AUTOPILOT_RUN_INTEGRATION=1 pytest tests/test_integration.py -v
"""

from __future__ import annotations

import os
import asyncio
import json
import pytest
import pytest_asyncio


# ── Gate: skip entire module unless explicitly opted in ─────────────────────

RUN_INTEGRATION = os.getenv("AUTOPILOT_RUN_INTEGRATION", "").lower() in {"1", "true", "yes"}
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not RUN_INTEGRATION:
    pytest.skip(
        "Integration tests skipped — set AUTOPILOT_RUN_INTEGRATION=1 to run",
        allow_module_level=True,
    )

if not GROQ_KEY:
    pytest.skip(
        "Integration tests require GROQ_API_KEY — no Groq key found",
        allow_module_level=True,
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    """Shared asyncio event loop for the module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def integration_store(tmp_path_factory):
    """SQLite store backed by a temp path for integration tests."""
    from autopilot.storage import Store
    db = tmp_path_factory.mktemp("integration") / "integration.db"
    return Store(db)


@pytest.fixture(scope="module")
def integration_registry():
    """Connector registry with all free/keyless connectors loaded."""
    from autopilot.connectors.registry import default_registry
    return default_registry()


@pytest.fixture(scope="module")
def integration_kernel(integration_store, integration_registry):
    """Full RuntimeKernel backed by real store and registry."""
    from autopilot.kernel.runtime import RuntimeKernel
    return RuntimeKernel(store=integration_store, registry=integration_registry)


# ── LLM provider tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_groq_responds():
    """Verify that at least one Groq slot returns a parseable JSON response."""
    from autopilot.operators.llm import reason, parse_json

    raw = await reason(
        role="investigator",
        messages=[{
            "role": "user",
            "content": (
                "You are a sub-agent in AUTOPILOT. Reply with exactly:\n"
                "{\"action\": \"answer\", \"result\": {\"status\": \"ok\", \"provider\": \"groq\"}}"
            ),
        }],
    )

    assert raw is not None, "reason() returned None — LLM call failed"
    parsed = parse_json(raw)
    assert isinstance(parsed, dict), f"Response was not JSON: {raw[:200]}"
    assert parsed.get("action") == "answer", f"Expected action=answer, got: {parsed}"
    result = parsed.get("result", {})
    assert result.get("status") == "ok", f"Expected status=ok, got: {result}"


@pytest.mark.asyncio
async def test_subagent_tool_use(integration_registry):
    """Verify SubAgent can call a real tool (Tavily/DDG web search) via ReAct loop."""
    from autopilot.agents.base import SubAgent, Tool
    from autopilot.connectors.tavily import TavilyConnector

    tavily = TavilyConnector()

    async def search_fn(query: str):
        results = await tavily.search(query)
        return [{"source": e.source, "title": e.title, "summary": e.summary[:100]} for e in results]

    tools = [
        Tool(
            name="web_search",
            description="Search the web for current information.",
            parameters={"query": "Search query string"},
            fn=search_fn,
        )
    ]

    agent = SubAgent(
        role="investigator",
        tools=tools,
        max_steps=4,
        system_prompt=(
            "You are a test sub-agent. Use the web_search tool to find information about "
            "'Python asyncio'. Then return: {\"action\": \"answer\", \"result\": {\"found\": true}}"
        ),
    )

    result = await agent.run("Search for information about Python asyncio and confirm you found results.")

    assert result.success, f"SubAgent failed: {result.error}"
    assert result.tool_calls_made >= 1, "SubAgent made no tool calls — not using real tools"
    assert isinstance(result.answer, dict), f"Answer not a dict: {result.answer}"


# ── Mission E2E test ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_mission_ingestion(integration_kernel):
    """Verify a complete mission lifecycle with real LLM and free connectors."""
    from autopilot.models import Signal

    signal = Signal(
        source="integration_test",
        type="incident.test",
        summary="Integration test: High error rate on auth service for 5 minutes",
        entities=["auth-service", "production"],
        urgency="high",
        payload={"test": True, "integration": True},
    )

    mission = await integration_kernel.ingest(signal)

    assert mission is not None, "ingest() returned None"
    assert mission.id, "Mission has no ID"
    assert mission.title, "Mission has no title"
    assert mission.status.value in {"queued", "running", "resolved", "unresolved", "failed"}, \
        f"Unexpected mission status: {mission.status}"

    # Run full mission
    timeout_s = int(os.getenv("AUTOPILOT_MISSION_TIMEOUT_SECONDS", "120"))
    completed = await asyncio.wait_for(
        integration_kernel.run_mission(mission),
        timeout=timeout_s,
    )

    assert completed is not None, "run_mission() returned None"
    assert completed.status.value in {"resolved", "unresolved"}, \
        f"Mission didn't finish: {completed.status}"
    assert completed.confidence > 0.0, "Final confidence was 0 — agent loop didn't run"
    assert len(completed.agent_runs) > 0, "No agent runs recorded"
    assert len(completed.evidence) > 0, "No evidence gathered — investigator didn't run"


# ── Memory agent tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_type_keyed_recall(integration_store):
    """Verify type-keyed memory recall returns the stored pattern on the same key."""
    from autopilot.agents.persistent.memory import MemoryAgent
    from autopilot.models import Signal, Mission

    mem = MemoryAgent(integration_store)

    # Build a minimal mock mission with a typed signal
    signal = Signal(
        source="test",
        type="incident.auth",
        summary="Auth service down",
        entities=["auth"],
        urgency="high",
        payload={},
    )
    mission = Mission(
        title="Test auth incident",
        summary="Auth is down",
        severity="high",
        signals=[signal],
    )

    # Store a resolution
    mem.remember(mission, "resolved")

    # Recall should surface the typed entry
    recalls = integration_store.recall("resolution:incident", limit=5)
    assert len(recalls) >= 1, "remember() should have stored at least one type-keyed entry"
    assert "auth incident" in recalls[0].lower() or "Test" in recalls[0], \
        f"Expected mission title in recall, got: {recalls[0]}"


# ── Connector health tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_weather_connector_open_meteo():
    """Verify WeatherConnector returns real weather data via Open-Meteo (no API key needed)."""
    from autopilot.connectors.weather import WeatherConnector

    wc = WeatherConnector()
    result = await wc.read("London")

    assert "error" not in result, f"Weather connector returned error: {result}"
    assert result.get("temperature_c") is not None, f"No temperature returned: {result}"
    assert result.get("provider") in {"openweathermap", "open_meteo"}, f"Unexpected provider: {result}"


@pytest.mark.asyncio
async def test_tavily_ddg_fallback():
    """Verify TavilyConnector returns results via DDG/HN when no API key is set."""
    import os
    # Temporarily unset Tavily key to force fallback
    original = os.environ.pop("TAVILY_API_KEY", None)

    from autopilot.connectors.tavily import TavilyConnector
    tc = TavilyConnector()

    try:
        results = await tc.search("Python programming language")
        assert len(results) > 0, "DDG/HN fallback returned no results"
        assert all(hasattr(r, "source") for r in results), "Evidence objects malformed"
    finally:
        if original:
            os.environ["TAVILY_API_KEY"] = original


# ── Security audit tests ──────────────────────────────────────────────────────

def test_security_audit_pattern_detection():
    """Verify SecurityAuditAgent detects high-risk patterns in changed files."""
    from autopilot.agents.specialized import SecurityAuditAgent
    from autopilot.connectors.registry import default_registry
    from autopilot.storage import Store
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(path=(lambda: __import__("pathlib").Path(tmp) / "test.db")())
        registry = default_registry()
        agent = SecurityAuditAgent(store=store, registry=registry)

        report = agent._audit_report({
            "repository": {"full_name": "test/repo"},
            "pull_request": {"number": 42, "title": "Add auth middleware"},
            "changed_files": [
                "src/auth/middleware.py",
                ".env.production",
                "requirements.txt",
                "src/utils.py",
            ],
        })

        assert "Secret Exposure" in report, "Should detect .env.production as secret exposure"
        assert "Auth And Access" in report, "Should detect auth/middleware as auth risk"
        assert "Dependency" in report, "Should detect requirements.txt as dependency risk"
        assert "CRITICAL" in report or "HIGH" in report, "Should produce critical/high severity"
        assert "AUTOPILOT Security Audit" in report, "Report header missing"
