# AUTOPILOT

**Autonomous Operator Runtime** for connected-system events and bounded actions.

AUTOPILOT is not a single-app bot. It operates over connectors that declare what
they can read, search, write, notify, or safely execute.

```text
connected surface -> normalized signal -> mission graph -> scoped operators
-> policy gate -> bounded action -> trace + memory
```

## Current Build

This repository contains a working MVP:

- FastAPI webhook and dashboard server
- SQLite-backed mission, step, trace, graph, and memory persistence
- capability-declared connector registry
- generic webhook connector
- local knowledge connector with optional Tavily web search
- artifact connector for durable Markdown reports
- notification connector with Slack webhook or local fallback
- scoped operator suite with optional LLM reasoning and deterministic fallback
- adaptive replanning when correlated signals arrive mid-mission or confidence is low
- explicit policy decisions before side effects
- live dashboard with mission graph, evidence, policy, actions, and trace

## Quickstart

```powershell
cd C:\Users\aacer\Documents\Anvil\autopilot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_server.py
```

Open:

```text
http://127.0.0.1:8090
```

Click **Run Demo**.

The demo emits three asynchronous events:

1. support escalation
2. monitoring error spike
3. rollout status signal

AUTOPILOT correlates them into one mission, expands the mission graph, performs
a follow-up investigation branch, verifies confidence, applies policy, writes a
report, and emits a notification action.

## Webhook Example

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8090/webhooks/custom `
  -ContentType "application/json" `
  -Body '{"type":"support_escalation","summary":"Enterprise customer reports failed exports after rollout.","entities":["export service","rollout"],"urgency":"high"}'
```

## Optional LLM Providers

AUTOPILOT auto-detects the first configured provider:

| Provider | Environment variable | Default model |
| --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `google/gemini-2.5-flash` |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

Without a provider key, all operators use deterministic heuristics. This keeps
the demo reliable offline.

To force deterministic mode:

```powershell
$env:AUTOPILOT_DISABLE_LLM="1"
```

## Connectors

| Connector | Capabilities | Safe actions |
| --- | --- | --- |
| `webhook` | read | none |
| `knowledge` | search, read | none |
| `artifact` | write, action | `write_report`, `write_action_packet` |
| `notification` | notify, action | `notify_ops`, `webhook_callback` |

## Architecture

| Layer | Responsibility |
| --- | --- |
| Connector registry | Declares capabilities, event types, safe actions, reliability |
| Normalized models | `Signal`, `Mission`, `Hypothesis`, `Evidence`, `ActionResult` |
| Runtime kernel | Correlation, scheduling, graph execution, retries, persistence |
| Operator suite | Evaluation, planning, investigation, verification, replanning, synthesis |
| Policy engine | Blocks unsafe or low-confidence side effects |
| Trace sink | Local traces with Omium-ready metadata |

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

<<<<<<< HEAD
## Environment & Configuration

Create a `.env` file at the project root (you can copy `.env.example`) and populate required values. Key environment variables:

- `GITHUB_TOKEN` — Personal Access Token or GitHub App installation token (needed for `GitHubAgent`).
- `GITHUB_API_URL` — GitHub API base URL (defaults to `https://api.github.com`).
- `REDIS_URL` — Optional Redis URL for hot caching (e.g. `redis://localhost:6379/0`).
- `POSTGRES_DSN` — Optional Postgres DSN for durable storage (future adapter).
- `SLACK_BOT_TOKEN`, `TEAMS_WEBHOOK_URL` — Optional notification connectors.

Quick start (Windows PowerShell):

```powershell
cd C:\path\to\autopilot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
# copy example env
copy .env.example .env
# edit .env to add tokens
$env:PYTHONPATH="src"
uvicorn autopilot.api.main:app --reload --port 8080
```

Open the dashboard at `http://127.0.0.1:8080` and click **Run Demo**.

CI / automated runs: add `GITHUB_TOKEN` and other secrets to your CI environment (GitHub Actions secrets, etc.).

=======
## Omium

Set `OMIUM_API_KEY` in `.env` to mark traces as Omium-ready. The current
implementation records local trace events with causal step IDs; the integration
point is isolated in `src/autopilot/tracing/omium.py`.
>>>>>>> 7e86d18fb019511de1ac9377bbddc4d936f91751
