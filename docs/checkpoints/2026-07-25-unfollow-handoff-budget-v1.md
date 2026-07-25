# Unfollow handoff budget V1 — 2026-07-25

## Scope and lineage

Parent Worker: `403036a7f27dfbaa81103669266444a63ee0f200` on the active
Follow-scroll lineage. This checkpoint is Worker-only. It does not change
Follow gestures, quotas, schedules, CT selection, package bindings, Auto Login,
Auto Restart policy, Incidents, T-10 or Target Followers Resume V2. Mythyl V2
remains shadow-only with enforce disabled.

## Corrected contracts

- Scheduler/request metadata resolves one timezone-aware
  `BUSINESS_ACTION_DEADLINE` and passes it through consumer, subprocess env,
  runner and account-session orchestration.
- Follow-to-Unfollow and direct-Unfollow entry use the same one-time budget.
- The six-hour fallback is allowed only when no real deadline exists and is
  explicitly identified in telemetry.
- Capacity is the minimum of remaining quota, eligible candidates and the
  conservative remaining-time capacity after cleanup, recovery, optional
  Outreach and initial navigation margins.
- The first useful viewport/scroll is not blocked merely because every planned
  action cannot be guaranteed in advance.
- During Unfollow, a monotonic guard runs every five minutes or 25 verified
  actions and at the phase boundary. It performs no DB lookup or plan rebuild.
- Optional Outreach receives a complete new deadline decision; insufficient
  time produces a clean explicit skip. No-Outreach sessions go to cleanup.
- Real runner sessions publish terminal status after cleanup, while direct
  status-helper calls retain the established Follow deferred-persistence
  contract. The consumer terminalizes the request after the subprocess exits
  and the lease is released, preventing a completed run with a ghost `running`
  request.

## Mythyl fixture and tests

The exact regression fixture retains 199 eligible candidates and a quota/plan
cap of 120. It records the historical 2454-second fallback failure, then uses
the real six-hour window. Expected result: 120 planned, first scroll allowed,
no artificial `ui_coverage_budget_exhausted` after the four initially visible
candidates. Tests also cover real/fallback/near/expired deadlines, reserves,
direct Unfollow entry, Outreach allowed/skipped/absent, periodic guarding,
pagination and eligibility exclusions, persistence/counters, request success,
partial/failure terminalization, and unchanged manual-stop semantics.

All validation is device-free. No run, ADB command, phone action or Instagram
opening is authorized by this checkpoint.

## Rollback

Rollback is an immutable symlink return to the immediately previous Worker
release followed by the standard one-shot startup guard and one dispatcher
restart. Do not edit a release directory in place. A rollback does not mutate
historical requests/runs and must be preceded and followed by the zero
request/run/lock gate.

## Handover known gap

The new budget/guard is test-certified, not physically revalidated here. The
first production evidence must come from a natural run or a separate Liam GO;
no manual tick or device action is part of delivery.
