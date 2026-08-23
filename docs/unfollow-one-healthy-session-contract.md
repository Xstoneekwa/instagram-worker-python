# Unfollow one healthy session contract

`UNFOLLOW_DAILY_PLAN_V1` is the immutable authority for the phase.  When the
phase remains safe and inside its quota/deadline, reaching the end of the own
Following list transfers unresolved plan members to exact Search in the same
session; it is not a normal terminal boundary.

## Recovery domains

- Candidate recovery: one bounded same-session retry after the current Search
  generation.  Exhausting this local budget defers only that candidate and the
  session continues with the rest of the plan.
- Session safety circuit: three consecutive candidate failures, or failure to
  restore a proven safe state, stops the phase.  A verified and persisted
  Unfollow resets the consecutive counter immediately.
- Search surface circuit: its existing three-consecutive-technical-failure
  threshold remains fail-closed.  A healthy exact result or terminal confirmed
  no-result resets that Search streak.

An ambiguous post-tap outcome is never replayed in the same session.  It keeps
the existing durable hold and strict recovery contract.  When the ambiguity
has been durably journaled, the exact owner Following list has been restored,
the package/activity/account identity remains proved, and no unsafe marker is
present, its recovery class is `SAFE_CANDIDATE_LOCAL_AMBIGUITY`: no success
receipt or quota delta is created and the session proceeds to the next
candidate.  Failure of any one of those safety proofs remains a global
fail-closed boundary.  A pre-tap transient CTA/Search failure may use the
candidate-local retry because no destructive action has occurred.

## Stable reasons and telemetry

- `candidate_recovery_deferred_same_session`
- `candidate_recovery_budget_exhausted_continue_session`
- `unfollow_session_consecutive_candidate_failure_limit_reached`
- `unfollow_global_safe_state_not_restored`

Summaries expose candidate failure counts, deferred candidates, consecutive
session failures, successful counter resets, and direct Search retry
generations.  No retry is unbounded and no candidate-local exhaustion is
reported as a verified Unfollow.
