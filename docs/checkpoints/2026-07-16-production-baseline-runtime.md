# 2026-07-16 Production Baseline - Worker Runtime

Short name: `JULY_16_PRODUCTION_BASELINE`

Human name: **2026-07-16 Production Baseline - Warmup, Unfollow, Welcome,
Multi-device, Live Counters, Follow Caps, Like Evidence Reuse**

This is the functional rollback point and comparison baseline before additional
performance work. The reference business commit is
`5a59ef3677c59b5e65e6b69462f3756fd96cda78`; the immutable production release
is `/Users/admin/phonefarm-worker-releases/5a59ef3-follow-caps-like-evidence`
and `/Users/admin/phonefarm-worker-current` resolves to it.

See also [the cross-repository summary](./2026-07-16-production-baseline-cross-repo.md).
The backend and BotApp repositories contain checkpoint documents with the same
date and scope.

## Runtime invariants

- Natural scheduling is independent of Electron BotApp. Launch authority is the
  fresh heartbeat of the durable launchd dispatcher.
- An eligible ready preflight for the same account is consumable by the request;
  it is not rejected as a foreign lease.
- Request creation is allowed on the first eligible tick when dispatcher and
  device heartbeats are fresh and no conflicting request, run, lock or lease is
  active.
- The dispatcher runs a bounded multi-device pool. Device, account, app instance
  and UI lease scopes remain isolated. One phone owns one UI session; distinct
  phones may execute independently.

## Welcome DM invariants

- The followers scan uses at most three soft downward gestures.
- Instagram Suggestions rows, `Follow` buttons and `See all suggestions` are a
  boundary, never Welcome jobs and never Follow CT candidates.
- Boundary recovery is bounded to at most five upward observations, stops on the
  first usable true-followers surface and preserves already collected jobs.
- A true followers row with a valid `Message`, `Follow back` or equivalent CTA
  is sufficient when the Followers tab and hybrid surface proof agree.
- A job becomes sent, its follower becomes welcomed and counters increment only
  after strong proof of a new outbound bubble. Composer clearing is not proof.
- The sender finishes normally when no jobs exist or all jobs are exhausted,
  restores the followers surface when needed, and only then hands off to the
  separate Follow CT flow.
- Complete recent physical proof of recovery, outbound bubble and handoff is
  still pending.

## Golden Follow, Mute, Like and Return CT

- The Golden pre-Follow context and strong-profile proof plumbing is restored.
- Mute passes `confirmed_sheet_level="mute_toggles"` and uses the level-aware
  dismiss path.
- Like uses the Golden Tier-1 direct no-post check and reuses existing strong
  evidence where safe.
- Story/Facebook post-open guards, identity protection, real Like verification,
  safe-stop behavior and Return CT protections remain mandatory.
- `follow_limit` is the canonical account production limit;
  `max_follow_per_run` is legacy fallback only. Mythyl resolves to `120/day` and
  `20/session` under current settings.

## Warmup and Unfollow

- Historical baseline rule at this checkpoint was calendar based: Day 1 = 10,
  Day 2 = 20, Day 3 = 40 and Day 4+ =
  package maximum. Effective limits are the minimum of package, warmup and
  account day/session limits.
- Tracker and Mythyl activation dates are backfilled; future activated accounts
  initialize from package/service start and progress without manual intervention.

> Superseded for current policy on 2026-07-23 by
> [Follow Warmup Active SAST Days V1](2026-07-23-follow-warmup-active-sast-days-v1.md).
- Unfollow eligibility begins at J+3. Protected rows are excluded, production
  day/session caps apply, the hidden one-action safety cap is removed from the
  effective production path, and insufficient remaining time produces a clean
  skip.
- Physical Mythyl run `00b90fc4-1313-4a1c-b8c9-38c205260be3` completed 10
  follows and 10 verified likes. Eleven canonical interaction rows were updated
  with `unfollow_result=success` during the run window.

## Counters and asynchronous evidence

- Verified Follow and Like evidence is emitted asynchronously for Profiles live
  projection; no synchronous network call is added between Like and Return CT.
- Settings counters and stale renderer fallbacks are excluded from business
  totals.
- Deferred noncritical persistence may continue after UI return, but critical
  action proof must be durable before a run is completed.

## Incidents, notifications and security

- Structured failures retain their precise English reason and phase. Human
  intervention uses the generic exactly linked `operator_review_required`
  workflow and `Mark reviewed` transition.
