# Target Followers Progressive Resume V2

## Status and authority

V2 is a passive shadow on the current Golden Worker lineage. It is not an
enforcement engine. The legacy Followers navigation remains the only authority
for CT selection, candidate evaluation, taps, swipes, delays, safe stops and
returns. The shadow never emits a device gesture and its failures are fail-open.

The first production scope is exactly account UUID
`0d299d1e-46ee-49d2-8a84-4f928f2bb182` (`mythyl_fitness`). Every other current
or future account has no V2 controller, no V2 RPC, no V2 event and no V2
checkpoint by default. Scoping is by UUID only, never username.

## Runtime contract

The canonical runtime source must set:

```text
TARGET_FOLLOWERS_RESUME_V2_SHADOW_ENABLED=true
TARGET_FOLLOWERS_RESUME_V2_SHADOW_ACCOUNT_IDS=0d299d1e-46ee-49d2-8a84-4f928f2bb182
TARGET_FOLLOWERS_RESUME_V2_ENFORCE_ENABLED=false
```

Before canary activation, a release is certified with shadow false, an empty
allowlist and enforce false. `build_runtime_controller` checks all three gates
before importing the Supabase client or calling an RPC. Enforce true fails
closed in the production builder.

Shadow RPCs use one attempt with a short timeout. Timeout, RPC rejection or
unexpected observation disables the controller for that session while legacy
navigation continues.

## Depth, anchors and cursor

One `depth_unit` is one verified transition between distinct viewports of the
same expected CT's real Followers list. Both fingerprints must exist and
differ. The surface must be confirmed, recoverable and unambiguous. A sent
swipe, unchanged viewport, popup, Suggestions surface, wrong CT or incomplete
viewport never advances depth. Depth is bounded to `0..80`.

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

## Observability and incidents

The V2 stream uses `target_followers_checkpoint_loaded`, `resume_plan_built`,
`fast_forward_started`, `anchor_found`, `anchor_not_found`,
`checkpoint_claimed`, `checkpoint_committed`, `checkpoint_conflict`,
`checkpoint_invalidated`, `end_reached` and `resume_fallback_legacy`. Payloads
contain UUID account/run context, a hashed target ID, stable reasons, depth,
version and RPC duration; they contain no raw anchor source.

These events are distinct from legacy `follow_target_checkpoint_*`. A normal
fallback does not block a campaign, create operator review, or notify
Slack/Discord. Only a repeated structured technical failure may enter the
existing deduplicated incident lifecycle; Slack and Discord remain independent.

## Runbook

1. Confirm Worker lineage, immutable release and canonical dispatcher root.
2. Confirm the production migration registry, tables, RLS, RPC grants and zero
   initial checkpoints.
3. Certify a release with all V2 flags off; restart once; verify an empty queue,
   zero active request/run/lock and zero implicit run.
4. Set the canonical runtime source to Mythyl UUID only and enforce false;
   restart once if required and verify the loaded redacted environment.
5. Do not trigger a run. Await Liam's GO for observation of a natural Mythyl
   run.

## Rollback

Disable shadow and clear the allowlist in the canonical runtime source, then
perform one controlled dispatcher restart. This stops all future V2 RPCs while
leaving legacy navigation and stored audit rows untouched. Do not drop tables,
delete checkpoints or backfill history during operational rollback. Enforce
remains disabled.

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
