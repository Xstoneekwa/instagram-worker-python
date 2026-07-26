# Release Registry

## PACKAGE_RUNTIME_CONTRACT_V1_2026-07-26

Parent Worker runtime: `ada6af15f4cd3506cdd48e9e79aa3a28d4c31ec6`.
This release adds the fail-closed Supabase package/runtime preflight, strict
production follow-source settings and a bounded recheck for known Android login
system overlays. Unknown or persistent foreground mismatches still safe-stop;
Identity Guard is unchanged. The final SHA, immutable release name and active
dispatcher PID are rollout evidence. No physical run belongs to this entry.

Architecture and rollback:
[Package runtime contract V1](./checkpoints/2026-07-26-package-runtime-contract-v1.md).

## UNFOLLOW_HANDOFF_BUDGET_V1_2026-07-25

Parent Worker code: `403036a7f27dfbaa81103669266444a63ee0f200`.
This release propagates the real business-action deadline, calculates one
capacity plan at Unfollow entry, retains only the periodic monotonic deadline
guard, re-evaluates the optional Outreach handoff, and repairs request/run
terminal ordering. The immutable release name and final SHA are rollout
evidence; no physical run belongs to this entry.

Architecture, tests and rollback:
[Unfollow handoff budget V1](./checkpoints/2026-07-25-unfollow-handoff-budget-v1.md).

## T10_FOLLOW_SCROLL_7_PLUS_1_V2_RUNTIME_2026-07-25

Parent Worker runtime: `d0910d79538fd23cc1d53f0f00367194a8dded6b`.
The scoped release changes only legacy Follow continuation geometry and its
telemetry: seven fully visible new rows plus one verified overlap is preferred,
while 6+2, 5+3, the 0.24-height fallback and zero-overlap refusal remain
authoritative safety paths. T-10 requires environment correction but no code
change. Target Followers V2 remains Mythyl-only shadow, enforce false, and its
natural observation failed open after lease expiry.

The final commit, immutable release, active symlink, backend deployment and
official BotApp package are rollout evidence. See
[the scoped checkpoint](./checkpoints/2026-07-25-t10-follow-scroll-v2-runtime.md).

## FOLLOW_ADAPTIVE_SCROLL_STARTUP_GUARD_V1_2026-07-25

Parent checkpoint: `7d2797eb72bbc3432415bd9a3eccc907f3e053b0`. The
release containing this registry entry adds only the deployment-time,
atomically consumed Auto Restart startup-tick guard above the already validated
adaptive Follow scroll and safe CT recovery patch.

Immutable release name:
`follow-adaptive-scroll-startup-guard-v1-20260725`. Normal Auto Restart remains
enabled; the token skips only the first embedded tick of the one controlled
dispatcher restart. No physical run or phone validation belongs to this
checkpoint. The full final commit, active symlink and dispatcher PID are rollout
evidence and must be recorded in the operator report.

Runbook and rollback: [Run Control production](./run-control-production.md#deployment-only-startup-tick-guard).

## TARGET_FOLLOWERS_PROGRESSIVE_RESUME_V2_2026-07-24

Baseline: `d60573601174d4983b66582ada2cc079da934a46` on the Golden production
lineage. Historical source commits `ca3e1aa` and `7fdf2b4` were audited; their
core blobs were already present through `6e4b6da` and `a64368c`. This checkpoint
adds exact shadow observability, bounded fail-open RPC transport, migration
certification and the production runbook.

Activation order is immutable release with V2 off, then Mythyl-only shadow in
the canonical dispatcher environment. Enforce stays false. The final commit,
release directory, active symlink and dispatcher PID belong to the rollout
evidence; no natural or physical run is part of this registry entry.

Architecture and rollback: [Target Followers Progressive Resume V2](./target-followers-progressive-resume-v2.md).

## T10_PREFLIGHT_AND_FOLLOW_SOURCE_DEFAULTS_2026-07-24

Worker baseline is `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`; the final immutable release
and backend deployment SHA are recorded at activation. See the [addendum](./checkpoints/2026-07-24-t10-preflight-follow-source-defaults.md).

## JULY_16_PRODUCTION_BASELINE

| Repository | Business reference | Final documentation reference |
|---|---|---|
| Worker | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` | `checkpoint-worker-july16-production-baseline` |
| Backend | `6f0f3b028379e029f09f66086372bf39798fe193` | `checkpoint-backend-july16-production-baseline` |
| BotApp | `fa427ace11b294d3765f4a6e0dda2522770ed70e` | `checkpoint-botapp-july16-production-baseline` |

Human name: **2026-07-16 Production Baseline - Warmup, Unfollow, Welcome,
Multi-device, Live Counters, Follow Caps, Like Evidence Reuse**.

Purpose: immutable rollback baseline before performance optimization of
Pre-Follow, Post-Mute to post open and CT stable to next candidate.

Detailed provenance: [cross-repository checkpoint](./checkpoints/2026-07-16-production-baseline-cross-repo.md).

## FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1 — prepared 2026-07-23

| Repository | Code reference | Documentation reference |
|---|---|---|
| Worker | `51278eadf1613f80349a7930e17420c8d8dd1e64` | `8a5e9726e3dc3bdedaad6a8090ebc3a2ce83d88d` |
| Backend | `108f7defc17c2bce328801f4c272f82fb1f62706` | `bd37a1eea82cc44c0cf50f02c4f6fdedde80c579` |
| BotApp | `5f5b6a8ba97ce6af73482fc05767a3c4efb81f50` | `9bc269740e7d036bee852351f3ccedfe3618e7eb` |

Worker branch: `feature/follow-warmup-active-days-v1-20260723`. Backend and
BotApp final consolidated heads: `feature/targets-ui-parity-v1-20260723`;
these preserve the validated Warmup commits and add the Targets UI parity
checkpoint.
Activation requires documentation, additive migration verification, backend
deployment, immutable Worker release and official BotApp package validation.