- Slack and Discord share the canonical hidden `Open Incidents/Actions` CTA,
  delivery records, `delivered_at` and idempotence.
- The renderer never receives direct Supabase/service-role access. Privileged
  operations use the authenticated relay and backend boundary.
- No cap, setting, schedule or package is changed to manufacture a test. No
  retry occurs without explicit approval.

## PHYSICALLY VALIDATED IN PRODUCTION

- Natural scheduler request creation at the first eligible tick.
- Golden Follow, Mute, Like verification and Return CT on physical devices.
- Mythyl: 10 follows, 10 likes and 11 successful J+3 unfollows in the July 16
  baseline run.
- Follow live and Like live projection during the physical Mythyl run.
- Warmup completed and limiting-source projection.
- Human `Mark reviewed`, English Slack/Discord deliveries and BotApp active/idle
  transitions.

## TEST-VALIDATED ONLY

- Scheduler launch with Electron BotApp closed while durable runtime is healthy.
- Same-account preflight lease consumption and bounded multi-device dispatch.
- Welcome Suggestions boundary classification, three-down/five-up bounded
  recovery, job preservation and safe exhaustion.
- Follow limit priority and legacy fallback behavior.
- Like evidence reuse without weakening Story/Facebook, identity or Return CT.

## PENDING PHYSICAL OBSERVATION

- Tracker completing the latest five-up Welcome boundary recovery.
- A recent outbound Welcome bubble verified under the current release.
- Welcome-to-Follow handoff after the latest Welcome patches.
- Two phones executing business runs simultaneously.
- Natural scheduler launch while BotApp is fully closed.
- Mythyl completing 20 follows under the new Follow resolver.
- Physical latency gain from Like evidence reuse.

## KNOWN RISKS / LIMITS

- Historical runner heartbeat rows can be stale and can retain obsolete run
  metadata; process and active-release evidence remains authoritative.
- Dispatcher heartbeat `git_sha` can be unknown; resolve provenance from the
  immutable active root as well.
- Historical dirty/noncanonical clone worktrees may remain outside the active
  release and must not be promoted.
- BotApp capture may be limited by ScreenCaptureKit or ambiguous bundle identity;
  this does not replace runtime evidence.

## OPEN PERFORMANCE WORK AFTER JULY_16_PRODUCTION_BASELINE

Physical reference: Mythyl run `00b90fc4-1313-4a1c-b8c9-38c205260be3`, started
26.6 seconds after its 16:00 UTC window and completed in 27 minutes 12 seconds.
The observed worker was commit `9838e1d`, an ancestor of the checkpoint business
commit `5a59ef3`; the later commit changes limit/evidence plumbing but has not yet
physically proven the expected gains.

### 1. Pre-Follow

- Current metric: 10 cycles, average 31.06 s, median 30.86 s, range
  30.04-32.92 s; total 310.57 s.
- Golden reference: restored context/proof plumbing is present, but no claim of
  Golden-equivalent physical timing is made.
- Unproved hypotheses: redundant pending-request, profile-proof and screen-guard
  observations may repeat already strong evidence.
- Required method: line-by-line Golden/current comparison before any patch.
- Non-regression: exact identity, private/follow-state checks, social memory and
  real Follow verification remain intact.

### 2. Post-Mute to post open

- Current metrics: post-Mute surface gap average 3.57 s; subsequent
  surface-to-post-open path average 14.49 s, for about 18.06 s per cycle before
  post interaction.
- Golden reference: level-aware Mute dismiss and Tier-1 direct no-post check are
  restored.
- Unproved hypotheses: a closed-sheet proof is sometimes rejected, followed by
  repeated profile/grid observations before legacy-safe post open.
- Required method: line-by-line Golden/current comparison; do not remove
  Story/Facebook checks or real Like verification.
- Non-regression: Mute axes, sheet closure, post identity and safe-stop remain
  mandatory.

### 3. CT stable to next candidate

- Current metric: 9 transitions, average 14.40 s, median 12.85 s, range
  12.26-22.96 s; critical persistence alone averaged 9.02 s per cycle.
- Golden reference: compact Return CT and explicit CT proof reuse are active.
- Unproved hypotheses: serial critical persistence and picker refresh account for
  most post-return delay.
- Required method: line-by-line comparison and proof that any deferral remains
  durable before terminal completion.
- Non-regression: CT stability, action accounting, anti-duplicate state and
  Return CT safety remain intact.

`Any commit after this checkpoint touching these paths must be compared against JULY_16_PRODUCTION_BASELINE.`
