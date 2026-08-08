# Follow60 Ordering V2 behavioral canary runtime integration V1

Status: source-only, inactive. Production remains on the pre-existing Worker.

## Runtime boundary

The pre-run control stores only `control_id`, `account_id`, expected Worker SHA,
canary type, the fixed ten-cycle limit, baseline, expiry and `armed` status.
Run, request, business-session, attempt and lease identities remain null until a
manual Play request has created and linked those canonical rows.

At mainline bootstrap the service-role-only claim RPC locks the armed control,
verifies the exact account, Worker SHA, linked `account_session` run and explicit
manual Play metadata, then atomically writes the one-shot runtime binding. An
exact replay returns the same lease; another run/request/session cannot bind it.

## Routing and barrier

The behavioral path remains disabled by default. Even when enabled, the
allowlist must contain exactly one valid account UUID and the current account
must match it. No account is hard-coded in runtime source.

Counters are durable and distinct: candidate seen, V2 selected, V2 complete,
V2 partial and V1 fallback. Only an idempotent `v2_complete` event increments
the barrier counter. The tenth complete event first follows the existing
critical persistence and ledger ACK, then marks the control barrier reached,
expires its lease and returns from the followers engine before candidate 11.
V1 fallback events never consume the V2 barrier.

Operator Stop terminalizes the claimed control idempotently and preserves the
actual counters. It never synthesizes ten cycles.

## Activation boundary

This checkpoint does not authorize a migration, environment change, runtime
switch, restart, run, tick or device action. A later explicit runtime GO must
apply the migration, create one dormant pre-run control, set the shared-path
variables for one approved account, and re-certify the production gate.
