# Follow60 Ordering V2 Behavioral Canary V1

## Scope

This source-only path is disabled by default and may select `POST_FIRST_V2`
only when all of the following are true:

- the behavioral flag is enabled;
- the allowlist is exactly the Rex account UUID;
- a live control binds the exact account, worker SHA, run, request, business
  session, attempt and one-shot lease;
- the per-run V2 barrier is below 10 completed cycles;
- the existing pre-Follow mono-XML proves the exact candidate, a public profile,
  a positive Posts count, an explicitly selected Posts/Grid tab, and one unique
  fully visible absolute top-left cell (`DIRECT_GRID_SAFE`).

Every missing, invalid, expired or non-Rex binding routes to Follow60 V1. No
current or future account inherits V2.

## Behavioral order

`profile_certified -> post_opened -> V5 -> like_verified|like_skipped ->`
`profile_reentry_verified -> follow_pending -> follow_verified|follow_failed ->`
`mute_posts_verified -> mute_stories_verified -> return_ct_exact -> cycle_complete`

The router and orchestrator reuse the existing PostGrid parser, PostOpenIntent,
viewer/V5, LikeTapContext, Follow engine, Mute engine, Return CT and composite
Post-Follow persistence. V1 remains the production/default path.

## Proof and tap boundaries

`StableCandidateProofV2` transports immutable identity, business verdicts,
positive Posts/Grid evidence, binding and top-left provenance through the Post
and viewer stages. It reuses the already acquired mono-XML and performs no UI
acquisition.

`DeferredFollowIntentV2` records only intent and reservation. It explicitly
invalidates every pre-Post CTA bound and can never authorize a Follow tap.

After Back, Level 1 reentry reads only package/activity, exact action-bar title,
challenge selectors, the official exact Follow control and the current UI
generation. It performs no hierarchy dump, screenshot, filter, eligibility,
Posts-count or PostGrid analysis. The Follow engine accepts the resulting
volatile bounds for at most two seconds and only while package/activity and UI
generation remain unchanged. Any mismatch fails closed; it never falls back to
the V1 selector after a Post action.

## Post-first safety

The V2 happy path creates exactly one PostOpenIntent from the existing
`DIRECT_GRID_SAFE` evidence. It adds no reveal, Golden attempt, XML, screenshot,
PostGrid reclassification or measurement tap. Viewer confirmation and V5 remain
mandatory before Like. If the intent is rejected before any Post action, the
candidate may return to V1. Once a Post action may have occurred, the candidate
is fail-closed and cannot restart the V1 Post/Like path.

## Durable receipts and recovery

The local SQLite WAL ledger uses `synchronous=FULL`, deterministic stage keys,
payload-conflict detection and file mode `0600`. A verified Like is durable
before Follow and is never replayable. `like_verified` is not cycle completion.
Follow failure, partial Mute, missing exact Return CT, missing composite
persistence acknowledgement or Stop all leave the cycle incomplete.

Stop records a separate receipt without inventing any physical acknowledgement.
The replay plan exposes the exact next stage. Safe replay is automatic only when
the next action is unambiguous; ambiguous post-open or Follow-pending boundaries
remain fail-closed for explicit recovery. A terminal Like receipt is reused and
never physically repeated.

The test-only PostgreSQL fixture mirrors the stage and idempotency constraints.
It is not a production migration.

## Instrumentation

Existing phase events remain authoritative. V2 adds route selection, Post/V5/
Like/Back duration, Level 1 reentry duration, cycle completion and barrier
events. The instrumentation itself performs no screenshot, XML acquisition or
tap. Supabase outbox cost is reported only when observed by the existing outbox
instrumentation; it is never synthesized.

## Activation and rollback

This delivery creates an inactive immutable release only. It authorizes no
runtime switch, restart, production DB migration, deployment, run, tick, ADB or
phone action.

Any future runtime activation requires a new explicit, SHA-scoped GO and an
exact Rex-only control. The rollback release remains:

`/Users/admin/phonefarm-worker-releases/45de130-follow60-see-more-surface-v1`

