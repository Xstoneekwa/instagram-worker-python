# Project checkpoints

## FOLLOW60_V2_MAINLINE_V1 — official production reference

Status: source and Phase AA regression matrix certified; runtime activation is
recorded only after the controlled gate/switch/restart/soak.

- Worker behavior commit: `55a039ae14a2111c6c48261094e08f3fe00def01`
- Parent: `e2aaa6f36469bae94459b16bc6e09505b21970a8`
- Manifest: `FOLLOW60_V2_MAINLINE_MANIFEST_V1.json`
- Lock: `FOLLOW60_MAINLINE_LOCK_V2_1`
- Mainline: global default, no control, allowlist, lease or ten-cycle barrier.
- Golden: preserved only as bounded fail-closed fallback.
- V1 rollback: `45de130d29a8658ff24f222595f1d2b9184716d9`.
- Field baseline: `docs/follow60-v2-performance-baseline-v1.md`.
- Phase AA: `docs/follow60-v2-mainline-phase-aa.md`.

The older V1 checkpoint below remains immutable history.

## FOLLOW60_MAINLINE_V1 — official production reference

Status: source and DB contract certified; runtime activation recorded in the
manifest and release registry after the controlled switch.

- Worker functional commit: `5cc22fe702a1a05925aeb4c58a59e1c7306ca3aa`
- Backend/DB commit: `84fb4b60f3401b4047beba32b1b63fee33ce70c6`
- Production migration: `20260804231500_follow60_mainline_binding_v1`
- Tag: `follow60-mainline-production-2026-08-04`
- Mainline: generic, run-scoped, no canary control and no evaluation barrier.
- Canary harness: optional, account-scoped and inactive by default.
- Golden85: preserved rollback only.
- CT Resume V4: source/tests certified; second-pass field certification open.
- Reference evidence: `reference_runs/follow60-mainline-v1/`.
- Change lock: `FOLLOW60_MAINLINE_LOCK_V1`.

No physical Instagram action belongs to this checkpoint creation.
