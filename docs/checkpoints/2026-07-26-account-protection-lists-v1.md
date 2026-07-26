# Account protection lists V1 — 2026-07-26

Status: active. The final cross-task collision check found no Worker overlap, and confirmed this lineage preserves `403036a`, `ada6af1` and `8eec60f`. Backend migration/deployment completed first. Worker code SHA `45d3cd3e4ca1647299ed28ef324e2cc96562d046` is active from immutable release `/Users/admin/phonefarm-worker-releases/45d3cd3-account-protection-lists-v1`; one-shot startup skip plus one dispatcher restart yielded dispatcher PID `72009`, consumer PID `72061`, and zero active request, run or device lock after rollout.

Scope: one canonical account-scoped snapshot, fail-closed pre-device loading, immutable per request, and guards for Follow, Like, Welcome/Outreach enqueue and send, plus both Unfollow modes. Comment and Story Watch have no active send implementation; their future entry points must call the same interaction guard.

Explicit exclusions: no run, ADB, phone action, login, Stripe action, cap change, legacy backfill, or Rex cleanup.

Targeted evidence: snapshot normalization/account mismatch/malformed/failure tests; exactly-one-RPC dispatcher test; overlap semantics; visible Unfollow routing regression; Python compilation. Full release evidence must include the final SHA, immutable release directory, active symlink, dispatcher PID, zero business subprocess started by rollout, and queue/lock state.

Runtime and rollback: [Account protection lists runtime](../account-protection-lists-runtime.md).
