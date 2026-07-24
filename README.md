# AUTOPILOT

AUTOPILOT is an autonomous operator runtime that processes operational signals, coordinates investigation agents, and safely executes policy-gated actions. It uses an LLM-backed reasoning loop to automate incident response workflows, such as searching runbooks, querying external APIs, and posting status updates.

The platform is built around strict policy gates and human-in-the-loop approvals to ensure safe side effects in connected environments.

## Features

- **Event Correlation**: Ingests signals via webhooks and aggregates related events into active missions.
- **Agent Swarm**: Uses a ReAct-based agent loop to gather evidence across connected tools.
- **Policy Engine**: Enforces rules based on connector capabilities, risk levels, and confidence thresholds.
- **Human-in-the-Loop**: High-risk actions require explicit approval from the dashboard before execution.
- **Observability**: SQLite-backed trace store for every operator step, tool transition, and mission lifecycle event.

### Omium (optional dashboard tracing)

The hosted Omium platform uses the **Python SDK** (`omium.init`, `@omium.trace`) and/or its
**REST execution API** — not a generic `POST …/traces` JSON dump. To enable the official client:

```bash
pip install ".[omium]"
```

Set `OMIUM_SDK_INIT=1`, `OMIUM_API_KEY`, and optionally `OMIUM_PROJECT` / `OMIUM_API_URL`.
For a **custom HTTP relay** of SQLite-shaped events only, set `OMIUM_HTTP_INGEST_URL`
(compatible with `X-API-Key` auth) in addition to `OMIUM_API_KEY`.

- **Local Fallback**: Can run in a deterministic, heuristic mode without an LLM provider for testing and validation.

## Architecture 

The pipeline processes missions through nine discrete stages:
1. Memory Check
2. Signal Correlation
3. Planning
4. Investigation
5. Verification
6. Adaptive Replanning (if confidence is low)
7. Action Synthesis
8. Action Publishing
9. Final Validation

AUTOPILOT can also run as a persistent connected-service monitor. Set
`AUTOPILOT_PERSISTENT_MONITORING=1` to make the backend periodically inspect
the connector directory, check runtime readiness, and open an autonomous
mission only when a connected service is degraded. The dashboard's **Check
Services** control runs the same monitor once on demand.

## Connectors

AUTOPILOT integrates with external systems via connectors. Each connector exposes specific read/write capabilities and tools to the agent swarm. 

Implemented integrations include:
- **GitHub**: Search issues/PRs, create issues, post comments.
- **Linear**: Create issues.
- **Communication**: Slack (notifications), Gmail (draft/send replies).
- **Knowledge**: Local runbooks, Google Drive, Notion, Tavily.
- **Observability**: Sentry, Weather.
- **System**: Durable artifacts and generic webhooks.
- **MCP**: Official-SDK stdio and Streamable HTTP clients, plus authenticated
  stdio and Streamable HTTP servers that expose connector reads and AUTOPILOT
  mission control with accurate read/write annotations and bounded polling.

Connector readiness is explicit. Some connectors require user credentials for
real side effects, while others have useful no-key fallbacks:

- **No-key/demo-capable**: local artifacts, local Slack fallback, generic webhooks, local runbooks, Tavily fallback, weather via Open-Meteo.
- **Optional-key upgraded**: GitHub public search without a token, authenticated/private GitHub with `GITHUB_TOKEN`; weather via OpenWeather with `OPENWEATHER_API_KEY`; Tavily AI search with `TAVILY_API_KEY`.
- **Account/OAuth/API-key required for real side effects**: Gmail, Google Drive, Notion, Linear, PagerDuty notes, Slack webhook delivery.

## Setup

### Backend

```bash
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On Unix: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your LLM and connector credentials
python run_server.py
```
Backend runs at: `http://localhost:8090`

### Frontend

```bash
cd client
npm install
npm run dev
```
Dashboard runs at: `http://localhost:3000`

### Docker

```bash
docker compose up --build
```

## Configuration

Set the following environment variables in `.env` to enable specific features:

