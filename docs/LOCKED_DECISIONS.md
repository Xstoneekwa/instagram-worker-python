# Locked Decisions

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
