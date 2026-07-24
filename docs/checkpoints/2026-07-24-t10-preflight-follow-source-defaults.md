# T-10 preflight and Follow-source defaults — 2026-07-24

Worker baseline: `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` (navigation `f93c501` preserved).

- The backend-only route `/api/instagram-dashboard/login-preflight/cron` is registered every five minutes. It uses the existing IANA/date calculation and never creates a business `account_session`; a `connected` + `ready` account is skipped before any phone UI request.
- The historical 07ee adapter now reuses the canonical `maybe_provision_follow_source_rotation_on_ready` hook only after the engine reports a published connected result and the backend is reread as `connected` + `ready`. No selector, logout, popup, or other 07ee UI decision changes.
- The default Follow-source contract for a missing row is `30` follows per target and `4` targets. It is create-only/idempotent: an existing custom row is not overwritten.
- Lorielebras stopped at six because its absent row selected the legacy fallback `2 × 3`; the global day-one cap remains independently `min(package, account, warmup, ops, quota)` and is not changed by the CT rotation contract.
- Mythyl requires no patch: its protected navigation, `social_memory`, and `unfollowed_completed` exclusions remain untouched. No phone run or physical validation is performed by this checkpoint.

Operational handover: deploy the backend configuration first, then the immutable Worker release; only after both are live may the missing Lorielebras row be inserted after reconfirming no active run, request, or lock. New Mac/setup needs no new phone configuration: Vercel owns the T-10 invocation and the Worker retains the existing runtime environment.
