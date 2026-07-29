# Target Availability Worker pilot rollout — prepared, not executed

## Certified boundary

The dormant Worker candidate is based on production Worker `8f6d16c772236450b22d167239223ca3eae30e7d`. The production-only navigation, Followers, recovery, Unfollow, dispatcher, resume, identity, reliability, heartbeat and Auto Restart changes remain inherited without modification. Availability changes are limited to a pure observation contract, a lazy runtime adapter, a bounded fail-open writer, two observation-only rotation hooks, tests and documentation.

No command below was executed during Gate 3. Every sub-gate requires its own explicit GO, an empty runtime gate, a clean immutable release, an exact checksum match and an operator-visible rollback window.

## Gate 4A — dormant release, flags OFF

1. Confirm BotApp/Scheduler coordination and certify requests, runs, locks and queue empty.
2. Confirm all four Availability flags are absent/OFF, the allowlist is absent, and no operator kill-switch configuration is required.
3. Pause the dispatcher with the canonical controller and certify zero consumer/runner process.
4. Switch `/Users/admin/phonefarm-worker-current` through `phonefarm-runtimectl switch-release <candidate-release>`; never edit the symlink manually.
5. Arm the shared startup skip through `phonefarm-runtimectl dispatcher prepare-auto-restart-startup-skip`.
6. Resume once through `phonefarm-runtimectl dispatcher resume`.
7. Verify one PID, `duplicate=false`, exact release SHA, queue/runs/requests/locks empty, no Availability module/thread/queue/transport and zero observation rows.
8. Observe natural historical scheduling only. Do not run a manual tick, ADB command or device action.

## Gate 4B — one-account memory capture

Use a synthetic preflight first, then one explicit production pilot UUID. Enable capture only, keep writer OFF, keep Shadow/policy Shadow OFF, and keep the UUID allowlist to exactly one account. Certify that observations are computed and discarded in a bounded way, no Supabase write occurs, and historical latency/gesture counts remain within the Gate 3 baseline. Stop immediately through the kill switch on any drift.

## Gate 4C — one-account writer pilot

Keep the same single account. Enable capture and writer only after 4B acceptance. Preserve Shadow/policy Shadow OFF. Cap runs and observation volume, inspect only `ct_target_availability_observations`, verify idempotency and service-role-only access, and keep the kill switch ready. Do not create lifecycle decisions or modify `ig_targets`.

## Prepared rollback owned by Dieumerci Ekwa Ntende

The historical target is `/Users/admin/phonefarm-worker-releases/8f6d16c-unfollow-lifecycle-resilience-v1`.

After an explicit rollback GO and a certified empty runtime gate, the canonical sequence is:

```text
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher pause --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl switch-release 8f6d16c-unfollow-lifecycle-resilience-v1 --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher prepare-auto-restart-startup-skip --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher resume --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl status --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher status --json
```

Before and after the sequence, verify the symlink target, exact Git SHA, PID/process count, duplicate status, queue, active requests/runs, device locks, effective release and BotApp Scheduler state. If BotApp is able to auto-resume the dispatcher, close or explicitly coordinate it before the pause boundary. A failed check stops the sequence; no opportunistic repair is part of this runbook.
