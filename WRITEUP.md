# AUTOPILOT Writeup Draft

## Problem

Modern teams operate through connected systems: support tools, monitoring,
chat, docs, issue trackers, status feeds, and internal APIs. Signals arrive
continuously, but the expensive middle work is still manual: triage, correlate,
investigate, decide what can be done safely, and document the outcome.

AUTOPILOT is an Autonomous Operator Runtime that turns connected-system events
into verified, policy-bounded actions.

## Product

AUTOPILOT is connector-agnostic. A connector declares capabilities and safe
actions. The runtime reasons over those capabilities rather than hardcoding one
application workflow.

The MVP includes webhook, knowledge, artifact, and notification connectors. It
can ingest arbitrary operational events, correlate related signals, run scoped
operators, adapt the mission graph when new evidence arrives, and publish local
artifacts or notifications.

## Architecture

Core components:

- Connector registry
- Normalized object model
- SQLite persistence
- Runtime kernel
- Mission graph
- Scoped operators
- Capability router
- Policy engine
- Trace sink

The runtime persists every mission, signal, operator step, policy decision,
action, and trace event. SQLite runs with explicit connection handling and WAL
mode for better local demo concurrency.

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
