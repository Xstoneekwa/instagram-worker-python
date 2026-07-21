# Unfollow UI coverage expansion

## Evidence-derived budget

The offline replay uses existing Worker JSONL artifacts from 2026-05-23. The
aggregate timings contain no account or target identifiers:

- 25 completed verified Unfollow iterations: p90 `14.541 s`, rounded up to
  `15 s/action`;
- 4 completed Following-list scrolls: p90 `2.471 s`, rounded up to
  `3 s/viewport`;
- the recorded Following viewports expose 7 username rows.

For `Q` quota remaining, `E` eligible DB candidates, `S` seconds until the
scheduled session end, `U` recent unique usernames per viewport, `C` recent DB
candidates matched per viewport, `Tv` seconds per viewport and `Ta` seconds per
verified Unfollow:

```text
cleanup_reserve = 600
available_business_seconds = max(0, S - cleanup_reserve)
action_slots = min(Q, E)
observation_window = ceil(Ta / Tv)
bootstrap_candidate_yield = 1 / observation_window
username_discovery_ratio = min(1, U / 7) if U > 0 else 1
effective_candidate_yield = max(
  bootstrap_candidate_yield,
  C * username_discovery_ratio
)
required_viewports = ceil(E / effective_candidate_yield)
diagnostic_allowance = min(observation_window, max(1, E))
coverage_viewports_budget = required_viewports + diagnostic_allowance
recovery_budget_seconds = diagnostic_allowance * Ta
derived_phase_seconds = (
  action_slots * Ta
  + coverage_viewports_budget * Tv
  + recovery_budget_seconds
)
max_unfollow_phase_duration_seconds = min(
  available_business_seconds,
  derived_phase_seconds
)
max_scroll_passes_absolute = floor(
  max(0, available_business_seconds - action_slots*Ta - recovery_budget_seconds)
  / Tv
)
adaptive_scroll_budget = min(
  max_scroll_passes_absolute,
  max(0, coverage_viewports_budget - 1),
  floor(
    max(0, max_phase_seconds - action_slots*Ta - recovery_budget_seconds) / Tv
  )
)
```

`Tv` and `Ta` start from the measured p90 values (`3 s`, `15 s`). `U` and `C`
are recomputed over the recent evidence-derived observation window after every
viewport. Before the first positive candidate yield, the bootstrap is one
candidate per observation window; candidate location is therefore never
inferred from the DB count or from `7 rows/viewport`. In particular, 20 DB
candidates do not imply three viewports. Zero recent matches expands only the
adaptive estimate; no-progress, repeated-fingerprint, time and absolute-scroll
bounds still stop the loop.

The no-progress, repeated-fingerprint and recovery allowances are also bounded
by measured time: at most `ceil(15 / 3)` viewports, further capped by the number
of required viewports. No environment-selected fixed scroll count controls the
strict DB-plan path.

`business_action_deadline` is already `session_end - 10 minutes`. The runtime
adapter reconstructs the scheduled remaining duration before calling the pure
policy so that the 600-second cleanup reserve is subtracted exactly once.

## Runtime state and recovery contract

The strict path is list-native:

```text
confirmed owner Following list
-> multi-row viewport harvest
-> normalized usernames
-> intersection with the current DB plan
-> current Following state + unsafe-marker validation
-> verified and persisted Unfollow
-> bounded return/recovery to Following
-> scroll and next viewport
```

Viewport fingerprints are SHA-256 prefixes over the normalized, ordered rows
and a surface signature. Raw usernames are not embedded in the fingerprint.
The tracker owns the observed set, verified set, progress streaks, repeated
fingerprints, scroll count, recovery count and terminal fingerprint.

A navigation drift permits one bounded recovery sequence: verify/return by a
bounded Back path, then reopen the owner's Following list only if needed. An
action resumes only after `detect_own_following_list_screen` confirms the owner
Following surface again. Account mismatch, challenge/restriction text, exhausted
recovery budget, session time exhaustion and unresolved DB candidates at the UI
end all stop with explicit reasons.

## Future Auto Restart contract (not active in this checkpoint)

There is no voluntary multi-session continuation. A future Auto Restart may
resume only after a genuine recoverable involuntary stop and only when all of
these conditions are proven:

- same SAST business day;
- remaining quota recomputed from durable verified/persisted actions;
- every already verified action excluded before UI selection;
- exact account identity revalidated;
- no challenge, restriction, unsafe marker or account mismatch;
- the normal session window still has business time before the T-10 cleanup
boundary.

## Offline harness

The release-candidate harness contains 16 deterministic device-free replays:
zero candidates, first-viewport completion, dispersed candidates, normal
overlap, identical viewport, no-motion scroll, A/B/A cycle, real list end,
unresolved candidates at UI end, quota reached, time exhaustion, successful
recovery, recovery exhaustion, unsafe marker, 120 verified candidates without
duplicates, and DB-plan exhaustion before the cap. The harness never imports or
invokes ADB/uiautomator2 and reports `device_runs=0`.

The recovery request must carry the original run lineage and durable completed
action identities. It must stop rather than retry when reconciliation is
incomplete. Runtime activation of this contract is deliberately outside this
checkpoint.
