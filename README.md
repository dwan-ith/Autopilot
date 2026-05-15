# AUTOPILOT

AUTOPILOT is an autonomous operator runtime that processes operational signals, coordinates investigation agents, and safely executes policy-gated actions. It uses an LLM-backed reasoning loop to automate incident response workflows, such as searching runbooks, querying external APIs, and posting status updates.

The platform is built around strict policy gates and human-in-the-loop approvals to ensure safe side effects in connected environments.

## Features

- **Event Correlation**: Ingests signals via webhooks and aggregates related events into active missions.
- **Agent Swarm**: Uses a ReAct-based agent loop to gather evidence across connected tools.
- **Policy Engine**: Enforces rules based on connector capabilities, risk levels, and confidence thresholds.
- **Human-in-the-Loop**: High-risk actions require explicit approval from the dashboard before execution.
- **Observability**: SQLite-backed state management records every agent thought, tool call, and state transition.
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

## Connectors

AUTOPILOT integrates with external systems via connectors. Each connector exposes specific read/write capabilities and tools to the agent swarm. 

Implemented integrations include:
- **GitHub**: Search issues/PRs, create issues, post comments.
- **Linear**: Create issues.
- **Communication**: Slack (notifications), Gmail (draft/send replies).
- **Knowledge**: Local runbooks, Google Drive, Notion, Tavily.
- **Observability**: Sentry, PagerDuty, Weather.
- **System**: Durable artifacts and generic webhooks.

*Note: Connectors require valid API keys or OAuth credentials to function.*

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
| `AUTOPILOT_API_KEY` | Protects API endpoints and webhooks. |
| `AUTOPILOT_WEBHOOK_SECRET` | Enables HMAC signature verification for inbound webhooks. |
| `AUTOPILOT_DISABLE_LLM=1` | Runs the system in offline, deterministic heuristic mode. |
| `OPENROUTER_API_KEY` | Primary LLM provider key (recommended). |
| `GROQ_API_KEY` | Fallback LLM provider key. |
| `SLACK_WEBHOOK_URL` | Enables Slack notifications for the `notify_ops` action. |

Additional connector-specific keys (e.g., `GITHUB_TOKEN`, `NOTION_API_KEY`) are documented in `.env.example`.

## Testing

```bash
# Run backend test suite
python -m pytest tests/ -v

# Check frontend types and linting
cd client
npm run lint
npm run build
```

## Hackathon Demo

See `HACKATHON_CHECKLIST.md` for the required-capability map, demo script, and
rubric score estimate.

Useful judge-facing endpoints:

- `GET /api/operators`: all operators, tool schemas, safe actions, readiness.
- `POST /api/operators/web_search/probe`: local knowledge/web-search probe.
- `POST /api/operators/weather/probe`: weather probe with Open-Meteo fallback when no key is set.
- `POST /api/operators/local_artifacts/probe`: real local artifact side effect.
- `POST /demo/fire`: asynchronous three-signal autonomous mission.
