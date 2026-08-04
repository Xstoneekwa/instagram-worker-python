# Follow60 Mainline V1

`FOLLOW60_MAINLINE_V1` is the default production Follow engine for every
request-linked Supabase account session. It uses the same verified Follow,
Mute, PostGrid, Story/Highlight V5, Like, Return CT, outbox and completed-cycle
ledger contracts that were field-tested by the Follow60 canary.

## Runtime contract

- Normal runs use `binding_kind=mainline`, bind to their exact `run_id`, and do
  not require a canary control.
- Normal runs have no ten-cycle canary barrier. Their package, daily quota,
  schedule, stop request and other canonical guards remain authoritative.
- The optional canary harness remains available only through an explicitly
  active, account-scoped, first-attempt control. It keeps its historical
  `binding_kind=canary`, baseline and evaluation barrier semantics.
- Inactive or historical canary controls never change a normal run.
- Local, offline and unlinked command-line sessions keep their historical
  behavior and cannot silently obtain production mainline authority.

## Safety invariants

This promotion does not change `instagram_navigation.py`. PostGrid positive
proof, bounded reveal, absolute top-left selection, fresh tap proof, positive
Post viewer identity, Story/Highlight V5, exact Like verification and Return CT
proof remain mandatory. Golden85 is not an alternate normal engine: it is a
documented emergency rollback only.

## Persistence

Normal runs call `persist_follow60_post_follow_v3` and
`ack_follow60_completed_cycle_v2`. Both require exact account, request, run,
candidate, binding and durable-stage continuity. The canary branch delegates to
the existing V2/V1 RPCs without semantic change. RPC execution is restricted to
`service_role`; idempotency keys and projection rules are preserved.

## Existing and future accounts

The engine choice is global Worker behavior, not an account UUID or username
allowlist. Therefore every existing account and every future account created
through the normal package/runtime flow inherits Follow60 Mainline. Account
package, cap, schedule, protection, warmup and incident policy remain data-
driven and independent.

## Change control

Critical runtime files are protected by `FOLLOW60_MAINLINE_LOCK_V1`. A future
functional edit requires an explicit, short-lived, one-shot approval record
bound to the base SHA, scope and diff hash. Documentation-only edits are
allowed. CI and the local pre-commit hook run the same verifier.

Rollback is described in `docs/follow60-mainline-rollback.md`. The rollback
script defaults to dry-run and never triggers a run, tick or device action.

