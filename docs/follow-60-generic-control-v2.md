# Follow 60 generic control V2

## Scope

This source-only release removes account selection from Worker constants. The
Follow 60 V4 engine is enabled only by a canonical, armed V2 control whose
account, active Worker SHA, account baseline and runtime request binding all
match. An environment flag is a global kill switch only; it is not an account
allowlist.

The active runtime is intentionally unchanged by this delivery. Production
activation requires a separate, explicit runtime GO after the database control
and bind RPC have been migrated and certified as account-neutral.

## Binding contract

`Follow60CanaryBindingV2` carries the control identity, canonical account ID,
optional secondary username check, expected Worker SHA, account-scoped
baseline, cycle limit and count, timestamps, runtime type/package, idempotency
identity and the request/run/attempt/business-session binding.

Activation is fail-closed when any required field is absent or inconsistent,
the control is not armed, expired, revoked, completed or colliding, the account
or baseline belongs elsewhere, the Worker SHA differs, the cycle allowance is
exhausted, or the runtime binding is incomplete. Fail-closed means the normal
Golden flow continues; it does not safe-stop a healthy non-canary account.

## Runtime isolation

- `follow_60s_canary.py` owns the validated in-process binding.
- `runner.py` reads and prevalidates the canonical control before binding it;
  fast PostGrid, stage receipts, Stop handling and the ten-cycle barrier all
  require the validated account-scoped runtime.
- `account_run_request_consumer.py` independently revalidates the bound
  account, SHA, request, run, status, collision and expiry before applying
  canary-only terminal behavior.
- At the cycle limit, the barrier is eligible only after the complete cycle and
  its stage receipts. It cannot open the next candidate.

## Production dependency

At source audit time, the production implementation of
`bind_follow_60s_canary_runtime_v2` still contained an account-specific guard.
This Worker candidate therefore remains inactive. A later authorized database
migration must make both the control row and binding RPC generic, enforce one
active control for the selected account, return all binding fields above, and
retain service-role-only execution before any account is armed.
