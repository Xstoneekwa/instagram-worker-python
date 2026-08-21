# Follow60 Point D — safe Unfollow handoff after partial Follow

Date: 2026-08-21  
Baseline: `e5b6a40e08b2556765cb6b6fd884e2d5296b5364`

## Scope and root cause

The authoritative `FOLLOW_TERMINATION_DECISION_V1` could already preserve a
durable Follow while declaring candidate-local post-Follow recovery pending.
The next orchestration boundary still treated that state as an account-session
restart blocker, and H3 did not independently authorize Unfollow from the full
decision envelope. Mandatory Unfollow could therefore be suppressed although
its own gates were healthy.

The first broken boundaries were `_evaluate_h3_follow_exit_code_gate` at live
Follow-to-Unfollow entry and `_restart_eligibility` at resume planning. Exit
code 53 alone is never authorization.

Historical read-only blast radius: 5 runs, 2 accounts, 129 independently
eligible Unfollow opportunities. No historical record was changed.

## Canonical contract

Point D requires the complete Point A decision envelope: `partial_resumable`,
physical Follow preserved, canonical receipt present, candidate-local failure,
post-Follow recovery required, no new Follow until recovery, safe boundary,
and `handoff_to_unfollow`. Invalid or incomplete envelopes fail closed and P0C
reconciliation remains first.

Unfollow entry remains independently gated by existing structured account and
platform safety, current blocking incident, persistence, lease/device/runtime
ownership, global stop, package/settings enablement, executable candidates,
existing quota/cap, and scheduler `business_action_deadline`. The deadline
decision reuses the existing historical action P90; no fixed phase duration or
new RPC is introduced. `canonical_global_blockers` is an orchestration input,
and the existing structured `platform_state` challenge/restriction contract is
preserved. No free-text/log blob is interpreted as authorization.

After a successful Unfollow, the aggregate session remains
`partial_resumable`: post-Follow recovery is still pending. The next attempt is
recovery-first, blocks new Follow, preserves `business_session_id`, and does not
replay an Unfollow already terminal in the same session contract.

## Observable decisions

The single non-looping boundary emits:

- `follow_partial_handoff_evaluated`
- `follow_partial_handoff_to_unfollow`
- `follow_partial_handoff_blocked`

Fields include account/run/business-session identity, Follow decision version,
safe-boundary source, Unfollow enablement and executable count, deadline
remaining, and block reason.

## Frozen invariants

- Point A authoritative runner/orchestrator decision: unchanged.
- Point B structural Mute matcher: unchanged.
- Point C fresh-Mute-before-new-Like ordering: unchanged.
- Unfollow engine, S1 policy, cursor, search and candidate lifecycle: unchanged.
- CT Resume/checkpoint/reacquisition/scroll/fast-forward files changed: 0.
- New device RPC/XML/screenshot/Vision/sleep/network RPC: 0/0/0/0/0/0.
- Account-specific logic: none.

## Verification

Deterministic fixture covers cumulative Follow counts 1, 2, 10, 30, 80 and
120 with 52 executable Unfollow candidates. Negative cases cover missing
canonical receipt, challenge, restriction, account mismatch, persistence,
P0C ambiguity, lease, device, runtime, global stop, blocking incident,
deadline, disabled Unfollow and zero candidates.

Targeted Point D plus Point A/B/C and resume regressions: 58 tests PASS.
The expanded terminal, business-session, P0C and Unfollow orchestration matrix:
96 tests PASS. A repository-wide discovery run was intentionally not used as
promotion evidence because unrelated simulated network-failure fixtures include
long real-time waits; it was stopped without changing source or runtime.
The final promotion matrix, compile, diff-check, Follow60 lock verification and
candidate hashes are recorded at the Approval 2 freeze; no runtime activation
is authorized by Approval 1.
