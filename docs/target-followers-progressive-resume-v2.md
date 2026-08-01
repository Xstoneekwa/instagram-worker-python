# Target Followers Progressive Resume V2

## Status and authority

V2/V4 remains passive Shadow by default.  The explicit 2026-08-01 rollout may
enable Enforce only for UUIDs already present in the bounded Shadow allowlist.
The persisted checkpoint is never device-action authority by itself: exact CT,
lease, provenance, physical depth and fresh anchor continuity must all pass.
Any rejection evaluates the current viewport from row zero through legacy
Golden navigation and skips no username.

The production scope is the existing bounded UUID rollout list in the canonical
runtime environment. Every other current or future account has no controller,
RPC, event or checkpoint by default. Scoping is by UUID only, never username.

## Runtime contract

The canonical runtime source must set:

```text
TARGET_FOLLOWERS_RESUME_V2_SHADOW_ENABLED=true
TARGET_FOLLOWERS_RESUME_V2_SHADOW_ACCOUNT_IDS=<existing-bounded-uuid-rollout>
TARGET_FOLLOWERS_RESUME_V2_ENFORCE_ENABLED=<false-by-default-or-explicitly-approved-true>
```

`build_runtime_controller` checks the bounded allowlist before importing the
Supabase client or calling an RPC. Enforce true does not widen that allowlist.

Shadow RPCs use one attempt with a short timeout. Timeout, RPC rejection or
unexpected observation disables the controller for that session while legacy
navigation continues.

## Depth, anchors and cursor

One `depth_unit` is one verified transition between distinct viewports of the
same expected CT's real Followers list. Both fingerprints must exist and
differ, a suffix of the previous viewport must be the prefix of the next one,
and the next viewport must expose at least one new unique row. The surface must
be confirmed, recoverable and unambiguous. A sent swipe, unchanged viewport,
popup, Suggestions surface, wrong CT or incomplete viewport never advances
depth. Depth is bounded to `0..80`.

The legacy navigation remains authoritative, but its canonical scroll
diagnostics are an accepted shadow evidence source. `depth_advanced`, positive
positional overlap, positive new-row count, before/after fingerprints, the
primary-list state and the fresh exact CT identity are staged after the legacy
gesture. The next ordinary candidate collection must reproduce the after
fingerprint before the shadow depth can advance. This bridge emits no gesture
and changes no legacy navigation decision.

Anchors are SHA-256-derived opaque tokens. At most 12 are stored; raw usernames,
screenshots, XML and full Followers dumps are forbidden. The theoretical cursor
advances only through a contiguous prefix made of anchors or canonically
terminal rows. The first unknown row stops the proposal. Shadow cursor and
depth proposals are observable but never consumed by legacy navigation.

## Persistence and concurrency

The backend owns one checkpoint per `(account_id, target_id, surface)` and an
append-only event stream. Shadow and future enforce depths, anchors and
fingerprints are physically separate. All mutation is through service-role-only
RPCs protected by optimistic version, run ownership and a bounded lease.

There is no backfill. The migration creates no rows. A first real claim creates
a checkpoint at depth zero. A transition is committed only after the complete
viewport proof; crash, safe stop, stale version or lease conflict preserves the
previous committed version. Invalidation and reset are explicit CAS operations.

Every clean CT rotation, CT end or clean session terminal flushes any verified
but uncommitted depth before releasing the lease. The flush is idempotent. A
crash records `run_terminal_before_checkpoint_flush`, releases the lease and
does not commit pending depth. Other stable no-commit reasons are
`no_safe_progress`, `continuity_unproven`, `lease_invalid`,
`target_identity_changed`, `suggestions_boundary_reached` and
`commit_conflict`.

In shadow, physical observation always starts at depth zero because V2 does not
move the list. A positive checkpoint loaded by Run B is a theoretical proposal,
not a physical offset: Run B never writes a lower depth and cannot add the
loaded value to its physical scroll count. It may only commit after its newly
proven physical depth exceeds the already stored shadow depth.

## Observability and incidents

The V2 stream uses `target_followers_checkpoint_loaded`, `resume_plan_built`,
`fast_forward_started`, `anchor_found`, `anchor_not_found`,
`checkpoint_claimed`, `depth_transition_rejected`, `checkpoint_committed`,
`checkpoint_flush_completed`, `checkpoint_not_committed`,
`checkpoint_conflict`, `checkpoint_invalidated`, `end_reached` and
`resume_fallback_legacy`. Payloads contain UUID account/run context, a hashed
target ID, stable reasons, depth, version, RPC duration and, for legacy bridge
proofs, scroll index, overlap, new-row count and fingerprints; they contain no
raw anchor source.

Checkpoint events also carry the exact `source_request_id`, the canonical
request `source_attempt_id`, and the full 40-character commit of the immutable
release root executing `runner.py`. A natural first request is attempt 1. An
Auto Restart request must provide its positive attempt in the validated resume
policy; the prior run projection is never used as a fallback. The dispatcher
root and its short commit must resolve to this module's root and match the full
Git commit. Missing or conflicting provenance emits a stable
`resume_fallback_legacy` reason and disables only the V2 shadow controller;
legacy navigation remains authoritative and unchanged.

The checkpoint commit uses the distinct
`commit_target_followers_resume_checkpoint_v4` RPC. The last validated scroll
evidence is retained until that RPC atomically advances the checkpoint and
persists the same request, attempt, release, overlap, new-row count, scroll
index and before/after fingerprints on the immutable `committed` event. The
controller accepts success only when the RPC returns
`provenance_persisted=true`; a missing V4 function or incomplete response fails
open to legacy without claiming a durable V2 commit. V4 must be installed
before any Worker release containing this caller is activated.

These events are distinct from legacy `follow_target_checkpoint_*`. A normal
fallback does not block a campaign, create operator review, or notify
Slack/Discord. Only a repeated structured technical failure may enter the
existing deduplicated incident lifecycle; Slack and Discord remain independent.

## Runbook

1. Confirm Worker lineage, immutable release and canonical dispatcher root.
2. Confirm the production migration registry, tables, RLS, service-role-only
   V4 RPC grants and atomic provenance tests before activating its Worker
   caller.
3. Certify a release with all V2 flags off; restart once; verify an empty queue,
   zero active request/run/lock and zero implicit run.
4. Keep the existing bounded UUID allowlist unchanged; enable Enforce only with
   an explicit rollout GO and verify the loaded redacted environment.
5. Do not trigger a run. Await the next natural eligible run.

## Rollback

Disable Enforce in the canonical runtime source, preserve Shadow and the bounded
allowlist, then perform one controlled dispatcher restart. This returns device
authority to legacy navigation while retaining audit rows. Do not drop tables,
delete checkpoints or backfill history during operational rollback.

## Handover and known gaps

This is an intermediate production checkpoint, not the final Frontend/Stripe
handover. A natural Mythyl run on 2026-07-25 certified that the UUID gate,
checkpoint load, plan build, lease claim, redacted events and fail-open legacy
authority execute in production. Two checkpoints and two `claimed` events were
persisted at depth zero. Six bounded commit attempts conflicted with
`lease_expired`; no depth advance was persisted, and legacy Follow continued to
its natural cap. All other accounts had zero V2 checkpoint and event for the
observation. Enforce remains false and is not authorized. Follow-up work may
investigate lease duration separately, but this checkpoint does not patch V2.

Evidence: [2026-07-25 scoped checkpoint](./checkpoints/2026-07-25-t10-follow-scroll-v2-runtime.md).
