# AUTOPILOT Critical Audit

Date: 2026-05-16

## Bottom Line

AUTOPILOT is a real product prototype, not a pure prompt wrapper. It has a working autonomous runtime, multiple agent roles, async mission execution, webhook ingress, connector probes, policy-gated actions, local trace storage, and a browser-visible demo. It is not yet a 100% hackathon submission because several surfaces are demo-fragile: external tracing is not verified, OAuth connectors need end-to-end user flows, provider health must be stricter, and the UI should make operator readiness/probes first-class.

Estimated readiness:

- Core hackathon score today: 82-86%.
- Real product readiness today: 68-72%.
- Remaining vibecoded/sloppy surface: 18-25%.
- Omium bonus readiness: 2-4/10 until official dashboard traces are verified.

## What Is Real

- Multi-agent runtime: correlator, memory, planner, investigator swarm, verifier, replanner, synthesizer/executor, action publisher, validator/reflection.
- Long-running async workflow: missions continue beyond request lifetime and can be resumed/canceled.
- Webhook/event ingress: generic, Jira, PagerDuty, weather, Sentry/GitHub-style normalization paths.
- Tool use: connectors expose read/search/write/action surfaces and sub-agents call them.
- Web search/live data: Tavily and fallback public search paths; weather via Open-Meteo fallback or OpenWeather key.
- Side effects: durable artifacts, notifications/local fallback, approval-gated GitHub/Linear/Gmail/Drive/Notion/PagerDuty actions when configured.
- Local observability: SQLite traces for mission, operator, agent, tool, policy, approval, and action events.
- Browser demo: dashboard can show connected backend, missions, traces, agent swarm, tool calls, approvals, and completed runs.

## What Is Still Sloppy

1. External trace verification is incomplete.
   - Local traces are strong.
   - Omium dashboard trace coverage is not proven.
   - The product must not claim +10 bonus until official SDK traces are visible in Omium.

2. Connector readiness is uneven.
   - Weather and Tavily have good no-key fallback design.
   - GitHub search is real, but agent-generated queries need continued shaping and backoff.
   - Gmail/Drive/Notion/Linear/PagerDuty are contract-ready, but need clean user-facing auth/setup and live smoke tests.

3. Provider health is still a demo risk.
   - Bad/empty/credit-exhausted keys should be quarantined before a mission begins.
   - The runtime should expose a provider health panel and never waste 30-90 seconds on known-bad slots.

4. UI is good enough for a demo, but not yet judge-proof.
   - Operators/probes are API-visible but need a dashboard surface.
   - Certified demo run should be a single bounded flow with expected duration, connector list, and side-effect outputs.
   - The dashboard should show artifact links and trace IDs more prominently.

5. Testing is useful but not exhaustive.
   - Unit/runtime tests cover core behavior.
   - Missing: Playwright/browser regression, OAuth mocked full flows, provider-health tests, Omium SDK instrumentation test, clean-machine quickstart test.

## Connector Design Principle

A connector should be judged by declared behavior, not whether every provider requires a user key.

- No-key public connector: real read/search through public APIs or local data. Example: Open-Meteo weather fallback.
- Optional-key upgraded connector: works without a key, improves with one. Example: OpenWeather/Tavily/GitHub public search.
- Account connector: requires user OAuth/API key for private data or real side effects. Example: Gmail, Google Drive, Notion, Linear.

Weather as a no-key fallback connector is correct product design, as long as readiness reports the mode honestly.

## Plan To Reach 100%

### Phase 1: Demo Reliability

- Add provider preflight endpoint and startup health check.
- Quarantine 401/402/403 provider keys before mission dispatch.
- Add mission-level max wall-clock budget and partial-result completion.
- Add per-connector retry budgets and circuit breakers.
- Make `/api/monitoring/check` the certified scenario: inspect connected services, open a mission only on real degradation, and target completion time under 3 minutes.

### Phase 2: Connector Completion

- Add a dashboard Operators view backed by `/api/operators`.
- Add one-click probe buttons for GitHub, web search, weather, artifacts, Slack/local notification.
- Add OAuth connect buttons/status for Gmail and Google Drive.
- Add Notion/Linear/PagerDuty setup validators that fail with actionable missing fields.
- Add integration smoke tests with mocked HTTP for every connector action contract.
- Add live smoke script that runs only when real credentials are present.

### Phase 3: Trace/Omium Bonus

- Use official Omium SDK instrumentation for workflow root, agent invocation, tool call, webhook ingress, async dispatch, policy decision, action execution, and approval execution.
- Store Omium trace/run IDs on Mission.
- Display trace IDs and dashboard URL in the UI.
- Add a trace coverage test that asserts every mission graph node has a trace event.
- Run and preserve one verified end-to-end Omium dashboard trace.

### Phase 4: UX Polish

- Add a certified demo landing state inside the app, not a marketing page.
- Show live mission timeline with current phase and elapsed time.
- Show connector mode badges: no-key, fallback, authenticated, blocked, action-ready.
- Show final side-effect artifacts and approval items in the mission summary.
- Add failure explanations that are product-language, not stack traces.

### Phase 5: Submission Hardening

- Fresh-clone quickstart script.
- `make demo` or equivalent one-command start.
- Demo video script with exact clicks and expected outputs.
- Three-page writeup covering problem, architecture, autonomy, tool surface, safety, and trace proof.
- Clean repository: remove generated probe artifacts from commits, keep `.env.example`, document all dependencies.

## Definition Of 100%

AUTOPILOT is 100% for this hackathon when a judge can:

1. Clone the repo and follow README quickstart successfully.
2. Open the dashboard and see connector readiness.
3. Trigger one workflow by UI, webhook, persistent monitor, or schedule.
4. Watch at least two specialized agents hand off work.
5. See at least three real tool calls, including one live web/data connector and one durable side effect.
6. See the workflow continue asynchronously and complete without steering.
7. Inspect local traces and, for bonus, the matching Omium dashboard trace.
8. Understand from UI and writeup why the workflow is useful beyond the demo.
