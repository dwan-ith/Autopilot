# AUTOPILOT Writeup Draft

## Problem

Modern teams operate through connected systems: support tools, monitoring,
chat, docs, issue trackers, status feeds, and internal APIs. Signals arrive
continuously, but the expensive middle work is still manual: triage, correlate,
investigate, decide what can be done safely, and document the outcome.

AUTOPILOT is an Autonomous Operator Runtime that turns connected-system events
into verified, policy-bounded actions.

## Product

AUTOPILOT is connector-agnostic. The product has a connector directory modeled
as a catalog of external services. Each service declares auth mode, scopes,
objects, event types, capabilities, and safe actions. The runtime reasons over
those capabilities rather than hardcoding one application workflow.

The MVP includes a demo-mode directory for services such as Gmail, Slack,
Google Drive, Notion, Linear, Jira, Sentry, PagerDuty, Zendesk, Web Search, and
Local Artifacts. Underneath that product layer, runtime adapters implement the
currently available local capabilities: webhook ingestion, knowledge search,
artifact writing, and notification fallback.

## Architecture

Core components:

- Connector registry
- Normalized object model
- SQLite StateStore
- Runtime kernel
- Mission graph
- Scoped operators
- Capability router
- Policy engine
- Trace sink

The runtime persists every mission, signal, operator step, policy decision,
action, memory item, and trace event through `StateStore`. SQLite runs with
explicit connection handling and WAL mode for better local demo concurrency.
This is the shared state boundary for the orchestrator, scoped agents, and
analytics reads, so context survives a crashed worker or restarted server.

## Scope Decisions

Three agent surfaces are intentionally narrowed for the hackathon build:

- Project management is Linear-first. Jira is only a config stub and returns a
  blocked result instead of carrying a second integration.
- Cloud infrastructure exposes one demoable action: trigger a deployment through
  a webhook or GitHub Actions workflow dispatch. Provider APIs, rollbacks, and
  cost APIs are outside this slot.
- Security audit runs from a defined trigger: PR open events. Its output
  contract is a durable audit artifact plus a Slack notification when configured.

## Autonomy

AUTOPILOT demonstrates autonomy through:

- webhook-driven mission creation
- asynchronous correlation of related signals
- dynamic hypothesis generation
- capability-routed evidence collection
- verification confidence scoring
- adaptive replanning on new correlated signals or weak evidence
- policy-gated side effects without human steering
- durable memory and trace logs

## Demo

The demo fires three events:

1. an enterprise support escalation
2. a monitoring error spike
3. a rollout status event

AUTOPILOT correlates them into one mission, expands the mission graph, launches
a follow-up investigation branch, verifies confidence, writes a mission brief,
and emits a notification action. The dashboard shows the graph, hypotheses,
evidence, policy decisions, actions, and traces.

## Safety

AUTOPILOT is policy-bounded. Connectors declare safe actions, and the runtime
creates an explicit policy decision before each side effect. In the MVP, only
local artifact writes and notifications are enabled. Riskier actions can be
added behind stricter confidence thresholds and validation steps.
