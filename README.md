# AUTOPILOT

**Autonomous Operator Runtime** for connected systems.

AUTOPILOT ingests events from connector surfaces, normalizes them into missions, dynamically coordinates scoped operators, gathers evidence, replans when confidence is low, executes bounded actions, and stores a durable trace.

## What Is Implemented

- Connector registry with capability declarations
- Generic webhook connector
- Knowledge/search connector with local runbooks and optional Tavily live search
- Artifact writer connector
- Notification connector with Slack webhook or local fallback
- SQLite-backed missions, steps, traces, and memory
- Runtime kernel with correlation, dynamic subagents, verification, adaptive replanning, synthesis, and bounded actions
- FastAPI API and SSE-powered dashboard
- Omium-ready local tracing shim

## Quickstart

```powershell
cd C:\Users\aacer\Documents\Anvil\autopilot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH="src"
uvicorn autopilot.api.main:app --reload --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

Click **Run Demo**. AUTOPILOT will emit multiple asynchronous signals, correlate them into one mission, spawn operator steps, replan if confidence is low, publish artifacts, and show the execution graph.

## Webhook Example

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8080/webhooks/custom `
  -ContentType "application/json" `
  -Body '{"type":"support_escalation","summary":"Enterprise customer reports failed exports after today''s rollout.","entities":["export service","rollout"],"urgency":"high"}'
```

## Demo Thesis

AUTOPILOT is not built for one app. It operates over any connector that implements the capability interface:

```text
connected surface -> normalized signal -> mission graph -> dynamic operators -> capability router -> bounded action -> trace + memory
```

The included demo uses generic webhook, knowledge, artifact, and notification connectors to prove the runtime without binding the product to a single domain.

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

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

