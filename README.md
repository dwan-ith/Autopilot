# AUTOPILOT

AUTOPILOT is a local-first autonomous operator runtime. It ingests operational
signals, correlates related events into missions, runs bounded investigation
agents, scores confidence, and publishes policy-gated actions such as local
artifacts, notifications, and connector-backed follow-up work.

This repository is an MVP, not a production incident-response platform yet. The
core runtime, SQLite persistence, mission graph, dashboard, policy engine,
artifact writing, webhook ingestion, local knowledge search, and several real
credential-backed connector adapters are implemented. Many external systems are
usable only when credentials are configured, and some catalog entries are still
demo or adapter-ready surfaces rather than deeply integrated product workflows.

## What Works Today

- Backend API with FastAPI and SQLite persistence.
- Runtime kernel with mission lifecycle, correlation window, replanning, memory,
  traces, approvals, and action history.
- Next.js dashboard for missions, connectors, approvals, traces, and analytics.
- Heuristic mode when no LLM provider is configured.
- Optional OpenRouter/Groq provider pool for agent reasoning.
- Webhook ingestion with optional API key and HMAC signature protection.
- Local artifact reports and JSON action packets.
- Local notification fallback, plus Slack webhook notifications when configured.
- Real connector adapters for GitHub, Linear, Notion, Tavily, Weather, Sentry,
  Gmail, and Google Drive, subject to credentials and current API scopes.

## Current Limits

- This is local/demo-grade by default. There is no multi-tenant auth model,
  hosted deployment hardening, RBAC, encrypted secret store, or durable worker
  queue.
- The connector directory is broader than the proven production surface. Treat
  every connector as "credential-gated and needs live validation" unless the
  tests cover the exact workflow you plan to use.
- Autonomous side effects are intentionally narrow. High-risk actions require
  explicit approval, and most write actions need confidence thresholds plus
  connector readiness.
- The offline heuristic path can demonstrate orchestration, but it is not a
  substitute for grounded live evidence from configured systems.

## Setup

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
python run_server.py
```

Backend URL: `http://localhost:8090`

### Frontend

```bash
cd client
npm install
npm run dev
```

Frontend URL: `http://localhost:3000`

### Docker

```bash
docker compose up --build
```

## Important Environment Variables

- `AUTOPILOT_API_KEY`: protects write endpoints and sensitive read streams when set.
- `AUTOPILOT_WEBHOOK_SECRET`: enables HMAC verification for inbound webhooks.
- `AUTOPILOT_DISABLE_LLM=1`: forces deterministic heuristic mode.
- `OPENROUTER_API_KEY*`, `GROQ_API_KEY*`: optional LLM provider pool.
- `GITHUB_TOKEN`, `LINEAR_API_KEY`, `NOTION_API_KEY`, `TAVILY_API_KEY`,
  `OPENWEATHER_API_KEY`, `SENTRY_TOKEN`, `SENTRY_ORG`: optional connector credentials.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: enables Gmail/Drive OAuth flows.
- `SLACK_WEBHOOK_URL`: enables Slack notifications instead of local fallback.

See `.env.example` for the full set.

## API Highlights

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Runtime health and provider mode |
| `POST` | `/api/signals` | Ingest a manual/normalized signal |
| `POST` | `/demo/fire` | Emit the built-in three-signal scenario |
| `GET` | `/api/missions` | List missions |
| `GET` | `/api/missions/{id}` | Mission detail, steps, and traces |
| `GET` | `/api/connectors` | Runtime connector readiness |
| `GET` | `/api/connector-directory` | User-facing connector catalog |
| `GET` | `/api/approvals` | Pending action approvals |
| `POST` | `/api/approvals/{id}/approve` | Execute a queued approval |
| `POST` | `/api/approvals/{id}/reject` | Reject a queued approval |
| `GET` | `/api/analytics/missions` | Mission aggregate stats |
| `GET` | `/api/analytics/agents` | Agent performance stats |
| `GET` | `/api/analytics/connectors` | Connector action health |
| `GET` | `/oauth/authorize/{connector_id}` | Start Google OAuth |
| `GET` | `/oauth/status` | OAuth authorization status |
| `DELETE` | `/oauth/revoke/{connector_id}` | Revoke stored OAuth tokens |
| `POST` | `/webhooks/{connector_name}` | Ingest a connector webhook |

## Example Signal

```bash
curl -X POST http://localhost:8090/api/signals ^
  -H "Content-Type: application/json" ^
  -H "x-autopilot-key: your-secret-key" ^
  -d "{\"source\":\"sentry\",\"type\":\"error.spike\",\"summary\":\"API error rate jumped from 1% to 38%\",\"entities\":[\"checkout-service\",\"payments\"],\"urgency\":\"high\"}"
```

## Tests

```bash
python -m unittest discover -s tests -v
cd client
npm run lint
npm run build
```
