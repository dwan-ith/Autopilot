# AUTOPILOT Writeup Draft

## Problem

Teams operate across many connected systems. Signals arrive through support tools, monitoring tools, docs, chat, status pages, and internal APIs. Humans still perform the expensive middle step: deciding whether an event matters, correlating it with other evidence, investigating likely causes, choosing bounded actions, and documenting the outcome.

AUTOPILOT is an Autonomous Operator Runtime that turns connected-system events into verified actions.

## Architecture

The system is connector-agnostic. Connectors declare capabilities such as read, search, write, notify, and action. Events are normalized into Signals. The runtime correlates signals into Missions, then creates a mission graph of operator steps.

Core components:

- Connector registry
- Normalized object model
- SQLite persistence
- Runtime kernel
- Dynamic scoped operators
- Capability router
- Policy-bounded action layer
- Local/Omium-ready trace sink

## Autonomous Behavior

AUTOPILOT demonstrates autonomy through:

- Event-driven mission creation
- Correlation of multiple asynchronous signals
- Dynamic hypothesis generation
- Parallel evidence collection through capability-routed connectors
- Verification confidence scoring
- Adaptive replanning when confidence is below threshold
- Bounded action publishing without human steering
- Persistent mission memory and trace logs

## Demo

The demo fires an enterprise support escalation, followed by monitoring and rollout-status events. AUTOPILOT correlates the events into one mission, investigates likely hypotheses, replans if confidence is insufficient, writes an action brief, and sends an operations notification through Slack or local fallback.

## Safety

AUTOPILOT is policy-bounded. Connectors advertise safe actions, and the MVP only allows local artifact writes and notification actions. Riskier actions can be added later behind validation and permission policies.