| Variable | Description |
|---|---|
| `AUTOPILOT_HOST` | Backend bind host; defaults to loopback `127.0.0.1`. Set `0.0.0.0` only behind an authenticated deployment boundary. |
| `AUTOPILOT_API_KEY` | Protects API endpoints and webhooks. |
| `AUTOPILOT_WEBHOOK_SECRET` | Enables HMAC signature verification for inbound webhooks. |
| `AUTOPILOT_OAUTH_STATE_SECRET` | Signs OAuth state tokens; defaults to `AUTOPILOT_API_KEY` when set. |
| `AUTOPILOT_CREDENTIAL_ENCRYPTION_KEY` | Encrypts persisted OAuth tokens; defaults to `AUTOPILOT_API_KEY` when set. |
| `AUTOPILOT_PUBLIC_URL` | Public HTTPS origin used in remote MCP client configuration. |
| `AUTOPILOT_MCP_API_KEY` | Optional separate bearer/API key for `/mcp/`; remote access refuses open unauthenticated hosts. |
| `AUTOPILOT_MCP_ALLOWED_HOSTS` | Exact/wildcard hosts accepted by the MCP DNS-rebinding guard. |
| `AUTOPILOT_MCP_ALLOWED_ORIGINS` | Browser origins accepted by the MCP DNS-rebinding guard. |
| `AUTOPILOT_DISABLE_LLM=1` | Runs the system in offline, deterministic heuristic mode. |
| `AUTOPILOT_PROVIDER_PREFLIGHT_ON_STARTUP` | `1` to run provider preflight at startup and quarantine bad LLM slots before missions. |
| `AUTOPILOT_LLM_TIMEOUT_SECONDS` | Per-provider LLM request timeout. |
| `AUTOPILOT_LLM_MAX_RETRY_DELAY_SECONDS` | Maximum delay honored for provider retry/backoff during demos. |
| `AUTOPILOT_PERSISTENT_MONITORING=1` | Enables background monitoring of connected services. |
| `AUTOPILOT_MONITOR_INTERVAL_SECONDS` | Background monitor interval, minimum 30 seconds. |
| `OPENROUTER_API_KEY` | Primary LLM provider key (recommended). |
| `GROQ_API_KEY` | Fallback LLM provider key. |
| `AUTOPILOT_GROQ_MODEL` | Optional Groq model override. |
| `AUTOPILOT_OPENROUTER_MODEL` | OpenRouter model override (default `openrouter/free`). |
| `OMIUM_API_KEY` | Omium SDK/API authentication (required when Omium integration is enabled). |
| `OMIUM_SDK_INIT` | `1` to call `omium.init()` at startup (after `pip install ".[omium]"`). |
| `OMIUM_PROJECT` | Omium project name (default `autopilot`). |
| `OMIUM_API_URL` | Override Omium API base URL when needed. |
| `OMIUM_HTTP_INGEST_URL` | Optional custom URL to POST trace-shaped JSON (advanced relay only). |
| `GITHUB_CLIENT_ID` / `SECRET` | OAuth credentials for GitHub interactions (issue creation, commenting). |
| `SLACK_ACCESS_TOKEN` | Required for the agent to post messages or respond to incidents. |
| `SLACK_DEFAULT_CHANNEL` | The default channel for Slack fallback messages (e.g., `#ops`). |
| `SENTRY_TOKEN` | Sentry API token for ingesting real error occurrences. |
| `SENTRY_ORG` / `PROJECT` | Scopes the agent's Sentry investigations to specific repositories. |
| `GOOGLE_CLIENT_ID` / `SECRET` | OAuth credentials for Gmail and Google Drive features. |
| `TAVILY_API_KEY` | Upgrades the knowledge connector to perform live AI-driven web searches. |

Additional connector-specific keys (e.g., `GITHUB_TOKEN`, `NOTION_API_KEY`, `LINEAR_API_KEY`) are documented in `.env.example`.

### Model Context Protocol

MCP servers are loaded from `data/mcp_servers.json` (override with
`AUTOPILOT_MCP_CONFIG`). A server is not reported live until the SDK completes
the MCP initialization handshake and tool discovery. The checked-in
`autopilot-native` entry is a local stdio proof that exposes Autopilot's real
read-only connector tools.

```json
{
  "mcpServers": {
    "remote-search": {
      "enabled": true,
      "transport": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${REMOTE_MCP_TOKEN}"
      },
      "allowedTools": ["search"],
      "allowedWriteTools": []
    }
  }
}
```

