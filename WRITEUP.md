# AUTOPILOT — Submission Writeup

## 1. Problem

Every engineering team operates across multiple connected systems — support tools, monitoring dashboards, deployment pipelines, status pages, internal APIs. When something breaks, signals arrive from several directions simultaneously: a customer ticket, an error spike alert, a deploy notification. Humans still perform the expensive middle step: reading all the signals, correlating them, deciding what matters, investigating likely causes, choosing a safe action, and documenting the outcome.

This middle step is slow, inconsistent, requires context that lives in runbooks nobody remembers to read, and happens at 2am when the on-call engineer is least equipped to do it well.

**AUTOPILOT** automates that middle step end-to-end. It ingests events from any connected system, correlates related signals into a unified Mission, deploys a team of specialised AI operators to investigate and reason, and executes verified actions — without a human in the loop.

---

## 2. Agent Architecture

AUTOPILOT is built around a **connector-native** model: the runtime does not know or care what system it is connected to. Connectors declare capabilities (`read`, `search`, `write`, `notify`, `action`). The runtime routes work to connectors by capability, not by name.

### Connectors (included)

| Connector | Capabilities | Role |
|---|---|---|
| `webhook` | read | Generic inbound event normalizer |
| `knowledge` | search, read | Local runbooks + Tavily live web search |
| `artifact` | write, action | Durable markdown report writer |
| `notification` | notify, action | Slack webhook or local file fallback |

### Operator Pipeline (6 distinct agents)

Each operator has its own system prompt, reasoning pattern, and output schema. They are not retry wrappers around a single prompt — each represents a distinct cognitive step.

```
Signal Evaluator   →   triage severity and blast radius
Mission Planner    →   decompose into competing hypotheses
Investigator       →   LLM-generated queries → connector search fan-out
Verification Gate  →   critically score evidence, decide to act or replan
Adaptive Replanner →   fill evidence gaps when confidence is below threshold
Synthesis Operator →   write the actionable mission brief
Action Publisher   →   execute bounded writes and notifications
```

### Coordination Flow

```
Webhook → normalize Signal → correlate with active Missions
    ↓
RuntimeKernel creates / extends Mission → schedules async task
    ↓
Signal Evaluator → Mission Planner → Investigator (parallel per hypothesis)
    ↓
Verification Gate → (replan loop if confidence < threshold)
    ↓
Synthesis Operator → Action Publisher → trace + memory persist
```

Signal correlation is entity-based: if a new signal shares entities with an active mission, it is merged into that mission rather than creating a new one. This allows the runtime to handle the realistic case where multiple alerts fire about the same incident.

---

## 3. Autonomous Behavior in Practice

### What makes it actually autonomous

- **No human approval gates.** Once a signal arrives, the full pipeline runs to completion independently.
- **Crash-safe.** The Runtime Kernel uses SQLite to persist every mission and step. On server restart, `resume_active()` re-schedules any incomplete missions.
- **Adaptive.** The Verification Gate asks the Replanner to spawn new investigation branches when evidence confidence is below threshold. The system retries different angles, not the same one.
- **Idempotent.** Each mission has a stable ID. Duplicate webhook fires are de-duped by entity correlation rather than creating redundant missions.

### Tooling and integrations with real side effects

- **Web search**: The Knowledge connector calls Tavily's search API for live results when configured, falling back to local runbook matching.
- **Artifact writes**: The Artifact connector writes durable Markdown reports to the `artifacts/` directory — verifiable files created during the demo.
- **Notifications**: The Notification connector posts to a Slack webhook or writes a local JSON artifact — either way, a real side effect.
- **Webhook ingestion**: The `/webhooks/{connector}` endpoint receives arbitrary external events and routes them through the connector registry.

### Long-running and async

The `/demo/fire` endpoint returns immediately. The mission runs asynchronously via `asyncio.create_task`. Multiple missions can run in parallel. The dashboard streams live updates via Server-Sent Events, so the judge watches the pipeline progress in real time while the API is non-blocking.

### Tracing

Every operator step, webhook receipt, and action is emitted to the local `TraceSink` which writes to SQLite and exposes via `/api/traces`. The same sink is wired to emit to Omium when `OMIUM_API_KEY` is set, providing a causal trace graph on the Omium dashboard with parent-step linking preserved end-to-end.

---

## 4. Safety and Policy

AUTOPILOT is policy-bounded. Each connector declares `safe_actions` in its manifest. The Action Publisher only invokes actions that appear in that list. Riskier actions (e.g. triggering a rollback, opening a prod PR) can be added later behind a confidence threshold gate and explicit permission policy.

The demo deliberately uses only local write and notification actions — no external mutations to production systems.

---

## 5. Dependencies

```
fastapi, uvicorn, pydantic, python-dotenv, httpx, python-multipart
```

LLM calls use `httpx` directly against the provider's OpenAI-compatible REST endpoint — no framework SDK required. Provider auto-detection: `OPENROUTER_API_KEY` → `GROQ_API_KEY` → heuristic fallback.
