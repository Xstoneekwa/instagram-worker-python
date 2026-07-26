# Account protection lists V1 — 2026-07-26

Status: code and tests prepared; production activation evidence is recorded only after the final cross-task collision check, backend migration/deployment, immutable release switch and one dispatcher restart.

Scope: one canonical account-scoped snapshot, fail-closed pre-device loading, immutable per request, and guards for Follow, Like, Welcome/Outreach enqueue and send, plus both Unfollow modes. Comment and Story Watch have no active send implementation; their future entry points must call the same interaction guard.

Explicit exclusions: no run, ADB, phone action, login, Stripe action, cap change, legacy backfill, or Rex cleanup.

Targeted evidence: snapshot normalization/account mismatch/malformed/failure tests; exactly-one-RPC dispatcher test; overlap semantics; visible Unfollow routing regression; Python compilation. Full release evidence must include the final SHA, immutable release directory, active symlink, dispatcher PID, zero business subprocess started by rollout, and queue/lock state.

Runtime and rollback: [Account protection lists runtime](../account-protection-lists-runtime.md).
