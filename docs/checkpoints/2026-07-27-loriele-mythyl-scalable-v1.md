# Loriele / Mythyl scalable recovery V1 — 2026-07-27

## Signals and stable outcomes

- Follow refreshes the followers-list hierarchy at the continuation boundary.
  A bounded exact `See more` / `Voir plus` accessibility control is expanded
  before any scroll. A local navigation stop remains `partial_resumable`; a
  positive verified-action count never promotes it to global completion.
- Unfollow retries an initially absent `Following` CTA once, after a fresh
  exact-profile identity proof. There is no coordinate fallback.
- Unfollow keeps an HMAC-only cursor (bounded anchors, depth and generation),
  restores at most ten scrolls, scans progressively while more than ten
  candidates remain, and switches to exact verified profile search for the
  final ten or after bounded progressive exhaustion.
- Missing or renamed exact accounts are excluded from the remaining plan only
  when a committed Search result surface proves absence. Ambiguous or unsafe
  surfaces remain partial and resumable.

## Recovery and terminalization

- `candidates_exhausted` and `quota_reached` are valid terminal phase states.
- Safe Unfollow checkpoints cross the Auto Restart claim boundary. The default
  cooldown is ten minutes; policy/window/lease checks remain authoritative.
- Business persistence remains synchronous. Non-critical action-log and
  Like/Mute/source projections are durably spooled to a local SQLite outbox
  before run terminalization, then replayed by the dispatcher with bounded
  claims and retries. This prevents a remote projection delay from keeping a
  completed session in `running`.

## Safety

- Exact account and exact profile identity are required before Unfollow.
- No ADB action, phone gesture, manual tick or synthetic run was used for this
  checkpoint. Runtime certification of the new UI branches is reserved for
  future natural runs.
