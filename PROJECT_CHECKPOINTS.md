# Project checkpoints

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

