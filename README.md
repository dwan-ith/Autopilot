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
- capability-declared connector registry and product-style connector directory
- generic webhook connector plus Sentry webhook normalization
- local knowledge connector with optional Tavily web search
- artifact connector for durable Markdown reports
- notification connector with Slack webhook, outbound callback, or local fallback
- Linear-first issue creation when `LINEAR_API_KEY` and `LINEAR_TEAM_ID` are configured, with Jira intentionally left as a config stub
- one-action cloud infrastructure connector for deployment webhook or GitHub Actions dispatch
- PR-open security audit connector with a defined artifact-plus-alert output contract
- shared SQLite StateStore for mission state, traces, and durable agent context
- scoped operator suite with optional LLM reasoning and deterministic fallback
- adaptive replanning when correlated signals arrive mid-mission or confidence is low
- explicit per-action policy decisions before side effects
- signal idempotency keys, duplicate suppression, and mission cancellation
- live dashboard with mission graph, evidence, policy, actions, and trace

## Quickstart

```powershell
cd C:\Users\aacer\Documents\Anvil\autopilot
# 1. Start the Backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_server.py

# 2. Start the Modern Frontend (New Terminal)
cd client
npm install
npm run dev
```

Open:

```text
http://localhost:3000
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

## Connector Directory

AUTOPILOT's connector directory is modeled after product connectors in systems
like AI computers and agent workspaces: a searchable catalog of external
services, each with auth mode, scopes, objects, events, capabilities, and safe
actions.

The MVP ships a demo-mode catalog for:

- Gmail
- Slack
- Google Drive
- Notion
- Linear
- Jira
- Sentry
- PagerDuty
- Zendesk
- Web Search
- Local Artifacts

Click **Connect** in the dashboard to create a durable demo connection record.
For API-key or webhook connectors, pass a `credentials_ref` such as
`SLACK_WEBHOOK_URL` or `LINEAR_API_KEY`; secret values are not stored in the
catalog metadata.

## Runtime Adapters

| Connector | Capabilities | Safe actions |
| --- | --- | --- |
| `webhook` | read | none |
| `sentry` | read, search | `mark_investigating` |
| `knowledge` | search, read | none |
| `artifact` | write, action | `write_report`, `write_action_packet` |
| `notification` | notify, action | `notify_ops`, `webhook_callback` |
| `linear` | write, action | `create_issue` |
| `cloud_infra` | action | `trigger_deployment` |
| `security_audit` | read | none |

## Scoped Agent Decisions

- `ProjectMgmtAgent`: Linear is the implemented ticketing path. Jira is represented only by `PROJECT_MGMT_PROVIDER=jira` and returns a stubbed blocked result so the demo does not carry two integrations.
- `CloudInfraAgent`: limited to one bounded action, `trigger_deployment`, through `DEPLOYMENT_WEBHOOK_URL` or GitHub Actions workflow dispatch. AWS, GCP, Azure, rollback, and cost APIs are out of scope for this build.
- `SecurityAuditAgent`: runs on PR-open events through the `security_audit` connector. Its output contract is a local audit artifact plus a Slack notification when `SLACK_WEBHOOK_URL` is configured.
- `StateStore`: `autopilot.state_store.StateStore` is the shared SQLite-backed store for mission state, traces, memory, and analytics reads.

## Bounded Action Examples

Trigger the scoped deployment action:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8090/api/actions/cloud_infra/trigger_deployment `
  -ContentType "application/json" `
  -Body '{"payload":{"environment":"staging","ref":"main","reason":"demo deployment"}}'
```

Create a Linear follow-up issue, or skip external creation when Linear credentials are absent:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8090/api/actions/linear/create_issue `
  -ContentType "application/json" `
  -Body '{"payload":{"title":"Investigate export incident","description":"AUTOPILOT demo follow-up"}}'
```

Send a PR-open security audit trigger:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8090/webhooks/security_audit `
  -ContentType "application/json" `
  -Body '{"action":"opened","repository":{"full_name":"demo/app"},"pull_request":{"number":42,"title":"Update auth flow","head":{"ref":"auth-update"}}}'
```

## Architecture

| Layer | Responsibility |
| --- | --- |
| Connector registry | Declares capabilities, event types, safe actions, reliability |
| Normalized models | `Signal`, `Mission`, `Hypothesis`, `Evidence`, `ActionResult` |
| StateStore | Shared SQLite mission state, traces, memory, and analytics source |
| Runtime kernel | Correlation, scheduling, graph execution, retries, persistence |
| Operator suite | Evaluation, planning, investigation, verification, replanning, synthesis |
| Policy engine | Blocks unsafe, high-risk, or low-confidence side effects |
| Trace sink | Local traces and optional Omium SDK forwarding when available |

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

## Omium

Set `OMIUM_API_KEY` in `.env` to enable optional Omium SDK forwarding. AUTOPILOT
still records every event locally with causal step IDs, and the trace sink
attempts to call a loaded Omium SDK through common trace/event methods.
