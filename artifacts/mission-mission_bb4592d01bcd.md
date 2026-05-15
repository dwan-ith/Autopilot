# AUTOPILOT Mission Brief

Generated: 2026-05-15T10:11:13.750070+00:00
Mission: export service: Enterprise customer reports failed exports after today's rollout.
Severity: high
Confidence: 0.96
Replans: 0

## Signals
- [support_webhook/support_escalation] Enterprise customer reports failed exports after today's rollout.
- [monitoring_webhook/error_spike] Export job failures increased from 1% to 38% in the last 20 minutes.
- [status_webhook/rollout_status] Experimental export pipeline was enabled for enterprise accounts earlier today.

## Leading Hypothesis
Rollout or configuration regression

The event mentions rollout/export/job failures or a sudden shift, so a recent change may be the root cause.

## Evidence
- Runbook: Export failures after rollout (knowledge, confidence 0.88): Recent export failures are commonly caused by schema flag drift. Check rollout metadata, compare job error signatures, and disable the experimental export pipeline if failures exceed 20%.
- Policy: Enterprise escalation handling (knowledge, confidence 0.94): Enterprise-impacting incidents require an internal alert, customer-safe update draft, and owner assignment within 15 minutes.
- Runbook: Error spike investigation (knowledge, confidence 0.79): Correlate error spikes with deploys, dependencies, traffic, and feature flags. Prefer rollback or flag-disable actions with validation.
- Runbook: Export failures after rollout (knowledge, confidence 0.88): Recent export failures are commonly caused by schema flag drift. Check rollout metadata, compare job error signatures, and disable the experimental export pipeline if failures exceed 20%.
- Policy: Enterprise escalation handling (knowledge, confidence 0.94): Enterprise-impacting incidents require an internal alert, customer-safe update draft, and owner assignment within 15 minutes.
- Runbook: Error spike investigation (knowledge, confidence 0.79): Correlate error spikes with deploys, dependencies, traffic, and feature flags. Prefer rollback or flag-disable actions with validation.

## Operational Recommendation
Open a remediation task to inspect the recent rollout/configuration change and prepare rollback or flag-disable steps.

## Bounded Actions
- Write durable mission report.
- Notify the operations channel or local fallback connector.
- Persist memory note for future correlation.
