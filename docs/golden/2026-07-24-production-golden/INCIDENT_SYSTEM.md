# Incident and notification system

## Purpose

Incidents translate exact Worker/runtime failures into durable, reviewable
control-plane objects. They do not hide the original reason behind a generic UI
label and they do not automatically authorize a retry.

## Lifecycle

```text
structured failure
→ runtime incident with exact reason/phase/correlation
→ optional operator-review action
→ idempotent Slack/Discord delivery
→ human investigation
→ reviewed/resolved/superseded transition
```

Request ID, run ID, account context and phase are correlated where available.
Sensitive credentials, raw XML, screenshots and device secrets are excluded.

## Worker components

- `runtime_incidents.py` and `runtime_incident_matrix.py` classify/persist;
- `incident_notifications.py` builds redacted canonical notifications;
- `incident_notification_service.py` delivers queued notifications;
- `incident_dashboard_action_sync.py` synchronizes review actions;
- dispatcher failure/terminalization paths publish exact reasons.

Backend tables/RPCs and BotApp incident views are external dependencies.

## Operator review

`operator_review_required` is a human workflow state, not the machine failure
reason. Marking an action reviewed must not rewrite run/request history or claim
the underlying issue never happened. A later successful Auto Login may
explicitly supersede an older identity-mismatch action/incident with a recorded
reason.

No bulk incident cleanup is permitted. Every reconciliation must name exact
objects and reason.

## Notifications

- Slack and Discord share the canonical Incidents/Actions destination;
- delivery is idempotent and tracks delivery state/time;
- message content is English and redacted;
- notification failure does not mutate business outcome;
- resolving an incident may produce a resolution notification but never a new
  business run.

## Security boundary

BotApp renderer and browser clients do not receive service-role credentials.
Privileged incident reads/writes cross authenticated relay/backend boundaries.
Links use stable operator routes and avoid credentials in query parameters.

## Operational checks

- count open incidents and action-required objects;
- verify notifier process and last delivery error;
- trace exact request/run/incident/action IDs;
- confirm a review action does not create a request or run;
- inspect retries/idempotency before resending notifications.

## Recovery rule

At the first runtime error, allow canonical terminalization and lock cleanup.
Do not manually create a retry or phone action. Obtain a new, account-specific GO
after the incident is understood.
