# Locked Decisions

## Target Followers Progressive Resume V2 — 2026-07-24

- V2 is shadow-only for account UUID `0d299d1e-46ee-49d2-8a84-4f928f2bb182`.
- `TARGET_FOLLOWERS_RESUME_V2_ENFORCE_ENABLED` remains false; legacy Golden
  navigation is the sole device-action authority.
- Non-allowlisted and future accounts stop before Supabase import/RPC and emit
  no V2 event or checkpoint.
- Checkpoints start at zero with no historical backfill; depth advances only on
  a verified, complete, distinct Followers viewport transition.
- Anchors are hashed and bounded to 12; raw usernames and visual artifacts are
  forbidden in checkpoint persistence.
- A normal V2 fallback is fail-open and never creates operator review or a
  Slack/Discord notification.

## Target Followers Progressive Resume V4 Enforce — 2026-08-01

- The 2026-07-24 Shadow-only authority is superseded only for UUIDs already in
  the bounded production rollout allowlist.
- Enforce may reuse a verified V3/V4 Shadow checkpoint only after exact target,
  request, attempt, release, lease, physical-depth and anchor continuity proof.
- Invalid, stale, missing or unproven checkpoints use the current viewport from
  row zero; they never skip a username and never create a device action from
  persisted depth alone.
- Non-allowlisted and future accounts remain entirely outside CT Resume.
- Rollback disables Enforce and preserves checkpoint/event history; it never
  deletes or backfills CT Resume state.

## T-10 and Follow-source defaults — 2026-07-24

- T-10 is backend-only. A backend-reread `connected` + `ready` account never receives a preflight UI request.
- `30/4` is the generic create-only Follow-source default; it is distinct from the global package/account/warmup/ops/quota cap and must not overwrite custom rows.
- The 07ee adapter calls the existing provisioning hook only after a successful published connected result and backend ready reread. Historical UI behavior remains locked.

## JULY_16_PRODUCTION_BASELINE

- Electron BotApp is not scheduler launch authority; the durable dispatcher is.
- A same-account ready preflight lease is consumable by the natural request.
- One phone owns one UI session. Concurrency is isolated by device, account, app
  instance and canonical lease.
- Suggestions rows are never Welcome jobs or Follow CT candidates.
- Welcome counters require proof of a new outbound bubble.
- `follow_limit` is canonical; `max_follow_per_run` is legacy fallback only.
- Warmup is activity based: distinct SAST dates with a persisted successful
  `follow_verified` event advance 10, 20, 40, then remove the progressive cap.
  Account age and package/service start timestamps never advance warmup.
- Account Follow caps are persistent configuration. Package defaults initialize
  them, package maxima bound them, and temporary warmup/effective limits never
  overwrite them.
- Unfollow begins at J+3, excludes protected rows and respects day/session/time
  guards.
- Story/Facebook, identity, Like verification and Return CT protections remain
  mandatory during performance work.
- Renderer code never receives direct privileged Supabase credentials.
- No settings/caps/schedules/packages are changed to manufacture a test, and no
  retry is created without explicit approval.
- Every future change to the three open performance paths requires comparison
  against `JULY_16_PRODUCTION_BASELINE`.