For stdio, use `command`, `args`, optional `cwd`, and `env`. Configuration never
uses a shell. Environment references use `${NAME}` and unresolved references
block enabled servers. Remote tools reach autonomous investigators only when
the server marks them read-only. Side-effecting tools require both an
`allowedWriteTools` entry and `confirm=true` on the explicit tool-call API.

AUTOPILOT also serves its own MCP interface. It exposes read-only connector
tools plus `autopilot_status`, mission reads, pending-approval reads,
`autopilot_submit_signal`, and `autopilot_wait_for_mission`. Signal submission
is correctly marked non-read-only. Approval execution stays in AUTOPILOT so an
external model cannot silently bypass policy review.

Run the local stdio server directly with:

```bash
autopilot-mcp
# or
python -m autopilot.mcp_server
```

The backend serves authenticated Streamable HTTP at `http://localhost:8090/mcp/`.
`GET /api/mcp/client-config` returns secret-free, environment-based setup
material for each client.

### Connect Codex

The repository includes `.codex/config.toml` for the local backend. Equivalent
manual configuration is:

```toml
[mcp_servers.autopilot]
url = "http://localhost:8090/mcp/"
bearer_token_env_var = "AUTOPILOT_MCP_API_KEY"
```

If `AUTOPILOT_MCP_API_KEY` is blank and `AUTOPILOT_API_KEY` protects the backend,
use `AUTOPILOT_API_KEY` as `bearer_token_env_var`. For a process-local setup,
Codex can also launch `python -m autopilot.mcp_server` over stdio.

### Connect Claude

The repository includes `.mcp.json` with the project-scoped HTTP server.
Equivalent CLI configuration is:

```bash
claude mcp add --transport http autopilot http://localhost:8090/mcp/ \
  --header "Authorization: Bearer your-autopilot-mcp-key"
```

Claude project configuration can use `${AUTOPILOT_MCP_API_KEY}` in the
`Authorization` header; the dashboard renders the exact JSON without embedding
the secret.

### Connect Perplexity Computer

Perplexity custom remote connectors cannot call localhost. Publish the backend
behind HTTPS, then set:

```dotenv
AUTOPILOT_PUBLIC_URL=https://autopilot.example.com
AUTOPILOT_MCP_API_KEY=<strong-random-key>
AUTOPILOT_MCP_ALLOWED_HOSTS=autopilot.example.com
AUTOPILOT_MCP_ALLOWED_ORIGINS=https://www.perplexity.ai
```

In Perplexity, add a Remote custom connector using
`https://autopilot.example.com/mcp/`, choose **Streamable HTTP**, and choose
**API Key** authentication. Local HTTP is intentionally reported as not ready
for Perplexity.

## Testing

```bash
# Run backend test suite
python -m pytest tests/ -v

# Check frontend types and linting
cd client
npm run lint
npm run build
```

## Live Demo & Autonomy

Use **Backend** + **Frontend** from [Setup](#setup). The UI is fully data-driven.

AUTOPILOT no longer runs "canned" simulation demos. The dashboard's **Manual Signal** ingest and the **Persistent Monitor** read actual live configurations from connected services.

When a webhook is received or the monitor triggers, AUTOPILOT correlates the events, expands a dynamic mission graph, spawns parallel investigations using actual search and API tools, verifies confidence, writes an evidence-backed mission brief, and surfaces policy-gated side effects (like creating a GitHub issue or posting to a Slack channel) for human-in-the-loop approval. The dashboard stream reflects these missions globally without hardcoded UI limits.

Highlights:
- `GET /api/mcp/servers` - configured MCP servers, handshake state, and discovered tools.
- `GET /api/mcp/client-config` - Codex, Claude, and Perplexity connection material and remote-readiness truth.
- `POST /api/mcp/servers/{id}/probe` - perform a real MCP handshake and tool discovery.
- `POST /api/mcp/servers/{id}/tools/{tool}` - invoke a discovered tool with explicit write controls.
- `POST /api/monitoring/check` — inspect connected services and create a mission only if a real connected service is degraded.
- `GET /api/operators` — live capability-driven catalog of connectors and readiness states.
- `POST /api/operators/{id}/probe` — bounded probes with real side effects where declared.
- `GET /api/provider/health` — LLM slot health, quarantine status, and bounded live checks.
- `GET /api/tracing/status` — live Omium trace proof status.
